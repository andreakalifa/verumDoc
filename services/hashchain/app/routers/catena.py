# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
API della catena delle impronte usate dalla dashboard (G2).

- ``GET  /api/v1/hashchain/status``: (R04) dimensione del registro e ultimo
  checkpoint con stato del sigillo e del testimone; blocchi recenti della
  catena storica con lo stato di firma,
  conteggio dei blocchi per stato di firma, esito e data dell'ultima verifica
  COMPLETA (null se mai eseguita: la pagina dice «mai verificata»).
- ``POST /api/v1/hashchain/verifica-completa``: AVVIA la verifica dell'intera
  catena in un task di fondo e risponde subito 202 (g4-chiusura;
  ``app.services.verifica_asincrona``); 409 con l'id di quella in corso.
- ``GET  /api/v1/hashchain/verifica-completa/{verifica_id}``: stato della
  verifica (IN_CORSO / COMPLETATA / FALLITA / INTERROTTA), esito e date.

Il servizio hashchain non è esposto all'esterno (ingress e NetworkPolicy:
solo dashboard-api lo chiama). In più (g3-backend, §19.1) entrambe le API
richiedono il token verificato dell'utente, inoltrato da dashboard-api:
- stato: qualsiasi utente autenticato (401 senza token valido);
- verifica completa (avvio e stato): solo SUPER_ADMIN e RSC_CONSERVATORE (403 HC-403).
Chi ha avviato la verifica (``avviata_da``, ``ruolo``) si legge SOLO dal token
verificato: i campi omonimi del corpo sono accettati e ignorati.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import richiede_ruolo_verifica, utente_verificato
from app.config import settings
from app.registro import archivio
from app.services.verifica_asincrona import VerificaInCorso, avvia_verifica, stato_verifica
from app.services.verifica_completa import (
    ultima_verifica,
)
from libs.shared.db import get_async_session
from libs.shared.errori import ErroreApi, errore_registrato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/hashchain", tags=["Catena delle impronte"])

STATI_FIRMA = ("FIRMATO", "NON_FIRMATO", "DA_FIRMARE")


def _errore(stato: int, codice: str, titolo: str, exc: BaseException | None) -> ErroreApi:
    """Errore del catalogo (VD-C-100) con l'id della richiesta; il testo
    dell'eccezione resta nel log."""
    return errore_registrato(codice, exc, titolo=titolo, status=stato, evento="errore_catena")


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


def _blocco(r: dict[str, Any]) -> dict[str, Any]:
    h = r["hash_blocco"]
    return {
        "numero": r["numero_blocco"],
        "hash": (h[:20] + "...") if h else "—",
        "hash_blocco": h,
        "hash_blocco_precedente": r["hash_blocco_precedente"],
        "merkle_root": r["merkle_root"],
        "num_aip": r["num_aip"],
        "created_at": _iso(r["created_at"]),
        "versione_formato": r["versione_formato"],
        "stato_firma": r["stato_firma"],
        "motivo_firma": r["motivo_firma"],
        "data_marca": _iso(r["tsa_timestamp"]),
    }


def _integrita(ultima: dict[str, Any] | None, ultimo_numero: int | None) -> dict[str, Any]:
    if ultima is None:
        return {
            "integra": None, "esito": "MAI_VERIFICATA",
            "messaggio": "Verifica completa mai eseguita",
            "verificata_il": None, "verifica_id": None, "blocchi_verificati": None,
            "ultimo_blocco_verificato": None, "blocchi_aggiunti_dopo": None,
            "blocchi_senza_firma_qualificata": None, "avviata_da": None,
        }
    ultimo_verificato = ultima.get("ultimo_blocco_verificato")
    aggiunti = None
    if ultimo_numero is not None:
        aggiunti = ultimo_numero - ultimo_verificato if ultimo_verificato is not None \
            else ultimo_numero + 1
    return {
        "integra": ultima.get("integra"),
        "esito": ultima.get("esito"),
        "messaggio": ultima.get("messaggio"),
        "verificata_il": ultima.get("conclusa_il"),
        "verifica_id": ultima.get("verifica_id"),
        "blocchi_verificati": ultima.get("blocchi_verificati"),
        "ultimo_blocco_verificato": ultimo_verificato,
        # Blocchi sigillati dopo la verifica: non coperti dall'esito
        "blocchi_aggiunti_dopo": max(aggiunti, 0) if aggiunti is not None else None,
        "blocchi_senza_firma_qualificata": ultima.get("blocchi_senza_firma_qualificata"),
        "avviata_da": ultima.get("avviata_da"),
    }


async def _registro(session: Any) -> dict[str, Any]:
    """Dimensione del registro e ultimo checkpoint con sigillo e testimone (R04)."""
    stato = await archivio.leggi_stato(session)
    cp = await archivio.ultimo_checkpoint(session)
    return {
        "dimensione": stato.dimensione,
        "ultimo_checkpoint": None if cp is None else {
            "dimensione": int(cp["dimensione"]), "radice": cp["radice"],
            "emesso_il": _iso(cp["emesso_il"]), "stato_sigillo": cp["stato_sigillo"],
            "motivo_sigillo": cp["motivo_sigillo"], "stato_testimone": cp["stato_testimone"],
            "controfirmato_il": _iso(cp["controfirmato_il"]),
            "esito_testimone": cp["esito_testimone"],
        },
        "testimone_dimensione": stato.testimone_dimensione,
    }


@router.get("/status")
async def stato_catena(
    limit: int = Query(5, ge=1, le=100, description="Blocchi recenti da restituire"),
    _utente: dict = Depends(utente_verificato),
) -> dict:
    """Stato della catena per la dashboard (nessuna verifica eseguita qui)."""
    try:
        async with get_async_session() as session:
            totale = (await session.execute(
                text("SELECT COUNT(*) FROM public.hashchain_blocks")
            )).scalar_one()
            per_firma = {
                r[0]: r[1] for r in (await session.execute(text(
                    "SELECT stato_firma, COUNT(*) FROM public.hashchain_blocks "
                    "WHERE numero_blocco > 0 GROUP BY stato_firma"
                ))).all()
            }
            righe = (await session.execute(
                text(
                    "SELECT numero_blocco, hash_blocco, hash_blocco_precedente, merkle_root, "
                    "num_aip, created_at, versione_formato, stato_firma, motivo_firma, "
                    "tsa_timestamp FROM public.hashchain_blocks "
                    "ORDER BY numero_blocco DESC LIMIT :limite"
                ),
                {"limite": limit},
            )).mappings().all()
            ultima = await ultima_verifica(session)
            registro = await _registro(session)
    except SQLAlchemyError as exc:
        raise _errore(503, "HC-503", "Database non disponibile: stato della catena non letto",
                      exc) from None

    ultimo_numero = righe[0]["numero_blocco"] if righe else None
    return {
        "totale_blocchi": totale,
        "ultimo_blocco": ultimo_numero,
        # Il blocco genesi (n. 0) non si firma: escluso dai conteggi
        "firma": {
            "conteggi": {s: per_firma.get(s, 0) for s in STATI_FIRMA},
            "blocchi_firmati": per_firma.get("FIRMATO", 0),
            "blocchi_da_firmare_o_non_firmati": sum(
                n for s, n in per_firma.items() if s != "FIRMATO"
            ),
        },
        "integrita": _integrita(ultima, ultimo_numero),
        "blocchi_recenti": [_blocco(dict(r)) for r in righe],
        # R04: registro a due livelli (i blocchi sopra sono la catena storica,
        # che non cresce più: i PdA nuovi entrano nel registro)
        "registro": registro,
    }


class RichiestaVerifica(BaseModel):
    """Corpo facoltativo, accettato per compatibilità e IGNORATO: chi avvia la
    verifica si legge dal token verificato, mai da un dato del client."""
    model_config = ConfigDict(extra="ignore")
    avviata_da: str | None = Field(None, max_length=100, description="Ignorato")
    ruolo: str | None = Field(None, max_length=50, description="Ignorato")


def _avviata_da(utente: dict) -> str | None:
    valore = str(utente.get("user_id") or utente.get("sub") or "").strip()
    return valore[:100] or None


@router.post("/verifica-completa", status_code=202)
async def verifica_completa(
    req: RichiestaVerifica | None = None,
    utente: dict = Depends(richiede_ruolo_verifica),
) -> JSONResponse:
    """
    Avvia la verifica completa dell'intera catena (ricalcolo di impronte,
    collegamenti e radici di Merkle) in un task di fondo e risponde subito:

    - 202 ``{"verifica_id", "stato": "IN_CORSO", "avviata_il"}``;
    - 409 ``HC-409`` con ``verifica_id`` della verifica già in corso;
    - 503 ``HC-503`` se il database non risponde (nessuna verifica avviata).

    L'esito si legge con GET /verifica-completa/{verifica_id}; il tempo massimo
    è HASHCHAIN_VERIFICA_TIMEOUT_SECONDS (oltre: stato INTERROTTA).
    """
    try:
        avvio = await avvia_verifica(avviata_da=_avviata_da(utente),
                                     ruolo=str(utente.get("ruolo"))[:50],
                                     timeout_secondi=settings.hashchain_verifica_timeout_seconds)
    except VerificaInCorso as exc:
        raise HTTPException(409, {"type": "HC-409",
                                  "title": "Una verifica completa è già in corso",
                                  "verifica_id": exc.verifica_id}) from None
    except SQLAlchemyError as exc:
        raise _errore(503, "HC-503",
                      "Database non disponibile: verifica non avviata", exc) from None
    return JSONResponse(status_code=202, content=avvio)


@router.get("/verifica-completa/{verifica_id}")
async def stato_verifica_completa(
    verifica_id: uuid.UUID = Path(..., description="Id restituito dall'avvio"),
    _utente: dict = Depends(richiede_ruolo_verifica),
) -> dict:
    """Stato della verifica: IN_CORSO, COMPLETATA (con esito INTEGRA /
    COMPROMESSA / NESSUN_BLOCCO), FALLITA o INTERROTTA; date di avvio e fine.
    404 HC-404 se l'id non esiste."""
    try:
        stato = await stato_verifica(str(verifica_id))
    except SQLAlchemyError as exc:
        raise _errore(503, "HC-503", "Database non disponibile: stato non letto", exc) from None
    if stato is None:
        raise HTTPException(404, {"type": "HC-404", "title": "Verifica non trovata"})
    return stato
