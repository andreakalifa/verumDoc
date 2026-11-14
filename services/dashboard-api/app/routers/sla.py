# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
F4.5 — SLA Enterprise: monitoraggio, report, alerting.
Riferimento piano: F4.5 — SLA Enterprise e Dashboard Avanzata

Endpoint:
  GET /api/dashboard/sla/report          — Report SLA mensile
  GET /api/dashboard/sla/uptime          — Uptime servizi
  GET /api/dashboard/sla/throughput      — Throughput versamenti
  GET /api/dashboard/admin/tenants-stats — Stats aggregate per tenant (admin)

R04 dati (piano §4.2, §6.4): letture sulla sessione di SOLA LETTURA
(``get_async_session_lettura``: replica se ``DATABASE_URL_LETTURA`` è
configurata, altrimenti primario in sola lettura) e conteggi dei PdA dai
contatori per ente (``libs.shared.db.contatori``), mai ``COUNT(*)`` su ``aip``.
Il percentile della durata di conservazione resta una query sul mese (indice
BRIN su ``aip.data_conservazione``): è una misura, non un conteggio.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.errori import errore_db
from libs.shared.db import get_async_session_lettura
from libs.shared.db.contatori import per_stato as contatori_per_stato
from libs.shared.db.contatori import serie_giornaliera, valori
from libs.shared.models import Aip, AipStato, HashchainBlock, Pdv, Tenant, TenantStato
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["SLA & Admin"],
                   # Revisione B5: token di utenti disattivati o enti sospesi → 401
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 # R03 f2 (REV-03): limite di richieste al minuto del piano
                                 Depends(limite_richieste)])

# Obiettivi di servizio (contratto enterprise). Sono OBIETTIVI, non misure:
# le misure stanno in «conformita» e sono null quando non esiste una fonte.
SLA_TARGETS = {
    "uptime_pct":           99.9,   # 99.9% uptime mensile
    "versamento_p95_ms":    2000,   # P95 latenza versamento < 2s
    "conservazione_p99_ms": 30_000, # P99 conservazione completa < 30s
    "disponibilita_pct":    99.95,  # 99.95% disponibilità API
}

# Indicatori percentuali/di latenza con il verso del confronto con l'obiettivo
_PIU_ALTO_E_MEGLIO = {"uptime_pct": True, "disponibilita_pct": True,
                      "versamento_p95_ms": False, "conservazione_p99_ms": False}

FONTE_CONSERVAZIONE = (
    "database: percentile 99 di (aip.data_conservazione − pdv.data_ricezione) "
    "sui pacchetti conservati nel periodo"
)
MOTIVO_SENZA_MONITORAGGIO = (
    "nessuna fonte di misura collegata: il monitoraggio di disponibilità e latenza "
    "non è attivo (VD-C-033)"
)



def _sla_require_admin(authorization: str | None) -> dict:
    """
    Guard: solo SUPER_ADMIN e RSC_CONSERVATORE per endpoint admin.
    Sprint 3A: usa verify_access_token (RS256) come dashboard.py.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, {"type": "AUTH-000", "title": "Token mancante"})
    from app.auth.jwt_handler import verify_access_token
    token = authorization.split(" ", 1)[1]
    payload = verify_access_token(token)
    if payload.get("ruolo") not in ("SUPER_ADMIN", "RSC_CONSERVATORE"):
        raise HTTPException(
            403, {"type": "AUTH-010", "title": "Solo SUPER_ADMIN e RSC_CONSERVATORE"})
    return payload


def _periodo(mese: str | None, now: datetime) -> tuple[datetime, datetime]:
    if mese:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mese):
            raise HTTPException(422, {"type": "SLA-422", "title": "mese nel formato AAAA-MM"})
        anno, m = mese.split("-")
        inizio = datetime(int(anno), int(m), 1, tzinfo=UTC)
    else:
        inizio = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fine = (inizio.replace(month=inizio.month + 1) if inizio.month < 12
            else inizio.replace(year=inizio.year + 1, month=1))
    return inizio, fine


def _misura(chiave: str, valore: float | None, fonte: str | None, motivo: str | None,
            campioni: int | None = None) -> dict:
    obiettivo = SLA_TARGETS.get(chiave)
    raggiunto = None
    if valore is not None and obiettivo is not None:
        raggiunto = valore >= obiettivo if _PIU_ALTO_E_MEGLIO[chiave] else valore <= obiettivo
    return {"valore": valore, "obiettivo": obiettivo, "raggiunto": raggiunto,
            "fonte": fonte if valore is not None else None,
            "campioni": campioni, "motivo": None if valore is not None else motivo}


@router.get("/sla/report", summary="Report SLA mensile")
async def sla_report(
    mese: str = Query(None, description="Mese nel formato YYYY-MM (default: mese corrente)"),
    authorization: str | None = Header(default=None),
) -> dict:
    """
    Report SLA del mese — CRIT-002 FIX: auth obbligatoria.
    G2 (VD-C-029): nessuna percentuale scritta nel codice. Ogni misura ha
    valore, fonte e motivo; senza una fonte il valore è null. Oggi l'unica
    misura con fonte reale è il tempo dal versamento alla conservazione (dal
    database); disponibilità e latenza delle API richiedono il monitoraggio,
    non collegato (VD-C-033).
    """
    _sla_require_admin(authorization)
    now = datetime.now(UTC)
    inizio, fine = _periodo(mese, now)

    try:
        async with get_async_session_lettura() as session:
            # PdA in CONSERVATO o IN_CATENA con data di conservazione nel mese
            # (contatore «aip.conservati» per giorno UTC: stessa definizione)
            aip_conservati = (await valori(
                session, prefisso="aip.conservati", da=inizio.date(), a=fine.date(),
            )).get("aip.conservati", 0)
            blocchi = (await session.execute(
                select(func.count(HashchainBlock.id)).where(
                    HashchainBlock.created_at >= inizio,
                    HashchainBlock.created_at < fine,
                )
            )).scalar_one()
            tenant_attivi = (await session.execute(
                select(func.count(Tenant.id)).where(Tenant.stato == TenantStato.ATTIVO)
            )).scalar_one()
            # Tempo dal versamento alla conservazione: misura reale dal DB
            durata_ms = func.extract(
                "epoch", Aip.data_conservazione - Pdv.data_ricezione,
            ) * 1000
            p99, campioni = (await session.execute(
                select(
                    func.percentile_cont(0.99).within_group(durata_ms),
                    func.count(Aip.id),
                )
                .join(Pdv, Pdv.id == Aip.pdv_id)
                .where(
                    Aip.data_conservazione >= inizio,
                    Aip.data_conservazione < fine,
                    Aip.stato == AipStato.CONSERVATO,
                )
            )).one()
    except SQLAlchemyError as exc:
        raise errore_db("report dei livelli di servizio", exc, codice="SLA-503") from None

    misure = {
        "uptime_pct": _misura("uptime_pct", None, None, MOTIVO_SENZA_MONITORAGGIO),
        "disponibilita_pct": _misura("disponibilita_pct", None, None, MOTIVO_SENZA_MONITORAGGIO),
        "versamento_p95_ms": _misura("versamento_p95_ms", None, None, MOTIVO_SENZA_MONITORAGGIO),
        "conservazione_p99_ms": _misura(
            "conservazione_p99_ms",
            round(float(p99), 1) if p99 is not None else None,
            FONTE_CONSERVAZIONE, "nessun pacchetto conservato nel periodo", campioni,
        ),
    }
    return {
        "periodo": {"inizio": inizio.isoformat(), "fine": fine.isoformat()},
        "sla_targets": SLA_TARGETS,
        "metriche": {
            "aip_conservati": aip_conservati,
            "blocchi_hashchain": blocchi,
            "tenant_attivi": tenant_attivi,
        },
        # Forma piatta per la pagina: valore misurato o null, mai un numero inventato.
        # «fonte» globale è null perché le misure hanno fonti diverse: vedi «fonti».
        "conformita": {
            **{k: m["valore"] for k, m in misure.items()},
            "fonte": None,
            "fonti": {k: m["fonte"] for k, m in misure.items()},
            "raggiunto": {k: m["raggiunto"] for k, m in misure.items()},
        },
        "misure": misure,
        "note": None,
        "generato_at": now.isoformat(),
    }


@router.get("/sla/throughput", summary="Throughput versamenti")
async def sla_throughput(
    giorni: int = Query(7, ge=1, le=90),
    authorization: str | None = Header(default=None),
) -> dict:
    """Throughput giornaliero — CRIT-002 FIX: auth obbligatoria.

    R04: dal contatore «aip.versati» per giorno UTC (ultimi ``giorni`` giorni,
    oggi compreso), mai ``COUNT(*)`` su ``aip``."""
    _sla_require_admin(authorization)
    oggi = datetime.now(UTC).date()
    try:
        async with get_async_session_lettura() as session:
            serie = await serie_giornaliera(session, "aip.versati",
                                            da=oggi - timedelta(days=giorni - 1),
                                            a=oggi + timedelta(days=1))
    except SQLAlchemyError as exc:
        raise errore_db("throughput dei versamenti", exc, codice="SLA-503") from None
    rows = sorted(serie.items())

    return {
        "periodo_giorni": giorni,
        "totale_aip": sum(r[1] for r in rows),
        "media_giornaliera": round(sum(r[1] for r in rows) / max(giorni, 1), 1),
        "per_giorno": [
            {"data": str(r[0])[:10], "aip": r[1]}
            for r in rows
        ],
    }


@router.get("/admin/tenants-stats", summary="Stats aggregate tenant (admin)")
async def tenants_stats(
    authorization: str | None = Header(default=None)
) -> dict:
    _sla_require_admin(authorization)
    """Statistiche aggregate per tutti i tenant — solo admin. R04: totale dei
    PdA dai contatori per ente (somma degli stati), mai ``COUNT(*)`` su ``aip``."""
    async with get_async_session_lettura() as session:
        # Distribuzione per piano
        piano_result = await session.execute(
            select(Tenant.piano, func.count(Tenant.id))
            .group_by(Tenant.piano)
        )
        per_piano = {row[0]: row[1] for row in piano_result.fetchall()}

        # Distribuzione per stato
        stato_result = await session.execute(
            select(Tenant.stato, func.count(Tenant.id))
            .group_by(Tenant.stato)
        )
        per_stato = {str(row[0].value): row[1] for row in stato_result.fetchall()}

        # Storage totale
        storage_result = await session.execute(
            select(func.sum(Tenant.storage_usato_gb))
        )
        storage_totale = float(storage_result.scalar_one() or 0)

        # Totale AIP: somma dei PdA per stato (contatori di tutti gli enti)
        totale_aip = sum(contatori_per_stato(
            await valori(session, prefisso="aip.stato."), "aip.stato.").values())

    return {
        "totale_tenant": sum(per_stato.values()),
        "per_piano": per_piano,
        "per_stato": per_stato,
        "storage_totale_gb": round(storage_totale, 2),
        "totale_aip": totale_aip,
        "timestamp": datetime.now(UTC).isoformat(),
    }
