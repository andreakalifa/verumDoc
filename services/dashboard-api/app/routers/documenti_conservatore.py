# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/routers/documenti_conservatore.py — manuale e piano
firmati (R13a, VD-C-013 riserva, VD-C-068).

  POST /api/dashboard/documenti-conservatore/{tipo}/pubblica   tipo = manuale | piano
  GET  /api/dashboard/documenti-conservatore                   versioni pubblicate e firma
  GET  /api/dashboard/documenti-conservatore/{id}/busta        .p7m firmato (CAdES)

«Pubblica» genera il documento dai dati reali (stessa funzione della pagina),
ne fissa il contenuto canonico (JSON senza la data di generazione: lo stesso
contenuto pubblicato due volte è la stessa versione) e lo mette fra i
documenti da sigillare (``firme_documenti``, stato DA_FIRMARE): il sigillo
qualificato del conservatore lo appone la hashchain, che conserva la busta nel
WORM (``registro/documenti/<tipo>/<impronta>.p7m``). Solo SUPER_ADMIN e
RSC_CONSERVATORE con token utente; ogni pubblicazione ha la sua voce d'audit
nella stessa transazione.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dipendenze import utente_corrente
from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.errori import errore_db
from libs.catena.canonico import json_canonico
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi
from libs.shared.security.audit import registra_audit
from libs.shared.security.revoca import dipendenza_non_revocato

router = APIRouter(prefix="/api/dashboard/documenti-conservatore",
                   tags=["Documenti del conservatore"], responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 Depends(limite_richieste)])

TIPI = {"manuale": "MANUALE", "piano": "PIANO"}
NOMI = {"MANUALE": "manuale_conservazione.json", "PIANO": "piano_cessazione.json"}


async def responsabile(user: dict = Depends(utente_corrente)) -> dict:
    if user.get("ruolo") not in ("SUPER_ADMIN", "RSC_CONSERVATORE") \
            or user.get("auth_method") == "api_key":
        raise ErroreApi("AUTH-010", "Manuale e piano li pubblica il responsabile del servizio "
                                    "di conservazione")
    return user


async def _documento(tipo: str) -> dict[str, Any]:
    from app.services.manuale import DatiConservatore

    adesso = datetime.now(UTC)
    if tipo == "MANUALE":
        from app.services.manuale import genera_manuale, leggi_classi_attive

        classi, errore = await leggi_classi_attive()
        return genera_manuale(DatiConservatore(), adesso=adesso, classi=classi,
                              errore_classi=errore)
    from app.services.piano_cessazione import genera_piano_cessazione, leggi_consistenza

    consistenza, errore = await leggi_consistenza()
    return genera_piano_cessazione(DatiConservatore(), adesso=adesso, consistenza=consistenza,
                                   errore_consistenza=errore)


def contenuto_canonico(tipo: str, documento: dict[str, Any]) -> bytes:
    """Contenuto firmato: JSON canonico senza «meta» (data di generazione e stato)."""
    return json_canonico({"tipo": tipo, "formato": "verumdoc-documento-conservatore-1",
                          "documento": {k: v for k, v in documento.items() if k != "meta"}})


def _vista(r: Any) -> dict[str, Any]:
    return {"id": str(r["id"]), "tipo": r["tipo"], "impronta_sha256": r["impronta_sha256"],
            "stato": r["stato"], "motivo": r["motivo"], "livello": r["livello"],
            "pubblicato_il": r["created_at"].isoformat(),
            "firmato_il": r["firmato_il"].isoformat() if r["firmato_il"] else None,
            "certificato_soggetto": r["certificato_soggetto"],
            "certificato_scadenza": (r["certificato_scadenza"].isoformat()
                                     if r["certificato_scadenza"] else None),
            "rinnovi": r["rinnovi"], "busta_disponibile": bool(r["ha_busta"])}


_SELECT = ("SELECT id, tipo, impronta_sha256, stato, motivo, livello, created_at, firmato_il, "
           "certificato_soggetto, certificato_scadenza, rinnovi, busta IS NOT NULL AS ha_busta "
           "FROM public.firme_documenti ")


@router.post("/{tipo}/pubblica", summary="Pubblica una versione da sigillare")
async def pubblica(tipo: Literal["manuale", "piano"],
                   user: dict = Depends(responsabile)) -> dict[str, Any]:
    codice = TIPI[tipo]
    contenuto = contenuto_canonico(codice, await _documento(codice))
    impronta = hashlib.sha256(contenuto).hexdigest()
    try:
        async with get_async_session() as session:
            nuova = (await session.execute(text(
                "INSERT INTO public.firme_documenti (tipo, nome, contenuto, impronta_sha256) "
                "VALUES (:t, :n, :c, :h) ON CONFLICT (tipo, impronta_sha256) "
                "WHERE aip_id IS NULL DO NOTHING RETURNING id"),
                {"t": codice, "n": NOMI[codice], "c": contenuto, "h": impronta})).first()
            if nuova is not None:
                await registra_audit(session, evento="conservatore.documento_pubblicato",
                                     user_id=user.get("user_id"),
                                     dettagli={"tipo": codice, "impronta_sha256": impronta,
                                               "id": str(nuova[0])})
            riga = (await session.execute(text(
                _SELECT + "WHERE tipo = :t AND impronta_sha256 = :h AND aip_id IS NULL"),
                {"t": codice, "h": impronta})).mappings().one()
    except SQLAlchemyError as exc:
        raise errore_db("pubblicazione del documento del conservatore", exc,
                        codice="GOV-503") from None
    return {**_vista(riga), "nuova_versione": nuova is not None}


@router.get("", summary="Versioni pubblicate di manuale e piano e stato del sigillo")
async def elenco(user: dict = Depends(responsabile)) -> dict[str, Any]:
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                _SELECT + "WHERE tipo IN ('MANUALE', 'PIANO') ORDER BY created_at DESC "
                          "LIMIT 100"))).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_db("elenco dei documenti del conservatore", exc, codice="GOV-503") from None
    return {"items": [_vista(r) for r in righe]}


@router.get("/{documento_id}/busta", summary="Busta CAdES (.p7m) del documento sigillato")
async def busta(documento_id: str, user: dict = Depends(responsabile)) -> Response:
    try:
        ident = uuid.UUID(documento_id)
    except ValueError:
        raise ErroreApi("GOV-404") from None
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                "SELECT tipo, nome, busta, stato FROM public.firme_documenti WHERE id = :id "
                "AND tipo IN ('MANUALE', 'PIANO')"), {"id": ident})).mappings().first()
    except SQLAlchemyError as exc:
        raise errore_db("busta del documento del conservatore", exc, codice="GOV-503") from None
    if riga is None or riga["stato"] != "FIRMATO" or riga["busta"] is None:
        raise ErroreApi("GOV-404")
    return Response(bytes(riga["busta"]), media_type="application/pkcs7-mime",
                    headers={"Content-Disposition":
                             f'attachment; filename="{riga["nome"]}.p7m"',
                             "Cache-Control": "no-store"})


__all__ = ["contenuto_canonico", "router"]
