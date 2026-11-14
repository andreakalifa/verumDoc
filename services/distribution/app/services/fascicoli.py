# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/services/fascicoli.py — elenco a cursore e chiusura dei
fascicoli (aggregazioni documentali).

Riferimenti: VD-C-087 (R05, agente r05-ciclo-vita); Allegato 5 alle Linee guida
AgID (aggregazioni documentali: DataApertura, DataChiusura); REGOLE §8.15 (il
dato derivabile si calcola: la data di chiusura predefinita è oggi, ora
italiana), §12.5, §14.2-§14.4, §15.1-§15.3.

La data di chiusura SERVE ALLO SCARTO: per un PdA in un fascicolo il termine di
conservazione decorre dalla chiusura (servizio ciclo-vita, ``app.domain.scarto``:
fascicolo aperto = PdA bloccato; chiuso = termine dalla data di chiusura).
Un fascicolo chiuso non accetta altri PdA (stato diverso da APERTO) e non si
riapre da qui: una riapertura cambierebbe i termini di scarto già calcolati
(decisione da confermare, rapporto R05).
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.shared.errori import ErroreApi
from libs.shared.security.audit import registra_audit

FUSO = ZoneInfo("Europe/Rome")
STATO_APERTO = "APERTO"
STATO_CHIUSO = "CHIUSO"
ELENCO = "fascicoli"


def oggi() -> date:
    return datetime.now(FUSO).date()


def codifica_cursore(quando: datetime, ultimo: Any) -> str:
    dati = {"e": ELENCO, "t": quando.isoformat(), "i": str(ultimo)}
    return base64.urlsafe_b64encode(json.dumps(dati, separators=(",", ":")).encode()
                                    ).decode().rstrip("=")


def decodifica_cursore(cursore: str) -> tuple[datetime, uuid.UUID]:
    try:
        dati = json.loads(base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)))
        if dati.get("e") != ELENCO:
            raise ValueError("cursore di un altro elenco")
        quando = datetime.fromisoformat(dati["t"])
        if quando.tzinfo is None:
            raise ValueError("data senza fuso")
        return quando, uuid.UUID(str(dati["i"]))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ErroreApi("FSC-400-CURSORE", "Cursore non valido per l'elenco dei fascicoli"
                        ) from None


async def elenca(session: AsyncSession, tenant_id: uuid.UUID, *, stato: str | None,
                 anno: int | None, tipo: str | None, cursore: str | None,
                 limite: int) -> dict[str, Any]:
    """Fascicoli dell'ente dal più recente (indice ``idx_fascicoli_tenant_cursore``),
    senza OFFSET né COUNT dell'elenco; ``num_aip`` contato per i soli fascicoli
    della pagina (una query raggruppata)."""
    condizioni = ["f.tenant_id = :t"]
    parametri: dict[str, Any] = {"t": tenant_id, "lim": limite + 1}
    if stato:
        condizioni.append("f.stato = :stato")
        parametri["stato"] = stato
    if anno is not None:
        condizioni.append("f.anno = :anno")
        parametri["anno"] = anno
    if tipo:
        condizioni.append("f.tipo = :tipo")
        parametri["tipo"] = tipo
    if cursore:
        quando, ultimo = decodifica_cursore(cursore)
        condizioni.append("(f.created_at, f.id) < (:quando, :ultimo)")
        parametri.update(quando=quando, ultimo=ultimo)
    righe = [dict(r) for r in (await session.execute(text(
        "SELECT f.id, f.codice, f.titolo, f.descrizione, f.tipo, f.anno, f.stato, "  # noqa: S608 — condizioni costanti
        "f.data_apertura, f.data_chiusura, f.created_at FROM public.fascicoli f "
        f"WHERE {' AND '.join(condizioni)} ORDER BY f.created_at DESC, f.id DESC LIMIT :lim"),
        parametri)).mappings().all()]
    altri = len(righe) > limite
    righe = righe[:limite]
    conteggi: dict[Any, int] = {}
    if righe:
        conteggi = {r[0]: int(r[1]) for r in (await session.execute(text(
            "SELECT fascicolo_id, count(*) FROM public.fascicoli_aip "
            "WHERE fascicolo_id = ANY(:ids) GROUP BY fascicolo_id"),
            {"ids": [r["id"] for r in righe]})).all()}
    prossimo = codifica_cursore(righe[-1]["created_at"], righe[-1]["id"]) if altri and righe \
        else None
    return {"items": [{**r, "id": str(r["id"]), "num_aip": conteggi.get(r["id"], 0)}
                      for r in righe],
            "limit": limite, "cursore": prossimo, "altri": altri}


def motivo_blocco_chiusura(stato: str, data_apertura: date, data_chiusura: date,
                           adesso: date) -> tuple[str, str] | None:
    """Regola pura della chiusura: solo un fascicolo APERTO; la data di chiusura
    fra l'apertura e oggi."""
    if stato != STATO_APERTO:
        return ("FSC-409", f"Il fascicolo è {stato}: si chiude solo un fascicolo aperto")
    if data_chiusura < data_apertura:
        return ("FSC-422-CHIUSURA", "La data di chiusura precede l'apertura del fascicolo")
    if data_chiusura > adesso:
        return ("FSC-422-CHIUSURA", "La data di chiusura non può essere futura")
    return None


async def chiudi(session: AsyncSession, *, tenant_id: uuid.UUID, fascicolo_id: uuid.UUID,
                 data_chiusura: date | None, motivo: str, utente_id: str | None,
                 stato_atteso: str | None) -> dict[str, Any]:
    """Chiusura sotto lock della riga (REGOLE §12.6) con audit nella transazione."""
    riga = (await session.execute(text(
        "SELECT id, stato, data_apertura FROM public.fascicoli WHERE id = :id "
        "AND tenant_id = :t FOR UPDATE"), {"id": fascicolo_id, "t": tenant_id})).mappings().first()
    if riga is None:
        raise ErroreApi("FSC-404", "Fascicolo non trovato")
    if stato_atteso is not None and riga["stato"] != stato_atteso:
        raise ErroreApi("FSC-409", f"Lo stato del fascicolo è {riga['stato']}, non {stato_atteso}",
                        dettagli={"stato_attuale": riga["stato"]})
    chiusura = data_chiusura or oggi()
    blocco = motivo_blocco_chiusura(riga["stato"], riga["data_apertura"], chiusura, oggi())
    if blocco is not None:
        raise ErroreApi(*blocco)
    await session.execute(text(
        "UPDATE public.fascicoli SET stato = :chiuso, data_chiusura = :data, updated_at = now() "
        "WHERE id = :id"), {"id": fascicolo_id, "chiuso": STATO_CHIUSO, "data": chiusura})
    await registra_audit(session, evento="fascicolo.chiuso", tenant_id=str(tenant_id),
                         user_id=utente_id,
                         dettagli={"fascicolo_id": str(fascicolo_id),
                                   "data_chiusura": chiusura.isoformat(), "motivo": motivo})
    return {"id": str(fascicolo_id), "stato": STATO_CHIUSO, "data_chiusura": chiusura.isoformat()}


__all__ = ["STATO_APERTO", "STATO_CHIUSO", "chiudi", "elenca", "motivo_blocco_chiusura", "oggi"]
