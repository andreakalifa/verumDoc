# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/routers/documentazione_servizio.py — documentazione di conformità
generata dalla configurazione (R15, VD-C-177).

  GET  /api/dashboard/documentazione-servizio                      documenti, ultima versione,
                                                                    dati da compilare, richieste
  POST /api/dashboard/documentazione-servizio/generazioni          genera (asincrona, 202)
  GET  /api/dashboard/documentazione-servizio/generazioni/{id}     stato della richiesta
  GET  /api/dashboard/documentazione-servizio/versioni?tipo=…      storico delle versioni
  GET  /api/dashboard/documentazione-servizio/versioni/{id}        dettaglio (sezioni, fonti,
                                                                    differenze, da compilare)
  GET  /api/dashboard/documentazione-servizio/versioni/{id}/markdown | /html   scaricamento

Permesso MAI DELEGABILE ``documentazione:genera`` (solo SUPER_ADMIN e RSC_CONSERVATORE con
ruolo fisso e token utente: niente chiave API, niente ruolo personalizzato), negato anche dal
vincolo del database sui ruoli personalizzati (migrazione ``20261111_058``).

Generazione asincrona: la richiesta nasce IN_CORSO (una sola alla volta, indice unico nel
database) con voce d'audit nella stessa transazione e scadenza; il lavoro gira dopo la
risposta (``BackgroundTasks``) nell'ambito del conservatore: legge la configurazione in una
transazione READ ONLY (``libs/conformita/database.py``), compone i documenti con la
fotografia del repository del rilascio (``libs/conformita/dati/fonti_repository.json``) e le
variabili ``CONSERVATORE_*``, inserisce SOLO le versioni il cui contenuto è cambiato (stesso
contenuto = nessuna versione nuova) e chiude la richiesta COMPLETATA con l'audit, tutto in
una transazione. Un errore la chiude in ERRORE (metrica e allarme
``DocumentazioneNonGenerata``); una richiesta rimasta IN_CORSO oltre la scadenza (processo
terminato) si dichiara interrotta alla richiesta successiva, che riparte. Le versioni sono
immutabili nel database (trigger) e la hashchain le archivia nel WORM con l'impronta nella
chiave (``registro/documentazione/<tipo>/<impronta>.md|.html``, scrittura idempotente).
"""
# ruff: noqa: S608 — SQL composto solo con le colonne costanti di questo modulo (_COLONNE_VERSIONE, _GENERAZIONE e la scelta chiusa del formato)
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dipendenze import utente_corrente
from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.errori import errore_db
from libs.shared.db import get_async_session
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi
from libs.shared.security.audit import registra_audit
from libs.shared.security.permessi import ha_permesso_non_delegabile
from libs.shared.security.revoca import dipendenza_non_revocato
from libs.verumdoc_rules.permessi import DOCUMENTAZIONE_GENERA

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/dashboard/documentazione-servizio",
                   tags=["Documentazione del servizio"], responses=RISPOSTE_ERRORE,
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 Depends(limite_richieste)])

TIPI = ("MANUALE_CONSERVAZIONE", "PIANO_SICUREZZA", "GUIDA_FUNZIONALITA", "CATALOGO_ROUTE")
TipoDocumento = Literal["MANUALE_CONSERVAZIONE", "PIANO_SICUREZZA", "GUIDA_FUNZIONALITA",
                        "CATALOGO_ROUTE"]
#: oltre questa durata una richiesta IN_CORSO si considera interrotta (processo terminato)
DURATA_MASSIMA = timedelta(minutes=10)

#: registrata sul registro del servizio in app.main (GET /metrics)
GENERAZIONI = Counter("verumdoc_documentazione_generazioni_totali",
                      "Generazioni della documentazione del servizio per esito", ["esito"],
                      registry=None)


async def conservatore(user: dict = Depends(utente_corrente)) -> dict:
    """Solo ruoli fissi del conservatore con token utente: permesso mai delegabile."""
    if user.get("auth_method") == "api_key" \
            or not ha_permesso_non_delegabile(user, DOCUMENTAZIONE_GENERA):
        raise ErroreApi("AUTH-010", "La documentazione del servizio la genera il responsabile "
                                    "del servizio di conservazione")
    return user


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _uuid(valore: str) -> uuid.UUID:
    try:
        return uuid.UUID(valore)
    except ValueError:
        raise ErroreApi("GOV-404") from None


def _utente(user: dict) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(user.get("user_id")))
    except ValueError:
        return None


# ── viste ──────────────────────────────────────────────────────────────

_COLONNE_VERSIONE = (
    "v.id, v.tipo, v.versione, v.precedente_id, v.titolo, v.nome_file, v.impronta_contenuto, "
    "v.impronta_sha256, v.impronta_html, v.sezioni, v.differenze, v.da_compilare, "
    "v.da_verificare, v.generata_il, v.created_at, v.worm_chiave_md, v.worm_chiave_html, "
    "v.archiviata_il, v.generazione_id")


def _vista_versione(r: Any, dettaglio: bool = False) -> dict[str, Any]:
    da_compilare = _json(r["da_compilare"]) or []
    vista: dict[str, Any] = {
        "id": str(r["id"]), "tipo": r["tipo"], "versione": r["versione"],
        "titolo": r["titolo"], "generata_il": _iso(r["generata_il"]),
        "impronta_contenuto": r["impronta_contenuto"].strip(),
        "impronta_sha256": r["impronta_sha256"].strip(),
        "impronta_html": r["impronta_html"].strip(),
        "differenze": _json(r["differenze"]),
        "sezioni_da_compilare": len(da_compilare),
        "campi_da_compilare": len({c["campo"] for s in da_compilare for c in s["campi"]}),
        "da_verificare": len(_json(r["da_verificare"]) or []),
        "archivio": {"archiviata": r["archiviata_il"] is not None,
                     "archiviata_il": _iso(r["archiviata_il"]),
                     "chiave_markdown": r["worm_chiave_md"],
                     "chiave_html": r["worm_chiave_html"]},
        # punto di aggancio della firma del responsabile (R16): si firma l'HTML stampabile
        "firma": {"stato": "non firmato", "da_firmare": {
            "formato": "text/html", "impronta_sha256": r["impronta_html"].strip()}},
    }
    if dettaglio:
        vista.update({"sezioni": _json(r["sezioni"]), "da_compilare": da_compilare,
                      "da_verificare_elenco": _json(r["da_verificare"]),
                      "fonti": _json(r.get("fonti")) if "fonti" in r else None,
                      "precedente_id": str(r["precedente_id"]) if r["precedente_id"] else None,
                      "generazione_id": str(r["generazione_id"])})
    return vista


def _vista_generazione(r: Any) -> dict[str, Any]:
    return {"id": str(r["id"]), "stato": r["stato"], "richiesta_il": _iso(r["richiesta_il"]),
            "scade_il": _iso(r["scade_il"]), "conclusa_il": _iso(r["conclusa_il"]),
            "esito": _json(r["esito"]), "errore": r["errore"]}


_GENERAZIONE = ("SELECT id, stato, richiesta_il, scade_il, conclusa_il, esito, errore "
                "FROM public.documentazione_generazioni ")


# ── generazione ───────────────────────────────────────────────────────

async def _chiudi_interrotte(session: Any) -> int:
    r = await session.execute(text(
        "UPDATE public.documentazione_generazioni SET stato = 'ERRORE', "
        "errore = 'interrotta: scadenza superata senza esito (processo terminato)', "
        "esito = '{\"interrotta\": true}'::jsonb "
        "WHERE stato = 'IN_CORSO' AND scade_il < now()"))
    return int(r.rowcount or 0)


@router.post("/generazioni", status_code=202,
             summary="Genera la documentazione del servizio (asincrona)")
async def richiedi_generazione(fondo: BackgroundTasks,
                               user: dict = Depends(conservatore)) -> JSONResponse:
    autore = _utente(user)
    try:
        async with get_async_session() as session:
            interrotte = await _chiudi_interrotte(session)
            riga = (await session.execute(text(
                "INSERT INTO public.documentazione_generazioni (richiesta_da, scade_il) "
                "VALUES (:u, now() + make_interval(secs => :s)) ON CONFLICT (stato) "
                "WHERE stato = 'IN_CORSO' DO NOTHING RETURNING id"),
                {"u": autore, "s": int(DURATA_MASSIMA.total_seconds())})).first()
            if riga is not None:
                await registra_audit(session, evento="documentazione.generazione_richiesta",
                                     user_id=str(autore) if autore else None,
                                     dettagli={"generazione_id": str(riga[0])})
            corrente = (await session.execute(text(
                _GENERAZIONE + "WHERE stato = 'IN_CORSO'"))).mappings().one()
    except SQLAlchemyError as exc:
        raise errore_db("richiesta di generazione della documentazione", exc) from None
    for _ in range(interrotte):
        GENERAZIONI.labels("interrotta").inc()
    if riga is not None:
        fondo.add_task(esegui_generazione, corrente["id"], autore)
    return JSONResponse({**_vista_generazione(corrente), "nuova": riga is not None},
                        status_code=202)


async def _precedenti(session: Any) -> dict[str, dict[str, Any]]:
    righe = (await session.execute(text(
        "SELECT DISTINCT ON (tipo) id, tipo, versione, impronta_contenuto, sezioni "
        "FROM public.documentazione_versioni ORDER BY tipo, versione DESC"))).mappings().all()
    return {r["tipo"]: {"id": r["id"], "versione": r["versione"],
                        "impronta_contenuto": r["impronta_contenuto"].strip(),
                        "sezioni": _json(r["sezioni"])} for r in righe}


async def esegui_generazione(generazione_id: uuid.UUID, autore: uuid.UUID | None) -> None:
    """Lavoro della richiesta (dopo la risposta): esito COMPLETATA o ERRORE, mai sospeso."""
    import os

    from libs.conformita import database, fotografia
    from libs.conformita.documenti import Fatti
    from libs.conformita.generazione import genera
    from libs.shared.db.ambito import ambito_conservatore

    try:
        with ambito_conservatore("generazione della documentazione del servizio richiesta "
                                 "dal responsabile (R15)"):
            async with get_async_session() as session:
                fatti_db = await database.raccogli(session)
            fatti = Fatti(fotografia.leggi_fotografia(), fatti_db, dict(os.environ))
            async with get_async_session() as session:
                precedenti = await _precedenti(session)
                adesso = datetime.now(UTC).replace(microsecond=0)
                generati = genera(fatti, generata_il=adesso.isoformat(), precedenti=precedenti)
                nuove, invariate = [], []
                for tipo, g in generati.items():
                    if g.versione is None:
                        invariate.append(tipo)
                        continue
                    ident = uuid.uuid4()
                    await session.execute(text(
                        "INSERT INTO public.documentazione_versioni (id, tipo, versione, "
                        "precedente_id, generazione_id, titolo, nome_file, markdown, html, "
                        "impronta_contenuto, impronta_sha256, impronta_html, sezioni, "
                        "differenze, fonti, da_compilare, da_verificare, generata_il, creato_da) "
                        "VALUES (:id, :tipo, :v, :prec, :gen, :titolo, :nome, :md, :html, :ic, "
                        ":ism, :ih, CAST(:sez AS jsonb), CAST(:diff AS jsonb), "
                        "CAST(:fonti AS jsonb), CAST(:dc AS jsonb), CAST(:dv AS jsonb), :il, :u)"),
                        {"id": ident, "tipo": tipo, "v": g.versione,
                         "prec": (precedenti.get(tipo) or {}).get("id"), "gen": generazione_id,
                         "titolo": g.titolo, "nome": g.nome_file,
                         "md": g.markdown.encode("utf-8"), "html": g.html.encode("utf-8"),
                         "ic": g.impronta_contenuto, "ism": g.impronta_sha256,
                         "ih": g.impronta_html, "sez": json.dumps(g.sezioni),
                         "diff": json.dumps(g.differenze), "fonti": json.dumps(g.fonti),
                         "dc": json.dumps(g.da_compilare), "dv": json.dumps(g.da_verificare),
                         "il": adesso, "u": autore})
                    nuove.append({"tipo": tipo, "versione": g.versione, "id": str(ident),
                                  "impronta_sha256": g.impronta_sha256})
                esito = {"nuove": nuove, "invariate": invariate,
                         "database": "letto in sola lettura",
                         "rilascio": (fatti.repo.get("rilascio") or {}).get("changelog")}
                chiusa = await session.execute(text(
                    "UPDATE public.documentazione_generazioni SET stato = 'COMPLETATA', "
                    "esito = CAST(:e AS jsonb) WHERE id = :id AND stato = 'IN_CORSO'"),
                    {"e": json.dumps(esito), "id": generazione_id})
                if chiusa.rowcount != 1:
                    raise RuntimeError("richiesta non più in corso (scaduta o chiusa da altri)")
                await registra_audit(session, evento="documentazione.generazione_completata",
                                     user_id=str(autore) if autore else None,
                                     dettagli={"generazione_id": str(generazione_id),
                                               "nuove": nuove, "invariate": invariate})
        GENERAZIONI.labels("completata").inc()
        logger.info("documentazione_generata", generazione_id=str(generazione_id),
                    nuove=len(nuove), invariate=len(invariate))
    except Exception as exc:  # noqa: BLE001 — esito ERRORE esplicito con allarme
        GENERAZIONI.labels("errore").inc()
        logger.error("documentazione_non_generata", generazione_id=str(generazione_id),
                     errore=type(exc).__name__)
        try:
            async with get_async_session() as session:
                await session.execute(text(
                    "UPDATE public.documentazione_generazioni SET stato = 'ERRORE', "
                    "errore = :e WHERE id = :id AND stato = 'IN_CORSO'"),
                    {"e": f"{type(exc).__name__}: {exc}"[:500], "id": generazione_id})
                await registra_audit(session, evento="documentazione.generazione_fallita",
                                     user_id=str(autore) if autore else None,
                                     dettagli={"generazione_id": str(generazione_id),
                                               "errore": type(exc).__name__})
        except Exception as exc2:  # noqa: BLE001 — resta la scadenza (interrotta)
            logger.error("documentazione_esito_non_registrato", errore=type(exc2).__name__)


@router.get("/generazioni/{generazione_id}", summary="Stato di una richiesta di generazione")
async def stato_generazione(generazione_id: str,
                            user: dict = Depends(conservatore)) -> dict[str, Any]:
    ident = _uuid(generazione_id)
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(_GENERAZIONE + "WHERE id = :id"),
                                          {"id": ident})).mappings().first()
    except SQLAlchemyError as exc:
        raise errore_db("stato della generazione", exc) from None
    if riga is None:
        raise ErroreApi("GOV-404")
    return _vista_generazione(riga)


# ── consultazione ─────────────────────────────────────────────────────

@router.get("", summary="Documenti del servizio: ultima versione e dati da compilare")
async def quadro(user: dict = Depends(conservatore)) -> dict[str, Any]:
    try:
        async with get_async_session() as session:
            ultime = (await session.execute(text(
                f"SELECT DISTINCT ON (v.tipo) {_COLONNE_VERSIONE} "
                "FROM public.documentazione_versioni v ORDER BY v.tipo, v.versione DESC"
            ))).mappings().all()
            richieste = (await session.execute(text(
                _GENERAZIONE + "ORDER BY richiesta_il DESC LIMIT 10"))).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_db("quadro della documentazione del servizio", exc) from None
    per_tipo = {r["tipo"]: r for r in ultime}
    documenti = []
    for tipo in TIPI:
        r = per_tipo.get(tipo)
        documenti.append({"tipo": tipo, "ultima": _vista_versione(r, True) if r else None})
    return {"documenti": documenti, "generazioni": [_vista_generazione(r) for r in richieste]}


@router.get("/versioni", summary="Storico delle versioni di un documento")
async def versioni(tipo: TipoDocumento = Query(...),
                   user: dict = Depends(conservatore)) -> dict[str, Any]:
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                f"SELECT {_COLONNE_VERSIONE} FROM public.documentazione_versioni v "
                "WHERE v.tipo = :t ORDER BY v.versione DESC LIMIT 200"), {"t": tipo})
            ).mappings().all()
    except SQLAlchemyError as exc:
        raise errore_db("storico della documentazione", exc) from None
    return {"tipo": tipo, "items": [_vista_versione(r) for r in righe]}


@router.get("/versioni/{versione_id}", summary="Dettaglio di una versione")
async def versione(versione_id: str, user: dict = Depends(conservatore)) -> dict[str, Any]:
    ident = _uuid(versione_id)
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                f"SELECT {_COLONNE_VERSIONE}, v.fonti FROM public.documentazione_versioni v "
                "WHERE v.id = :id"), {"id": ident})).mappings().first()
    except SQLAlchemyError as exc:
        raise errore_db("versione della documentazione", exc) from None
    if riga is None:
        raise ErroreApi("GOV-404")
    return _vista_versione(riga, True)


@router.get("/versioni/{versione_id}/{formato}", summary="Scarica Markdown o HTML stampabile")
async def scarica(versione_id: str, formato: Literal["markdown", "html"],
                  user: dict = Depends(conservatore)) -> Response:
    ident = _uuid(versione_id)
    colonna, impronta, media, estensione = (
        ("markdown", "impronta_sha256", "text/markdown; charset=utf-8", "md")
        if formato == "markdown" else ("html", "impronta_html", "text/html; charset=utf-8",
                                       "html"))
    try:
        async with get_async_session() as session:
            riga = (await session.execute(text(
                f"SELECT {colonna} AS contenuto, {impronta} AS impronta, nome_file, versione, "
                "tipo FROM public.documentazione_versioni WHERE id = :id"), {"id": ident})
            ).mappings().first()
            if riga is not None:
                autore = _utente(user)
                await registra_audit(session, evento="documentazione.versione_scaricata",
                                     user_id=str(autore) if autore else None,
                                     dettagli={"versione_id": str(ident), "formato": formato})
    except SQLAlchemyError as exc:
        raise errore_db("scaricamento della documentazione", exc) from None
    if riga is None:
        raise ErroreApi("GOV-404")
    nome = f"{riga['nome_file']}_v{riga['versione']}.{estensione}"
    return Response(bytes(riga["contenuto"]), media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{nome}"',
        "X-Impronta-SHA256": riga["impronta"].strip(), "Cache-Control": "no-store",
        # l'HTML scaricato non esegue nulla e non carica risorse esterne
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


__all__ = ["GENERAZIONI", "esegui_generazione", "router"]
