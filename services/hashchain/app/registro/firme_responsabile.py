# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Archiviazione nel WORM delle firme PERSONALI del responsabile (R16, VD-C-166).

Servizio: hashchain (l'unico che scrive sotto ``registro/`` e il solo gruppo con UPDATE
delle colonne dell'archiviazione di ``firme_responsabile``, migrazione 20261110_057).

Un documento FIRMATO (busta registrata da dashboard-api dopo la firma con l'OTP della
persona) si RIVERIFICA prima di archiviarlo, indipendentemente dall'API che l'ha scritto
(§31 R05: chi esegue riverifica il fatto che lo autorizza):

  1. impronta della busta uguale a ``busta_sha256``;
  2. busta CAdES con UN firmatario e con il contenuto ESATTAMENTE uguale al documento;
  3. credenziale registrata ATTIVA, della persona che ha firmato, con il certificato
     atteso uguale a quello dichiarato nella riga;
  4. verifica crittografica del servizio di firma (``/verifica``, radici del prestatore):
     esito «valida» e certificato del firmatario uguale a quello atteso.

Se un controllo non torna il documento va RESPINTO (stato finale, allarme
``FirmeResponsabileRespinte``; la persona firma di nuovo una richiesta nuova). Se il
servizio di firma non risponde, si ritenta con attesa crescente. Poi la busta va nel WORM
con retention permanente alla chiave ``registro/documenti/responsabile/<tipo>/<impronta>.
<sha_busta[:24]>.p7m`` (idempotente: dopo un crash fra WORM e UPDATE la ripresa ritrova
l'oggetto identico, §31 R13a revisione (1)) e la riga diventa ARCHIVIATO, nella stessa
transazione della verifica del lease del registro (fencing).
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import structlog
from asn1crypto import cms
from sqlalchemy import text

from app.registro import archivio, archivio_worm
from libs.shared.db import get_async_session
from libs.shared.firme_responsabile import chiave_worm

logger = structlog.get_logger(__name__)

LOTTO = 20
ATTESA_MASSIMA = timedelta(hours=6)

_SELEZIONE = text(
    "SELECT f.id, f.tipo, f.contenuto, f.impronta_sha256, f.busta, f.busta_sha256, "
    " f.certificato_sha256, f.firmato_da, f.tentativi, c.user_id AS cred_utente, "
    " c.certificato_sha256 AS cred_certificato, c.attiva AS cred_attiva "
    "FROM public.firme_responsabile f "
    "LEFT JOIN public.credenziali_firma_personali c ON c.id = f.credenziale_id "
    "WHERE f.stato = 'FIRMATO' AND (f.prossimo_tentativo_il IS NULL "
    " OR f.prossimo_tentativo_il <= :adesso) "
    "ORDER BY f.prossimo_tentativo_il NULLS FIRST, f.created_at LIMIT :n")


def controlli_locali(riga: Mapping[str, Any]) -> list[str]:
    """Controlli 1–3 (senza rete): motivi del rifiuto, vuoto se tutto torna."""
    motivi: list[str] = []
    busta = bytes(riga["busta"] or b"")
    if hashlib.sha256(busta).hexdigest() != str(riga["busta_sha256"] or "").strip():
        motivi.append("impronta della busta diversa da quella registrata")
    try:
        sd = cms.ContentInfo.load(busta)["content"]
        incluso = sd["encap_content_info"]["content"].native
        firmatari = len(sd["signer_infos"])
    except Exception:  # noqa: BLE001 — busta illeggibile
        incluso, firmatari = None, 0
    if incluso != bytes(riga["contenuto"]) or firmatari != 1:
        motivi.append("la busta non contiene esattamente il documento o ha più firmatari")
    certificato = str(riga["certificato_sha256"] or "").strip()
    if not riga["cred_attiva"] or str(riga["cred_utente"]) != str(riga["firmato_da"]) \
            or str(riga["cred_certificato"] or "").strip() != certificato:
        motivi.append("credenziale non attiva, di un'altra persona o con un altro certificato")
    return motivi


async def _aggiorna(detentore: str, adesso: datetime, sql: str, valori: dict[str, Any]) -> int:
    async with get_async_session() as session:
        await archivio.stato_con_lease(session, detentore, adesso)
        return int((await session.execute(text(sql), valori)).rowcount or 0)


async def _respingi(riga: Mapping[str, Any], motivi: list[str], *, detentore: str,
                    adesso: datetime) -> None:
    await _aggiorna(detentore, adesso,
                    "UPDATE public.firme_responsabile SET stato = 'RESPINTO', motivo = :m, "
                    "updated_at = :a WHERE id = :id AND stato = 'FIRMATO'",
                    {"m": "; ".join(motivi)[:500], "a": adesso, "id": riga["id"]})
    logger.error("firma_responsabile_respinta", id=str(riga["id"]), tipo=riga["tipo"],
                 motivi=motivi[:3], azione="far firmare di nuovo il documento e indagare")


async def _rinvia(riga: Mapping[str, Any], motivo: str, *, detentore: str,
                  adesso: datetime) -> None:
    attesa = min(timedelta(minutes=2 ** min(int(riga["tentativi"]), 8)), ATTESA_MASSIMA)
    await _aggiorna(detentore, adesso,
                    "UPDATE public.firme_responsabile SET motivo = :m, tentativi = tentativi + 1, "
                    "prossimo_tentativo_il = :p, updated_at = :a "
                    "WHERE id = :id AND stato = 'FIRMATO'",
                    {"m": motivo[:500], "p": adesso + attesa, "a": adesso, "id": riga["id"]})


async def archivia(servizio: Any, *, detentore: str, adesso: datetime,
                   limite: int = LOTTO) -> dict[str, int]:
    """Un giro: riverifica e archiviazione dei documenti FIRMATO (vedi modulo)."""
    conteggi = dict.fromkeys(("archiviati", "respinti", "rinviati"), 0)
    async with get_async_session() as session:
        righe = (await session.execute(_SELEZIONE, {"adesso": adesso, "n": limite})
                 ).mappings().all()
    for riga in righe:
        motivi = controlli_locali(riga)
        if not motivi:
            verifica = await _verifica(servizio, bytes(riga["busta"]))
            if verifica is None:
                await _rinvia(riga, "servizio di firma non disponibile per la riverifica",
                              detentore=detentore, adesso=adesso)
                conteggi["rinviati"] += 1
                continue
            if verifica.get("esito") != "valida":
                motivi.append(f"verifica della firma: {verifica.get('esito')} "
                              f"({'; '.join(map(str, verifica.get('motivi') or []))[:200]})")
            if verifica.get("certificato_sha256") != str(riga["certificato_sha256"]).strip():
                motivi.append("certificato del firmatario diverso da quello della credenziale")
        if motivi:
            await _respingi(riga, motivi, detentore=detentore, adesso=adesso)
            conteggi["respinti"] += 1
            continue
        busta = bytes(riga["busta"])
        chiave = chiave_worm(str(riga["tipo"]), str(riga["impronta_sha256"]).strip(), busta)
        try:
            _, versione = await archivio_worm.scrivi(chiave, busta, "application/pkcs7-mime",
                                                     adesso)
        except archivio_worm.ContenutoWormDiverso:
            await _respingi(riga, ["chiave WORM già occupata da un contenuto diverso"],
                            detentore=detentore, adesso=adesso)
            conteggi["respinti"] += 1
            continue
        await _aggiorna(detentore, adesso,
                        "UPDATE public.firme_responsabile SET stato = 'ARCHIVIATO', "
                        "busta_chiave = :k, busta_version_id = :v, archiviato_il = :a, "
                        "motivo = NULL, prossimo_tentativo_il = NULL, updated_at = :a "
                        "WHERE id = :id AND stato = 'FIRMATO'",
                        {"k": chiave, "v": versione, "a": adesso, "id": riga["id"]})
        conteggi["archiviati"] += 1
        logger.info("firma_responsabile_archiviata", id=str(riga["id"]), tipo=riga["tipo"])
    return conteggi


async def _verifica(servizio: Any, busta: bytes) -> dict[str, Any] | None:
    """Verifica del servizio di firma; None se non disponibile (si ritenta)."""
    verifica = getattr(servizio, "verifica", None)
    if verifica is None:
        return None
    try:
        return await verifica(busta)
    except Exception as exc:  # noqa: BLE001 — servizio non raggiungibile: si ritenta
        logger.warning("firma_responsabile_verifica_non_disponibile", errore=type(exc).__name__)
        return None


async def conta_respinte() -> int:
    """Metrica ``verumdoc_firme_responsabile_respinte`` (allarme)."""
    async with get_async_session() as session:
        return int((await session.execute(text(
            "SELECT count(*) FROM public.firme_responsabile WHERE stato = 'RESPINTO'"))
        ).scalar_one())


__all__ = ["archivia", "conta_respinte", "controlli_locali"]
