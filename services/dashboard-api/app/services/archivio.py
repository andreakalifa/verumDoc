# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Letture dell'archivio per la dashboard (G2, VD-C-014/VD-C-029): ricerca dei
pacchetti, elenco dei versamenti con il motivo del rifiuto, elenco dei
pacchetti con i metadati dell'Allegato 5.

Regole seguite:
- R03 f2 (REV-06): elenchi e ricerche reggono milioni di PdA per ente: niente
  ``COUNT(*)`` né OFFSET, paginazione a cursore servita dagli indici
  ``idx_aip_tenant_versamento`` / ``idx_aip_tenant_stato_versamento``
  (migrazione 20261021_022); la ricerca testuale (e per classe) va
  sull'indice di ricerca OpenSearch tramite distribution, che lo possiede
  (una sola implementazione, REGOLE §9.1), mai con ``ILIKE '%…%'`` sul database;
- ogni filtro dichiarato viene applicato davvero, e la risposta dice quali
  filtri ha applicato (``filtri_applicati``);
- il testo d'errore tecnico non esce verso il client: per un rifiuto di tipo
  «tecnico» si restituisce un messaggio generico (il dettaglio è nel registro
  del servizio che l'ha prodotto).
"""
from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, literal, literal_column, select, tuple_

from libs.shared.db import get_async_session_lettura
from libs.shared.models import (
    METADATI_ALLEGATO5_CAMPI,
    Aip,
    AipStato,
    ClasseDocumentale,
    Documento,
    MetadatiAllegato5,
    Pdv,
    PdvStato,
)

FUSO_ARCHIVIO = "Europe/Rome"
MAX_TERMINI = 8

MESSAGGIO_TECNICO = (
    "Errore tecnico durante l'elaborazione del pacchetto: il dettaglio è nel registro "
    "del servizio (nessun problema nei dati versati è stato accertato)."
)


def _iso(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "value"):
        return v.value
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def termini_ricerca(testo: str | None) -> list[str]:
    if not testo:
        return []
    return [t for t in testo.split() if t][:MAX_TERMINI]


def _classe(codice: str | None, nome: str | None) -> dict | None:
    return {"codice": codice, "nome": nome} if codice is not None else None


# ── Cursori ─────────────────────────────────────────────────────────

class CursoreNonValido(ValueError):
    """Cursore illeggibile o di un altro elenco (DSH-104)."""


def _b64(dati: dict) -> str:
    grezzo = json.dumps(dati, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(grezzo).decode().rstrip("=")


def _da_b64(cursore: str) -> dict:
    try:
        dati = json.loads(base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CursoreNonValido(str(exc)) from None
    if not isinstance(dati, dict):
        raise CursoreNonValido("forma non valida")
    return dati


def _posizione(dati: dict) -> tuple[datetime, uuid.UUID]:
    try:
        quando = datetime.fromisoformat(dati["d"])
        if quando.tzinfo is None:
            raise ValueError("data senza fuso")
        return quando, uuid.UUID(str(dati["i"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise CursoreNonValido(str(exc)) from None


def cursore_pda(data_versamento: datetime, aip_id: uuid.UUID, motore: str = "db") -> str:
    """Cursore opaco della posizione (data di versamento, id) di un PdA."""
    return _b64({"m": motore, "d": data_versamento.isoformat(), "i": str(aip_id)})


def cursore_indice(interno: str) -> str:
    """Cursore della ricerca sull'indice (avvolge quello di distribution)."""
    return _b64({"m": "indice", "c": interno})


def leggi_cursore(cursore: str, motore: str) -> Any:
    """Contenuto del cursore emesso da ``motore`` («db» o «indice»), o
    ``CursoreNonValido``. Il cursore non dà accesso a nulla: il filtro per ente
    si applica sempre."""
    dati = _da_b64(cursore)
    if dati.get("m") != motore:
        raise CursoreNonValido("cursore di un'altra ricerca")
    if motore == "indice":
        interno = dati.get("c")
        if not isinstance(interno, str) or not interno:
            raise CursoreNonValido("cursore dell'indice mancante")
        return interno
    return _posizione(dati)


def _dopo(colonna_data, colonna_id, cursore: str | None):
    """Condizione «dopo il cursore» per l'ordine (data DESC, id DESC)."""
    if not cursore:
        return None
    quando, ultimo = leggi_cursore(cursore, "db")
    return tuple_(colonna_data, colonna_id) < tuple_(
        literal(quando, colonna_data.type), literal(ultimo, colonna_id.type))


def intervallo_anno(anno: int) -> tuple[datetime, datetime]:
    """Inizio (incluso) e fine (esclusa) dell'anno nel fuso dell'archivio: un
    filtro su intervallo usa l'indice, ``extract(year …)`` no."""
    fuso = ZoneInfo(FUSO_ARCHIVIO)
    return datetime(anno, 1, 1, tzinfo=fuso), datetime(anno + 1, 1, 1, tzinfo=fuso)


# ── Ricerca ─────────────────────────────────────────────────────────

#: Ricerca sull'indice: (corpo per distribution) → risposta di distribution.
RicercaIndice = Callable[[dict], Awaitable[dict]]


def _voce_ricerca(r: Any) -> dict:
    return {
        "aip_id": str(r.id), "urn": r.urn, "stato": r.stato.value,
        "data_versamento": _iso(r.data_versamento),
        "hash_sha256": r.hash_sha256, "dimensione_bytes": r.dimensione_bytes,
        "pdv_id": str(r.pdv_id), "oggetto": r.oggetto,
        "classe_documentale": _classe(r.codice, r.nome),
    }


def _select_ricerca():
    return (
        select(Aip.id, Aip.urn, Aip.stato, Aip.data_versamento, Aip.hash_sha256,
               Aip.dimensione_bytes, Pdv.id.label("pdv_id"), Pdv.oggetto,
               ClasseDocumentale.codice, ClasseDocumentale.nome)
        .join(Pdv, Pdv.id == Aip.pdv_id)
        .outerjoin(ClasseDocumentale, ClasseDocumentale.id == Pdv.classe_doc_id)
    )


async def _codice_classe(session: Any, classe: str) -> str | None:
    """Codice della classe (l'indice la conosce per codice): un id si traduce."""
    try:
        cid = uuid.UUID(classe)
    except ValueError:
        return classe.strip()
    return (await session.execute(
        select(ClasseDocumentale.codice).where(ClasseDocumentale.id == cid)
    )).scalar_one_or_none()


async def cerca_aip(
    tenant_id: uuid.UUID, *, testo: str | None, classe: str | None, anno: int | None,
    stato: AipStato | None, cursore: str | None, limit: int,
    ricerca_indice: RicercaIndice | None = None,
) -> dict:
    """Ricerca dei PdA dell'ente, a cursore.

    - Con testo o classe: indice di ricerca (OpenSearch, via distribution con
      le credenziali del chiamante: ``ricerca_indice``), poi i PdA trovati si
      rileggono dal database (dati e stato attuali, solo dell'ente). Il filtro
      per stato si applica sul dato attuale: una pagina può avere meno di
      ``limit`` voci anche se ``altri`` è vero.
    - Senza: database, per (data di versamento, id) decrescenti sull'indice
      dell'ente (e dello stato); l'anno è un intervallo di date.
    """
    termini = termini_ricerca(testo)
    filtri = {
        "tenant_id": str(tenant_id),
        "testo": " ".join(termini) or None,
        "classe_documentale": classe or None,
        "anno_versamento": anno,
        "stato": stato.value if stato else None,
    }
    if termini or classe:
        if ricerca_indice is None:
            raise RuntimeError("ricerca sull'indice non configurata")
        return await _cerca_nell_indice(tenant_id, termini, classe, anno, stato, cursore, limit,
                                        filtri, ricerca_indice)

    condizioni = [Aip.tenant_id == tenant_id]
    if stato is not None:
        condizioni.append(Aip.stato == stato)
    if anno is not None:
        inizio, fine = intervallo_anno(anno)
        condizioni += [Aip.data_versamento >= inizio, Aip.data_versamento < fine]
    dopo = _dopo(Aip.data_versamento, Aip.id, cursore)
    if dopo is not None:
        condizioni.append(dopo)
    async with get_async_session_lettura() as session:
        righe = (await session.execute(
            _select_ricerca().where(and_(*condizioni))
            .order_by(Aip.data_versamento.desc(), Aip.id.desc()).limit(limit + 1)
        )).all()
    altri = len(righe) > limit
    righe = righe[:limit]
    return {
        "items": [_voce_ricerca(r) for r in righe],
        "limit": limit,
        "cursore": cursore_pda(righe[-1].data_versamento, righe[-1].id)
        if altri and righe else None,
        "altri": altri,
        "motore": "database",
        "filtri_applicati": filtri,
        "campi_testo": [],
    }


async def _cerca_nell_indice(
    tenant_id: uuid.UUID, termini: list[str], classe: str | None, anno: int | None,
    stato: AipStato | None, cursore: str | None, limit: int, filtri: dict,
    ricerca_indice: RicercaIndice,
) -> dict:
    corpo: dict[str, Any] = {"tenant_id": str(tenant_id), "limit": limit}
    if termini:
        corpo["query"] = " ".join(termini)
    if cursore:
        corpo["cursore"] = leggi_cursore(cursore, "indice")
    if anno is not None:
        inizio, fine = intervallo_anno(anno)
        corpo["data_da"] = inizio.isoformat()
        corpo["data_a"] = (fine - timedelta(microseconds=1)).isoformat()
    async with get_async_session_lettura() as session:
        if classe:
            codice = await _codice_classe(session, classe)
            if codice is None:  # id di classe inesistente: nessun risultato
                return {"items": [], "limit": limit, "cursore": None, "altri": False,
                        "motore": "indice", "filtri_applicati": filtri, "campi_testo": []}
            corpo["classe_documentale"] = codice
        risposta = await ricerca_indice(corpo)
        ids: list[uuid.UUID] = []
        for voce in risposta.get("items") or []:
            try:
                ids.append(uuid.UUID(str(voce.get("aip_id"))))
            except (TypeError, ValueError, AttributeError):
                continue
        righe = []
        if ids:
            condizioni = [Aip.tenant_id == tenant_id, Aip.id.in_(ids)]
            if stato is not None:
                condizioni.append(Aip.stato == stato)
            righe = (await session.execute(_select_ricerca().where(and_(*condizioni)))).all()
    per_id = {r.id: r for r in righe}
    interno = risposta.get("cursore")
    altri = bool(risposta.get("altri")) and isinstance(interno, str) and bool(interno)
    return {
        "items": [_voce_ricerca(per_id[i]) for i in ids if i in per_id],
        "limit": limit,
        "cursore": cursore_indice(interno) if altri else None,
        "altri": altri,
        "motore": "indice",
        "filtri_applicati": filtri,
        "campi_testo": list(risposta.get("campi_testo") or []),
    }


# ── Versamenti ──────────────────────────────────────────────────────

def motivi_rifiuto(errori: Any) -> list[dict]:
    """Voci di ``pdv.errori_validazione`` in forma uniforme per il client."""
    if not isinstance(errori, list):
        return []
    voci: list[dict] = []
    for e in errori:
        if isinstance(e, dict):
            tecnico = e.get("tipo") == "tecnico"
            voci.append({
                "codice": e.get("codice"),
                "messaggio": MESSAGGIO_TECNICO if tecnico else e.get("messaggio"),
                "passo": e.get("nome") or e.get("step"),
                "tipo": e.get("tipo"),
            })
        elif e is not None:
            voci.append({"codice": None, "messaggio": str(e)[:500], "passo": None, "tipo": None})
    return voci


#: R08 (D01): riassunto delle firme del passo «verifica_firme» (rapporto di
#: verifica salvato sul PdV) calcolato da PostgreSQL: solo nome del file, esito
#: in parole, firmatari con indicazione e motivi (mai il rapporto grezzo).
SQL_FIRME_PDV = (
    "(SELECT jsonb_agg(jsonb_build_object("
    "  'nome_file', f ->> 'nome_file', 'esito', f ->> 'esito_in_parole', "
    "  'motivo', f -> 'esito' ->> 'motivo', 'verificatore', f -> 'esito' ->> 'verificatore', "
    "  'firme', COALESCE((SELECT jsonb_agg(jsonb_build_object("
    "      'firmatario', x ->> 'firmatario', 'indicazione', x ->> 'indicazione', "
    "      'revoca', x ->> 'revoca', 'motivi', x -> 'motivi')) "
    "    FROM jsonb_array_elements(COALESCE(f -> 'esito' -> 'firme', '[]'::jsonb)) x), "
    "    '[]'::jsonb)) ORDER BY f ->> 'nome_file') "
    " FROM jsonb_array_elements(COALESCE(pdv.metadata_json -> 'verifica' -> 'passi', "
    "      '[]'::jsonb)) s, "
    "      jsonb_array_elements(COALESCE(s -> 'dettagli' -> 'rapporto' -> 'file', '[]'::jsonb)) f "
    " WHERE s ->> 'nome' = 'verifica_firme')")
SQL_LIVELLO_FIRME = (
    "(SELECT s -> 'dettagli' ->> 'livello' FROM jsonb_array_elements(COALESCE("
    "pdv.metadata_json -> 'verifica' -> 'passi', '[]'::jsonb)) s "
    "WHERE s ->> 'nome' = 'verifica_firme' LIMIT 1)")


def firme_del_pdv(livello: Any, file: Any) -> dict | None:
    """Esito delle firme leggibile per la console (None: verifica senza firme)."""
    if not livello:
        return None
    righe = file if isinstance(file, list) else []
    return {"livello": str(livello), "file": [r for r in righe if isinstance(r, dict)]}


def codifica_cursore(data_ricezione: datetime, pdv_id: uuid.UUID) -> str:
    """Cursore opaco della posizione (data di ricezione, id) dell'ultimo elemento."""
    grezzo = json.dumps({"d": data_ricezione.isoformat(), "i": str(pdv_id)},
                        separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(grezzo).decode().rstrip("=")


def decodifica_cursore(cursore: str) -> tuple[datetime, uuid.UUID]:
    """Posizione dal cursore; ``CursoreNonValido`` se non è un cursore emesso qui.
    Il cursore non dà accesso a nulla: il filtro per ente si applica sempre."""
    try:
        grezzo = base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4))
        dati = json.loads(grezzo)
        quando = datetime.fromisoformat(dati["d"])
        if quando.tzinfo is None:
            raise ValueError("data senza fuso")
        return quando, uuid.UUID(str(dati["i"]))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise CursoreNonValido(str(exc)) from None


async def elenco_versamenti(
    tenant_id: uuid.UUID, *, stato: PdvStato | None, cursore: str | None, limit: int,
) -> dict:
    """Versamenti dell'ente dal più recente, a cursore (R03 i1).

    Paginazione per chiave ``(data_ricezione, id)`` decrescente, servita dagli
    indici ``idx_pdv_tenant_ricezione`` / ``idx_pdv_tenant_stato_ricezione``
    (migrazione 20261021_020): il costo di una pagina non dipende dal numero di
    versamenti dell'ente né dalla posizione. Niente ``COUNT(*)`` né offset (a 1
    milione di PdV al giorno un conteggio per richiesta non regge): ``altri``
    dice se esiste una pagina successiva, ``cursore`` la apre."""
    condizioni = [Pdv.tenant_id == tenant_id]
    if stato is not None:
        condizioni.append(Pdv.stato == stato)
    if cursore:
        quando, ultimo = decodifica_cursore(cursore)
        condizioni.append(tuple_(Pdv.data_ricezione, Pdv.id) < tuple_(
            literal(quando, Pdv.data_ricezione.type), literal(ultimo, Pdv.id.type)))
    aip_del_pdv = (
        select(Aip.id).where(Aip.pdv_id == Pdv.id)
        .order_by(Aip.created_at.desc()).limit(1).correlate(Pdv).scalar_subquery()
    )
    async with get_async_session_lettura() as session:
        righe = (await session.execute(
            select(Pdv.id, Pdv.stato, Pdv.data_ricezione, Pdv.data_aggiornamento, Pdv.oggetto,
                   Pdv.numero_protocollo, Pdv.errori_validazione, ClasseDocumentale.codice,
                   ClasseDocumentale.nome, aip_del_pdv.label("aip_id"),
                   literal_column(SQL_FIRME_PDV).label("firme"),
                   literal_column(SQL_LIVELLO_FIRME).label("livello_firme"))
            .outerjoin(ClasseDocumentale, ClasseDocumentale.id == Pdv.classe_doc_id)
            .where(and_(*condizioni))
            .order_by(Pdv.data_ricezione.desc(), Pdv.id.desc())
            .limit(limit + 1)
        )).all()
    altri = len(righe) > limit
    righe = righe[:limit]

    items = []
    for r in righe:
        motivi = motivi_rifiuto(r.errori_validazione) if r.stato == PdvStato.ERRORE else []
        items.append({
            "pdv_id": str(r.id), "stato": r.stato.value,
            "data_ricezione": _iso(r.data_ricezione),
            "data_aggiornamento": _iso(r.data_aggiornamento),
            "oggetto": r.oggetto, "numero_protocollo": r.numero_protocollo,
            "classe_documentale": _classe(r.codice, r.nome),
            "aip_id": str(r.aip_id) if r.aip_id else None,
            "motivo_rifiuto": next((m["messaggio"] for m in motivi if m["messaggio"]), None)
            if r.stato == PdvStato.ERRORE else None,
            "motivi_rifiuto": motivi,
            "firme": firme_del_pdv(r.livello_firme, r.firme),
        })
    prossimo = (codifica_cursore(righe[-1].data_ricezione, righe[-1].id)
                if altri and righe else None)
    return {"items": items, "limit": limit, "cursore": prossimo, "altri": altri}


# ── Pacchetti con metadati Allegato 5 ───────────────────────────────

def _metadati(m: MetadatiAllegato5 | None) -> dict | None:
    if m is None:
        return None
    return {campo: _iso(getattr(m, campo)) for campo in METADATI_ALLEGATO5_CAMPI}


async def elenco_aip(
    tenant_id: uuid.UUID, *, stato: AipStato | None, cursore: str | None, limit: int,
) -> dict:
    """PdA dell'ente dal più recente, a cursore (R03 f2, REV-06).

    Chiave ``(data_versamento, id)`` decrescente servita dagli indici
    ``idx_aip_tenant_versamento`` / ``idx_aip_tenant_stato_versamento``: il costo
    di una pagina non dipende dal numero di PdA dell'ente né dalla posizione.
    Niente ``COUNT(*)`` né offset: ``altri`` dice se c'è una pagina successiva,
    ``cursore`` la apre."""
    condizioni = [Aip.tenant_id == tenant_id]
    if stato is not None:
        condizioni.append(Aip.stato == stato)
    dopo = _dopo(Aip.data_versamento, Aip.id, cursore)
    if dopo is not None:
        condizioni.append(dopo)
    async with get_async_session_lettura() as session:
        righe = (await session.execute(
            select(Aip, Pdv.oggetto, ClasseDocumentale.codice, ClasseDocumentale.nome)
            .join(Pdv, Pdv.id == Aip.pdv_id)
            .outerjoin(ClasseDocumentale, ClasseDocumentale.id == Pdv.classe_doc_id)
            .where(and_(*condizioni))
            .order_by(Aip.data_versamento.desc(), Aip.id.desc()).limit(limit + 1)
        )).all()
        altri = len(righe) > limit
        righe = righe[:limit]
        ids = [r[0].id for r in righe]
        documenti = (await session.execute(
            select(Documento.id, Documento.aip_id, Documento.nome_file, Documento.ordine,
                   Documento.formato_mime, Documento.hash_sha256, MetadatiAllegato5)
            .outerjoin(MetadatiAllegato5, MetadatiAllegato5.documento_id == Documento.id)
            .where(Documento.aip_id.in_(ids))
            .order_by(Documento.aip_id, Documento.ordine, Documento.nome_file)
        )).all() if ids else []

    per_aip: dict[uuid.UUID, list[dict]] = {}
    for d in documenti:
        per_aip.setdefault(d.aip_id, []).append({
            "documento_id": str(d.id), "nome_file": d.nome_file, "ordine": d.ordine,
            "formato_mime": d.formato_mime, "hash_sha256": d.hash_sha256,
            "metadati": _metadati(d.MetadatiAllegato5),
        })

    items = []
    for a, oggetto, codice, nome in righe:
        items.append({
            "aip_id": str(a.id), "urn": a.urn, "stato": a.stato.value,
            "hash_sha256": a.hash_sha256, "dimensione_bytes": a.dimensione_bytes,
            "data_versamento": _iso(a.data_versamento),
            "data_conservazione": _iso(a.data_conservazione),
            "scadenza_conservazione": _iso(a.scadenza_conservazione),
            "anni_conservazione": a.anni_conservazione,
            "pdv_id": str(a.pdv_id), "oggetto": oggetto,
            "classe_documentale": _classe(codice, nome),
            "metadati_allegato5": per_aip.get(a.id, []),
        })
    ultimo = righe[-1][0] if righe else None
    return {
        "items": items, "limit": limit,
        "cursore": cursore_pda(ultimo.data_versamento, ultimo.id)
        if altri and ultimo is not None else None,
        "altri": altri,
        "campi_allegato5": list(METADATI_ALLEGATO5_CAMPI),
    }
