# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica completa della catena delle impronte con esito persistito (G2,
VD-C-029: la pagina «Catena» non deve dire «integra» senza una verifica vera).

Cosa fa, in UNA transazione REPEATABLE READ (fotografia coerente di blocchi e
AIP) protetta da un lock consultivo (una sola verifica alla volta):
1. legge tutti i blocchi e le impronte degli AIP collegati;
2. esegue la verifica del servizio (``block_sealer.verifica_blocchi``:
   numerazione, collegamento al precedente, impronta di ogni blocco ricalcolata
   secondo il formato, radice di Merkle ricalcolata dalle impronte nel DB);
3. aggiunge due controlli: il blocco genesi è quello creato dalla migrazione
   001 (valori fissi) e ogni AIP elencato in un blocco punta a quel blocco
   (``aip.hashchain_block_id``);
4. registra l'esito in ``audit_global`` (append-only) con evento
   ``hashchain.verifica_completa``: è da lì che lo stato della catena legge
   «ultima verifica completa».

Limite dichiarato (lo riporta anche il messaggio): senza firma qualificata dei
blocchi, chi può scrivere sul DB e disattivare i trigger può ricostruire una
catena coerente; la verifica lo esclude solo per i blocchi FIRMATO.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.services.block_sealer import verifica_blocchi
from libs.shared.db import get_async_session

logger = structlog.get_logger(__name__)

EVENTO_VERIFICA = "hashchain.verifica_completa"
# Chiave del lock consultivo (transazionale): una verifica completa alla volta
_CHIAVE_LOCK = "verumdoc.hashchain.verifica_completa"

GENESI = {
    "numero_blocco": 0,
    "hash_blocco": "GENESIS_" + "0" * 64,
    "hash_blocco_precedente": "0" * 64,
    "merkle_root": "0" * 64,
}

ESITO_INTEGRA = "INTEGRA"
ESITO_COMPROMESSA = "COMPROMESSA"
ESITO_NESSUN_BLOCCO = "NESSUN_BLOCCO"

CONTROLLI = (
    "numerazione continua dei blocchi",
    "collegamento di ogni blocco all'impronta del precedente",
    "impronta di ogni blocco ricalcolata secondo il suo formato",
    "radice di Merkle ricalcolata dalle impronte degli AIP registrate nel DB",
    "blocco genesi uguale a quello della migrazione iniziale",
    "ogni AIP elencato in un blocco è collegato a quel blocco",
)


class VerificaGiaInCorso(Exception):
    """Un'altra verifica completa sta girando: non se ne avvia una seconda."""


@dataclass
class EsitoVerifica:
    verifica_id: str
    esito: str
    integra: bool | None
    messaggio: str
    blocchi_verificati: int
    ultimo_blocco_verificato: int | None
    blocchi_senza_firma_qualificata: int
    iniziata_il: str
    conclusa_il: str
    durata_ms: int
    avviata_da: str | None = None
    ruolo: str | None = None
    controlli: list[str] = field(default_factory=lambda: list(CONTROLLI))
    # g4: stato del ciclo di vita (verifica asincrona, verifica_asincrona.py)
    stato: str = "COMPLETATA"

    def come_dict(self) -> dict[str, Any]:
        return asdict(self)


def _uuid_o_none(valore: object) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


def controlla_genesi(blocchi: list[dict[str, Any]]) -> str | None:
    """Messaggio d'errore se il blocco 0 non è il genesi atteso, altrimenti None."""
    if not blocchi or blocchi[0]["numero_blocco"] != 0:
        return None  # lo segnala già verifica_blocchi
    g = blocchi[0]
    for campo, atteso in GENESI.items():
        if str(g[campo]) != str(atteso):
            return f"Blocco genesi alterato: {campo} diverso dal valore iniziale"
    if list(g.get("aip_ids") or []):
        return "Blocco genesi alterato: contiene AIP"
    return None


def controlla_collegamenti(
    blocchi: list[dict[str, Any]], blocco_di_aip: dict[str, str | None],
) -> str | None:
    """Ogni AIP elencato in un blocco deve puntare a quel blocco."""
    for b in blocchi:
        for aip in (str(x) for x in (b.get("aip_ids") or [])):
            if blocco_di_aip.get(aip) != b["id"]:
                return (f"Blocco #{b['numero_blocco']}: l'AIP {aip} non è collegato a "
                        "questo blocco")
    return None


def valuta(
    blocchi: list[dict[str, Any]], impronte: dict[str, str],
    blocco_di_aip: dict[str, str | None],
) -> tuple[str, bool | None, str, int]:
    """(esito, integra, messaggio, blocchi senza firma qualificata) — funzione pura."""
    if not blocchi:
        return ESITO_NESSUN_BLOCCO, None, "Nessun blocco nella catena: nulla da verificare", 0
    senza_firma = sum(1 for b in blocchi[1:] if b.get("stato_firma") != "FIRMATO")
    integra, messaggio = verifica_blocchi(blocchi, impronte)
    if integra:
        for controllo in (controlla_genesi(blocchi),
                          controlla_collegamenti(blocchi, blocco_di_aip)):
            if controllo:
                integra, messaggio = False, controllo
                break
    if not integra:
        return ESITO_COMPROMESSA, False, messaggio, senza_firma
    return ESITO_INTEGRA, True, messaggio, senza_firma


async def esegui_verifica_completa(
    *, avviata_da: str | None = None, ruolo: str | None = None,
    verifica_id: str | None = None, iniziata_il: datetime | None = None,
) -> EsitoVerifica:
    """Esegue la verifica sull'intera catena e ne registra l'esito (stato
    COMPLETATA). ``verifica_id``/``iniziata_il`` arrivano dall'avvio asincrono."""
    inizio = iniziata_il or datetime.now(UTC)
    t0 = time.monotonic()
    verifica_id = verifica_id or str(uuid.uuid4())
    async with get_async_session() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        preso = (await session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": _CHIAVE_LOCK},
        )).scalar_one()
        if not preso:
            raise VerificaGiaInCorso()

        blocchi = [dict(r) for r in (await session.execute(text(
            "SELECT id::text AS id, numero_blocco, hash_blocco, hash_blocco_precedente, "
            "merkle_root, aip_ids, created_at, versione_formato, stato_firma "
            "FROM public.hashchain_blocks ORDER BY numero_blocco ASC"
        ))).mappings().all()]
        righe_aip = (await session.execute(text(
            "SELECT id::text AS id, hash_sha256, hashchain_block_id::text AS blocco "
            "FROM public.aip WHERE hashchain_block_id IS NOT NULL"
        ))).mappings().all()
        impronte = {r["id"]: r["hash_sha256"] for r in righe_aip}
        blocco_di_aip = {r["id"]: r["blocco"] for r in righe_aip}

        esito, integra, messaggio, senza_firma = valuta(blocchi, impronte, blocco_di_aip)
        fine = datetime.now(UTC)
        risultato = EsitoVerifica(
            verifica_id=verifica_id, esito=esito, integra=integra, messaggio=messaggio,
            blocchi_verificati=len(blocchi),
            ultimo_blocco_verificato=blocchi[-1]["numero_blocco"] if blocchi else None,
            blocchi_senza_firma_qualificata=senza_firma,
            iniziata_il=inizio.isoformat(), conclusa_il=fine.isoformat(),
            durata_ms=int((time.monotonic() - t0) * 1000),
            avviata_da=avviata_da, ruolo=ruolo,
        )
        await session.execute(
            text(
                # Revisione 2 di R05 (S7): created_at = now() del DATABASE (inizio
                # della transazione), mai l'orologio dell'applicazione: la fine
                # della verifica resta nei dettagli (conclusa_il)
                "INSERT INTO public.audit_global (id, user_id, evento, dettagli, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:utente AS uuid), :evento, "
                "CAST(:dettagli AS jsonb), now())"
            ),
            {"id": verifica_id, "utente": _uuid_o_none(avviata_da), "evento": EVENTO_VERIFICA,
             "dettagli": json.dumps(risultato.come_dict())},
        )
    logger.info("verifica_completa_registrata", verifica_id=verifica_id, esito=esito,
                blocchi=len(blocchi))
    return risultato


async def ultima_verifica(session) -> dict[str, Any] | None:
    """Esito dell'ultima verifica completa CONCLUSA con un esito (stato
    COMPLETATA; le righe precedenti a g4 non hanno lo stato), o None se mai
    eseguita. Le verifiche FALLITA/INTERROTTA non cambiano lo stato mostrato."""
    riga = (await session.execute(
        text(
            "SELECT dettagli FROM public.audit_global WHERE evento = :evento "
            "AND COALESCE(dettagli->>'stato', 'COMPLETATA') = 'COMPLETATA' "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        ),
        {"evento": EVENTO_VERIFICA},
    )).first()
    return dict(riga[0]) if riga and isinstance(riga[0], dict) else None
