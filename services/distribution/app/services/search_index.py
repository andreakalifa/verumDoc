# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/services/search_index.py — indice di ricerca dei PdA (OpenSearch).

Riferimenti: F2.2; R03 b4 (VD-C-093, seguito di VD-C-062); piano R03–R05 §4.2
(«indicizzazione a lotti dal flusso eventi, instradamento per ente»);
REGOLE_SVILUPPO §14.2-§14.3 (fail-closed), §19.9 (minimizzazione).

Prima ``index_aip()`` era definita e mai chiamata: la ricerca era sempre vuota.
Ora:
  - i documenti si costruiscono dai dati del PdA (``documento_ricerca``, puro)
    e si scrivono a lotti con la bulk API (``indicizza_lotto``), id = id del
    PdA (idempotente: un nuovo invio sovrascrive lo stesso documento),
    instradamento (``routing``) = ente; i metadati marcati «sensibili» nel
    profilo della classe NON entrano nell'indice;
  - la ricerca filtra SEMPRE per ente (``corpo_ricerca`` solleva senza ente)
    e pagina a cursore (``search_after`` su data di versamento e id: niente
    ``from`` profondi); il cursore è opaco e si valida (DST-104);
  - OpenSearch si interroga via HTTP (``httpx``) con la sua API REST: nessun
    client dedicato da installare (prima ``opensearch-py`` non era nemmeno
    presente e ogni ricerca falliva), timeout da impostazione, credenziali
    facoltative (``OPENSEARCH_UTENTE``/``OPENSEARCH_PASSWORD``).
"""
from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

INDEX_MAPPING: dict[str, Any] = {
    "mappings": {
        "properties": {
            "aip_id":              {"type": "keyword"},
            "pdv_id":              {"type": "keyword"},
            "tenant_id":           {"type": "keyword"},
            "urn":                 {"type": "keyword"},
            "classe_documentale":  {"type": "keyword"},
            "stato":               {"type": "keyword"},
            "hash_sha256":         {"type": "keyword"},
            "anno":                {"type": "keyword"},
            "numero":              {"type": "keyword"},
            "registro":            {"type": "keyword"},
            "data_versamento":     {"type": "date"},
            "data_conservazione":  {"type": "date"},
            "scadenza_conservazione": {"type": "date"},
            "anni_conservazione":  {"type": "integer"},
            "dimensione_bytes":    {"type": "long"},
            "oggetto":             {"type": "text", "analyzer": "italian"},
            "identificativi_documento": {"type": "keyword"},
            "tipologie_documentali": {"type": "keyword"},
            "soggetti_produttori": {"type": "text", "analyzer": "italian"},
            "numeri_registrazione": {"type": "keyword"},
            "data_documento":      {"type": "date"},
            "nomi_file":           {"type": "keyword"},
            "testo_completo":      {"type": "text", "analyzer": "italian"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
}

#: Campi in cui cerca il testo libero.
CAMPI_TESTO = ["testo_completo", "oggetto", "soggetti_produttori", "classe_documentale",
               "numero", "registro", "urn", "identificativi_documento", "numeri_registrazione"]
#: Ordinamento totale della ricerca (§12.8): data di versamento, poi id.
ORDINAMENTO = [{"data_versamento": {"order": "desc"}}, {"aip_id": {"order": "asc"}}]


class RicercaNonDisponibile(RuntimeError):
    """OpenSearch non raggiungibile o risposta inattesa: mai un elenco vuoto
    spacciato per «nessun risultato» (revisione di sicurezza B9)."""


class CursoreNonValido(ValueError):
    """Il cursore non è stato emesso da questa ricerca (DST-104)."""


# ── Client HTTP ─────────────────────────────────────────────────────

class ClientOpenSearch:
    """Chiamate REST a OpenSearch (una connessione per uso, chiusa da ``chiudi``)."""

    def __init__(self, url: str | None = None, *, timeout: float | None = None) -> None:
        autenticazione = None
        if settings.opensearch_utente:
            autenticazione = (settings.opensearch_utente,
                              settings.opensearch_password.get_secret_value())
        self._http = httpx.AsyncClient(
            base_url=(url or settings.opensearch_url).rstrip("/"),
            timeout=timeout or settings.opensearch_timeout_secondi, auth=autenticazione,
        )

    async def _json(self, metodo: str, percorso: str, **kw: Any) -> dict[str, Any]:
        risposta = await self._http.request(metodo, percorso, **kw)
        if risposta.status_code >= 400:
            raise RicercaNonDisponibile(f"OpenSearch {metodo} {percorso}: {risposta.status_code}")
        dati = risposta.json()
        if not isinstance(dati, dict):
            raise RicercaNonDisponibile("risposta di OpenSearch non valida")
        return dati

    async def esiste_indice(self, indice: str) -> bool:
        risposta = await self._http.head(f"/{indice}")
        return risposta.status_code == 200

    async def crea_indice(self, indice: str, corpo: dict[str, Any]) -> None:
        await self._json("PUT", f"/{indice}", json=corpo)

    async def bulk(self, ndjson: str) -> dict[str, Any]:
        return await self._json("POST", "/_bulk", content=ndjson.encode("utf-8"),
                                headers={"Content-Type": "application/x-ndjson"})

    async def cerca(self, indice: str, corpo: dict[str, Any],
                    routing: str | None = None) -> dict[str, Any]:
        parametri = {"routing": routing} if routing else None
        return await self._json("POST", f"/{indice}/_search", json=corpo, params=parametri)

    async def chiudi(self) -> None:
        await self._http.aclose()


def _client_opensearch() -> ClientOpenSearch:
    return ClientOpenSearch()


async def ensure_index() -> None:
    """Crea l'indice se non esiste (all'avvio; OpenSearch giù: avviso, si riprova
    alla prima indicizzazione)."""
    client = _client_opensearch()
    try:
        if not await client.esiste_indice(settings.opensearch_index):
            await client.crea_indice(settings.opensearch_index, INDEX_MAPPING)
            logger.info("opensearch_index_created", index=settings.opensearch_index)
    except (httpx.HTTPError, RicercaNonDisponibile) as exc:
        logger.warning("opensearch_unavailable", errore=type(exc).__name__)
    finally:
        await client.chiudi()


# ── Documento dell'indice ───────────────────────────────────────────

def _iso(valore: Any) -> Any:
    if isinstance(valore, (datetime, date)):
        return valore.isoformat()
    return valore


#: Campi dell'indice → nomi di metadato che li coprono (REV-11): se il profilo
#: marca sensibile uno di questi nomi, il campo non entra nell'indice né nel
#: testo completo. Vale anche per i campi «base» (oggetto, nomi dei file,
#: produttore), non solo per quelli dell'Allegato 5.
NOMI_CAMPO: dict[str, frozenset[str]] = {
    "oggetto": frozenset({"oggetto"}),
    "soggetti_produttori": frozenset({"soggetto_produttore_nome", "soggetto_produttore"}),
    "nomi_file": frozenset({"nome_file", "nomi_file"}),
    "identificativi_documento": frozenset({"identificativo_documento"}),
    "tipologie_documentali": frozenset({"tipologia_documentale"}),
    "numeri_registrazione": frozenset({"numero_registrazione"}),
    "data_documento": frozenset({"data_documento"}),
    "numero": frozenset({"numero", "numero_ud"}),
    "registro": frozenset({"registro", "registro_ud"}),
    "anno": frozenset({"anno", "anno_ud"}),
}
#: Altri metadati dell'Allegato 5 che entrano solo nel testo completo.
_SOLO_TESTO = ("classificazione", "fascicolo_id")


def _escluso(campo: str, escludi: frozenset[str]) -> bool:
    return bool(NOMI_CAMPO.get(campo, frozenset({campo})) & escludi)


def _senza_sensibili(valore: Any, escludi: frozenset[str]) -> Any:
    """Copia di ``valore`` senza le chiavi sensibili a QUALSIASI profondità
    (es. ``metadati_extra.dati_specifici.<nome>``)."""
    if isinstance(valore, dict):
        return {k: _senza_sensibili(v, escludi) for k, v in valore.items()
                if str(k) not in escludi}
    if isinstance(valore, list):
        return [_senza_sensibili(v, escludi) for v in valore]
    return valore


def _testo_valore(valore: Any) -> str:
    if isinstance(valore, (dict, list)):
        return json.dumps(valore, ensure_ascii=False, sort_keys=True, default=str)
    return str(valore)


def documento_ricerca(
    aip: Mapping[str, Any],
    metadati: Sequence[Mapping[str, Any]],
    *,
    sensibili: Iterable[str] = (),
) -> dict[str, Any]:
    """
    Documento OpenSearch di un PdA (funzione pura).

    ``aip``: aip_id, pdv_id, tenant_id, urn, stato, classe_documentale,
    hash_sha256, data_versamento, data_conservazione, scadenza_conservazione,
    anni_conservazione, dimensione_bytes, chiave {numero, anno, registro},
    oggetto, nomi_file. ``metadati``: righe di ``metadati_allegato5`` dei
    documenti. ``sensibili``: nomi dei metadati del profilo marcati sensibili:
    nessuno entra nell'indice, né come campo né nel testo completo, compresi
    oggetto, produttore, nomi dei file (``NOMI_CAMPO``) e le chiavi annidate
    dei metadati liberi (R03 f2, REV-11; REGOLE §19.9).
    """
    escludi = frozenset(str(n) for n in sensibili)

    def ammesso(campo: str) -> bool:
        return not _escluso(campo, escludi)

    chiave = aip.get("chiave") or {}
    oggetti = ([aip.get("oggetto")] + [m.get("oggetto") for m in metadati]
               if ammesso("oggetto") else [])
    produttori = sorted({str(m["soggetto_produttore_nome"]) for m in metadati
                         if m.get("soggetto_produttore_nome")}) \
        if ammesso("soggetti_produttori") else []
    testo: list[str] = [str(x) for x in oggetti if x]
    testo += produttori
    for m in metadati:
        for campo, nome_indice in (("identificativo_documento", "identificativi_documento"),
                                   ("tipologia_documentale", "tipologie_documentali"),
                                   ("numero_registrazione", "numeri_registrazione")):
            if m.get(campo) and ammesso(nome_indice):
                testo.append(str(m[campo]))
        for campo in _SOLO_TESTO:
            if m.get(campo) and campo not in escludi:
                testo.append(str(m[campo]))
        extra = _senza_sensibili(m.get("metadati_extra") or {}, escludi)
        if isinstance(extra, dict):
            testo += [_testo_valore(v) for k, v in sorted(extra.items())
                      if v not in (None, "", {}, [])]

    def elenco(campo_metadato: str, nome_indice: str) -> list[str] | None:
        if not ammesso(nome_indice):
            return None
        return sorted({str(m[campo_metadato]) for m in metadati if m.get(campo_metadato)})

    date_doc = sorted(str(_iso(m["data_documento"])) for m in metadati
                      if m.get("data_documento")) if ammesso("data_documento") else []
    doc: dict[str, Any] = {
        "aip_id": str(aip["aip_id"]),
        "pdv_id": str(aip.get("pdv_id") or ""),
        "tenant_id": str(aip["tenant_id"]),
        "urn": aip.get("urn"),
        "stato": aip.get("stato"),
        "classe_documentale": aip.get("classe_documentale"),
        "hash_sha256": aip.get("hash_sha256"),
        "anno": chiave.get("anno") if ammesso("anno") else None,
        "numero": chiave.get("numero") if ammesso("numero") else None,
        "registro": chiave.get("registro") if ammesso("registro") else None,
        "data_versamento": _iso(aip.get("data_versamento")),
        "data_conservazione": _iso(aip.get("data_conservazione")),
        "scadenza_conservazione": _iso(aip.get("scadenza_conservazione")),
        "anni_conservazione": aip.get("anni_conservazione"),
        "dimensione_bytes": aip.get("dimensione_bytes"),
        "oggetto": " — ".join(dict.fromkeys(str(x) for x in oggetti if x)) or None,
        "identificativi_documento": elenco("identificativo_documento",
                                           "identificativi_documento"),
        "tipologie_documentali": elenco("tipologia_documentale", "tipologie_documentali"),
        "soggetti_produttori": " ; ".join(produttori) or None,
        "numeri_registrazione": elenco("numero_registrazione", "numeri_registrazione"),
        "data_documento": date_doc[0] if date_doc else None,
        "nomi_file": sorted({str(n) for n in (aip.get("nomi_file") or [])})
        if ammesso("nomi_file") else None,
        "testo_completo": " \n".join(dict.fromkeys(testo)),
    }
    return {k: v for k, v in doc.items() if v is not None}


def ndjson_bulk(indice: str, documenti: Sequence[Mapping[str, Any]]) -> str:
    """Corpo della bulk API: azione «index» con id = id del PdA, routing = ente."""
    righe: list[str] = []
    for d in documenti:
        righe.append(json.dumps({"index": {"_index": indice, "_id": d["aip_id"],
                                           "routing": d["tenant_id"]}}))
        righe.append(json.dumps(d, ensure_ascii=False, default=str))
    return "\n".join(righe) + "\n"


async def indicizza_lotto(documenti: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """
    Indicizza un lotto con una sola chiamata bulk. Restituisce gli errori per
    id del PdA (vuoto = tutto indicizzato); ``RicercaNonDisponibile`` se la
    chiamata intera fallisce (rete, 5xx).
    """
    if not documenti:
        return {}
    client = _client_opensearch()
    try:
        risposta = await client.bulk(ndjson_bulk(settings.opensearch_index, documenti))
    except httpx.HTTPError as exc:
        raise RicercaNonDisponibile(f"bulk non eseguita: {type(exc).__name__}") from exc
    finally:
        await client.chiudi()
    errori: dict[str, str] = {}
    for voce in risposta.get("items") or []:
        esito = voce.get("index") or {}
        if int(esito.get("status", 500)) >= 300:
            errore = esito.get("error")
            motivo = errore.get("type") if isinstance(errore, dict) else str(errore)
            errori[str(esito.get("_id"))] = str(motivo)[:300]
    if risposta.get("errors") and not errori:
        errori = {str(d["aip_id"]): "esito bulk non leggibile" for d in documenti}
    return errori


async def index_aip(aip_doc: dict) -> bool:
    """Compatibilità: indicizza un solo documento (preferire ``indicizza_lotto``)."""
    try:
        return not await indicizza_lotto([aip_doc])
    except RicercaNonDisponibile as exc:
        logger.warning("opensearch_index_failed", errore=str(exc)[:200])
        return False


# ── Ricerca ─────────────────────────────────────────────────────────

def codifica_cursore(valori: Sequence[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(valori)).encode()).decode().rstrip("=")


def decodifica_cursore(cursore: str) -> list[Any]:
    try:
        grezzo = base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4))
        valori = json.loads(grezzo)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CursoreNonValido("cursore non decodificabile") from exc
    if (not isinstance(valori, list) or len(valori) != len(ORDINAMENTO)
            or not isinstance(valori[1], str) or not isinstance(valori[0], (int, float, str))):
        raise CursoreNonValido("cursore di forma non valida")
    return valori


def corpo_ricerca(
    query: str | None,
    tenant_id: str,
    classe_documentale: str | None = None,
    anno: str | None = None,
    skip: int = 0,
    limit: int = 20,
    *,
    data_da: datetime | None = None,
    data_a: datetime | None = None,
    dopo: str | None = None,
) -> dict:
    """Corpo della ricerca. Il filtro sull'ente è OBBLIGATORIO (B9): senza
    tenant_id si solleva ValueError, mai una ricerca su tutti gli enti.

    R03 f2 (REV-06): nessuna paginazione per posizione (``from``: costo che
    cresce con la pagina) né conteggio esatto di tutti i risultati: la pagina
    successiva si chiede con ``dopo`` (``search_after``) e il totale si conta
    fino a ``RICERCA_CONTEGGIO_MASSIMO``. ``skip`` resta nella firma per
    compatibilità ed è ignorato (il router rifiuta ``skip`` > 0)."""
    del skip
    tenant = str(tenant_id or "").strip()
    if not tenant:
        raise ValueError("search_aip: tenant_id obbligatorio (filtro per ente)")
    must: list[dict] = []
    if query:
        # ogni parola deve comparire in almeno uno dei campi (come la vecchia
        # ricerca SQL del portale), non basta una parola qualsiasi
        must.append({"multi_match": {"query": query, "fields": CAMPI_TESTO,
                                     "type": "cross_fields", "operator": "and"}})
    filters: list[dict] = [{"term": {"tenant_id": tenant}}]
    if classe_documentale:
        filters.append({"term": {"classe_documentale": classe_documentale}})
    if anno:
        filters.append({"term": {"anno": anno}})
    intervallo: dict[str, str] = {}
    if data_da:
        intervallo["gte"] = data_da.isoformat()
    if data_a:
        intervallo["lte"] = data_a.isoformat()
    if intervallo:
        filters.append({"range": {"data_versamento": intervallo}})
    corpo: dict[str, Any] = {
        "query": {"bool": {"must": must or [{"match_all": {}}], "filter": filters}},
        "size": limit,
        "sort": ORDINAMENTO,
        "track_total_hits": settings.ricerca_conteggio_massimo,
    }
    if dopo:
        corpo["search_after"] = decodifica_cursore(dopo)
    return corpo


async def search_aip(
    query: str | None = None,
    tenant_id: str | None = None,
    classe_documentale: str | None = None,
    anno: str | None = None,
    skip: int = 0,
    limit: int = 20,
    *,
    data_da: datetime | None = None,
    data_a: datetime | None = None,
    dopo: str | None = None,
) -> dict:
    """Ricerca dei PdA, sempre limitata all'ente indicato.

    Restituisce ``{"total", "total_esatto", "items", "cursore", "altri",
    "campi_testo"}``: ``cursore`` è da passare come ``dopo`` per la pagina
    successiva (None se finita); ``total`` è contato al massimo fino a
    ``RICERCA_CONTEGGIO_MASSIMO`` (``total_esatto`` false se ce ne sono di più).
    ValueError senza tenant_id; CursoreNonValido per un cursore alterato;
    RicercaNonDisponibile se OpenSearch non risponde.
    """
    body = corpo_ricerca(query, tenant_id or "", classe_documentale, anno, skip, limit + 1,
                         data_da=data_da, data_a=data_a, dopo=dopo)
    client = _client_opensearch()
    try:
        resp = await client.cerca(settings.opensearch_index, body, routing=str(tenant_id))
        hits = resp["hits"]
        voci = list(hits.get("hits", []))
        totale = hits.get("total", {})
    except RicercaNonDisponibile:
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        logger.warning("opensearch_search_failed", errore=type(exc).__name__,
                       messaggio=str(exc)[:300])
        raise RicercaNonDisponibile("ricerca non disponibile") from exc
    finally:
        await client.chiudi()
    altri = len(voci) > limit
    voci = voci[:limit]
    cursore = codifica_cursore(voci[-1]["sort"]) if altri and voci and voci[-1].get("sort") \
        else None
    if isinstance(totale, dict):
        conteggio = int(totale.get("value", len(voci)))
        esatto = totale.get("relation", "eq") == "eq"
    else:
        conteggio, esatto = int(totale), True
    massimo = settings.ricerca_conteggio_massimo
    if conteggio >= massimo:
        conteggio, esatto = massimo, False
    return {
        "total": conteggio,
        "total_esatto": esatto,
        "items": [h.get("_source", {}) for h in voci],
        "cursore": cursore,
        "altri": altri,
        "campi_testo": list(CAMPI_TESTO),
    }


async def stato_attuale_dal_db(items: list[dict], tenant_id: str) -> list[dict]:
    """
    Seconda difesa e dato fresco: tiene solo i PdA che nel database sono
    DELL'ENTE (fail-closed) e ne riporta lo stato attuale (lo stato
    nell'indice è quello del momento dell'indicizzazione). Una query sola.
    """
    import uuid

    from sqlalchemy import text

    from libs.shared.db import get_async_session

    ids = []
    for item in items:
        try:
            ids.append(uuid.UUID(str(item.get("aip_id"))))
        except ValueError:
            continue
    if not ids:
        return []
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT id, stato::text FROM public.aip "
            "WHERE id = ANY(:ids) AND tenant_id = CAST(:t AS uuid)"
        ), {"ids": ids, "t": str(tenant_id)})).all()
    stati = {str(r[0]): r[1] for r in righe}
    return [{**i, "stato": stati[str(i.get("aip_id"))]} for i in items
            if str(i.get("aip_id")) in stati]
