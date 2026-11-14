# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/services/scarti.py — code di scarto consultabili e «Rielabora».

Riferimenti: VD-C-075 (R04 servizi); REGOLE_SVILUPPO §12.4 (una DLQ deve essere
consumata: pannello con «Rielabora»), §13 (anteprima e applicazione con impronta
del piano), §12.7 (ordine dei lock), §15.1–15.3 e §15.7 (audit nella stessa
transazione, motivo obbligatorio), §11.9 (scadenze controllate a ogni richiesta).

Lettura delle code: le DLQ Kafka non si leggono dalla richiesta HTTP (un topic
non si interroga per filtro né per pagina). Il processo
``python -m app.services.consumer_scarti`` le CONSUMA e le materializza nella
tabella ``public.scarti`` (una riga per messaggio, chiave unica
``chiave_origine``: rileggere un messaggio non lo duplica), e vi riporta anche le
righe in errore definitivo di ``indicizzazione_aip``. L'API legge solo la
tabella, a cursore sull'id. Motivazione nel rapporto R04 servizi §5.

Forme dei messaggi riconosciute (normalizzate da ``riga_da_messaggio``):

  - pipeline (validation su ``pdv.received.dlq``, aip-builder su
    ``pdv.validated.dlq``): ``{topic_origine, partizione, offset, evento,
    motivo: {codice, messaggio, tipo, tentativi}, registrato_il}``;
  - consumer di notification (``notification.dlq``): ``{original_topic,
    partition, offset, event, error, motivo, retries, failed_at}``;
  - invio della posta di notification (``notification.dlq``): ``{notifica_id,
    tenant_id, tipo, canale, oggetto_id, tentativi, errore, fallita_il}``;
  - altro: registrato con motivo «formato non riconosciuto» (non rielaborabile:
    si archivia con motivo).

«Rielabora» (azione per forma):

  - pipeline: il PdV torna da ERRORE allo stato da cui riparte il servizio che
    ha scartato (RICEVUTO per la verifica, VALIDATO per la costruzione del PdA),
    l'impronta del versamento torna attiva e il messaggio originale si
    ripubblica sul topic d'origine tramite l'outbox, tutto in una transazione.
    I file si rileggono dallo staging: bloccato se le copie di transito sono
    state eliminate o sono scadute (``STAGING_SCARTATI_GIORNI``, VD-P-36), o se
    la stessa unità documentaria è stata versata di nuovo nel frattempo;
  - consumer di notification: ripubblicazione dell'evento sul topic d'origine
    (il consumer è idempotente);
  - invio della posta: la notifica NON_PARTITA torna IN_CODA con tentativi
    azzerati;
  - indicizzazione: la riga ERRORE torna IN_CODA (stessa regola di
    ``rimetti_in_coda`` di distribution, che non è importabile da qui);
  - evento dell'outbox BLOCCATO (R04 correzioni, rev4 rilievo 2; tabella
    ``outbox_chiavi_ferme``, scritta dal relay di ogni servizio dopo
    ``OUTBOX_MAX_TENTATIVI`` errori dell'evento): «Rielabora» = RITENTA (la
    chiave si sblocca e il relay riprova dal primo evento, in ordine);
    «Archivia» = SCARTA l'evento (non sarà mai pubblicato: ``scartato_il``) e
    la chiave riparte dagli eventi successivi. L'ordine si rompe solo per
    quella chiave, in modo dichiarato nell'audit.

Rielaborazione di un PdV (rev4 rilievo 4): un solo evento di ripartenza per
PdV anche con più scarti dello stesso PdV nel piano (stesso messaggio due
volte in DLQ, o in ``pdv.received.dlq`` e in ``pdv.validated.dlq``): nel piano
uno solo è rielaborabile, gli altri sono bloccati «già nel piano»; in
applicazione l'evento parte solo se il PdV passa davvero da ERRORE (lock della
riga) e il vincolo ``scarti_ripartenze`` (una ripartenza per PdV e per episodio
d'errore) lo garantisce anche fra richieste concorrenti.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.config import settings
from libs.shared.errori import ErroreApi
from libs.shared.outbox import accoda_evento
from libs.shared.security.audit import registra_audit

logger = structlog.get_logger(__name__)

#: Origine degli eventi scritti nell'outbox da dashboard-api (relay nel processo
#: ``app.services.consumer_scarti``).
ORIGINE_OUTBOX = "dashboard-api"

DA_GESTIRE, RIELABORATO, ARCHIVIATO = "DA_GESTIRE", "RIELABORATO", "ARCHIVIATO"
STATI = (DA_GESTIRE, RIELABORATO, ARCHIVIATO)

#: Forme dei messaggi (campo ``forma`` della vista).
PIPELINE, EVENTO_NOTIFICA, INVIO_POSTA, INDICIZZAZIONE, SCONOSCIUTA = (
    "pipeline", "evento_notifica", "invio_posta", "indicizzazione", "sconosciuta")

#: Stato da cui il PdV riparte per ogni topic d'origine della pipeline.
RIPARTENZA_PDV = {"pdv.received": "RICEVUTO", "pdv.validated": "VALIDATO"}

CODA_INDICIZZAZIONE = "indicizzazione_aip"
SERVIZIO_INDICIZZAZIONE = "distribution"

#: Eventi dell'outbox bloccati (R04 correzioni): coda «in tabella», un servizio
#: per riga (l'origine dell'evento).
CODA_OUTBOX = "outbox_chiavi_ferme"
SERVIZIO_OUTBOX = "outbox (tutti i servizi)"
OUTBOX_BLOCCATO = "outbox_bloccato"

#: Lunghezze massime salvate (testo dei motivi, messaggi non leggibili).
MAX_MOTIVO = 2000
MAX_GREZZO = 2048


class CursoreNonValido(ValueError):
    """Cursore illeggibile (DSH-104)."""


class PianoCambiato(Exception):
    """L'impronta del piano ricalcolato non coincide con quella confermata."""

    def __init__(self, piano: dict[str, Any]) -> None:
        super().__init__("piano cambiato")
        self.piano = piano


# ── Normalizzazione dei messaggi ─────────────────────────────────────

@dataclass(frozen=True)
class RigaScarto:
    """Una riga di ``public.scarti`` pronta da inserire."""

    chiave_origine: str
    fonte: str
    coda: str
    servizio: str
    motivo: str
    scartato_il: datetime
    messaggio: dict[str, Any]
    topic_origine: str | None = None
    partizione: int | None = None
    posizione: int | None = None
    tenant_id: str | None = None
    pdv_id: str | None = None
    aip_id: str | None = None
    codice: str | None = None
    tentativi: int = 0

    def come_parametri(self) -> dict[str, Any]:
        return {
            "chiave_origine": self.chiave_origine, "fonte": self.fonte, "coda": self.coda,
            "servizio": self.servizio, "motivo": self.motivo[:MAX_MOTIVO],
            "scartato_il": self.scartato_il.isoformat(), "messaggio": self.messaggio,
            "topic_origine": self.topic_origine, "partizione": self.partizione,
            "posizione": self.posizione, "tenant_id": self.tenant_id, "pdv_id": self.pdv_id,
            "aip_id": self.aip_id, "codice": (self.codice or None) and self.codice[:60],
            "tentativi": int(self.tentativi or 0),
        }


def uuid_o_none(valore: Any) -> str | None:
    """UUID in forma canonica, o None (un messaggio scartato può portare di tutto)."""
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError, AttributeError):
        return None


def _data(valore: Any, ripiego: datetime) -> datetime:
    if isinstance(valore, str):
        try:
            data = datetime.fromisoformat(valore)
            return data if data.tzinfo else data.replace(tzinfo=UTC)
        except ValueError:
            return ripiego
    return ripiego


def _intero(valore: Any) -> int:
    try:
        return max(0, int(valore))
    except (TypeError, ValueError):
        return 0


def forma_del_messaggio(messaggio: Mapping[str, Any]) -> str:
    """Forma riconosciuta di un messaggio scartato (vedi docstring del modulo)."""
    if "topic_origine" in messaggio and isinstance(messaggio.get("evento"), Mapping):
        return PIPELINE
    if "original_topic" in messaggio and "event" in messaggio:
        return EVENTO_NOTIFICA
    if "notifica_id" in messaggio:
        return INVIO_POSTA
    if messaggio.get("tabella") == CODA_INDICIZZAZIONE:
        return INDICIZZAZIONE
    if messaggio.get("tabella") == CODA_OUTBOX:
        return OUTBOX_BLOCCATO
    return SCONOSCIUTA


def valore_da_byte(grezzo: bytes | None) -> dict[str, Any]:
    """Valore di un messaggio Kafka: JSON oggetto, altrimenti i primi byte in base64."""
    if grezzo is None:
        return {"__non_decodificabile__": "", "motivo": "messaggio vuoto"}
    try:
        valore = json.loads(grezzo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"__non_decodificabile__": base64.b64encode(grezzo[:MAX_GREZZO]).decode("ascii"),
                "dimensione": len(grezzo)}
    if not isinstance(valore, dict):
        return {"__non_decodificabile__": "", "valore_non_oggetto": str(valore)[:MAX_GREZZO]}
    return valore


def riga_da_messaggio(*, coda: str, servizio: str, partizione: int, posizione: int,
                      valore: dict[str, Any], ricevuto_il: datetime) -> RigaScarto:
    """Riga di scarto da un messaggio di una DLQ Kafka (funzione pura)."""
    base = {"chiave_origine": f"kafka:{coda}:{partizione}:{posizione}", "fonte": "KAFKA",
            "coda": coda, "servizio": servizio, "messaggio": valore,
            "partizione": partizione, "posizione": posizione}
    forma = forma_del_messaggio(valore)
    if forma == PIPELINE:
        evento = valore.get("evento") or {}
        motivo = valore.get("motivo") if isinstance(valore.get("motivo"), Mapping) else {}
        return RigaScarto(
            **base, topic_origine=str(valore.get("topic_origine") or "") or None,
            tenant_id=uuid_o_none(evento.get("tenant_id")), pdv_id=uuid_o_none(
                evento.get("pdv_id")),
            aip_id=uuid_o_none(evento.get("aip_id")), codice=str(
                motivo.get("codice") or "") or None,
            motivo=str(motivo.get("messaggio") or "motivo non indicato"),
            tentativi=_intero(motivo.get("tentativi")),
            scartato_il=_data(valore.get("registrato_il"), ricevuto_il))
    if forma == EVENTO_NOTIFICA:
        evento = valore.get("event") if isinstance(valore.get("event"), Mapping) else {}
        testo = str(valore.get("motivo") or "")
        errore = str(valore.get("error") or "")
        return RigaScarto(
            **base, topic_origine=str(valore.get("original_topic") or "") or None,
            tenant_id=uuid_o_none(evento.get("tenant_id")), pdv_id=uuid_o_none(
                evento.get("pdv_id")),
            aip_id=uuid_o_none(evento.get("aip_id")),
            codice="SCARTO_EVENTO" if errore.startswith("ScartoEvento") else "TECNICO",
            motivo=(f"{testo}: {errore}" if testo and errore and testo not in errore
                    else errore or testo or "motivo non indicato"),
            tentativi=_intero(valore.get("retries")),
            scartato_il=_data(valore.get("failed_at"), ricevuto_il))
    if forma == INVIO_POSTA:
        return RigaScarto(
            **base, tenant_id=uuid_o_none(valore.get("tenant_id")),
            aip_id=uuid_o_none(valore.get("oggetto_id")), codice="NON_PARTITA",
            motivo=str(valore.get("errore") or "invio non riuscito"),
            tentativi=_intero(valore.get("tentativi")),
            scartato_il=_data(valore.get("fallita_il"), ricevuto_il))
    return RigaScarto(**base, motivo="Formato del messaggio non riconosciuto: non rielaborabile",
                      codice="FORMATO", scartato_il=ricevuto_il)


# ── Scrittura idempotente ────────────────────────────────────────────

_INSERISCI = text(
    "INSERT INTO public.scarti (chiave_origine, fonte, coda, topic_origine, partizione, "
    " posizione, servizio, tenant_id, pdv_id, aip_id, codice, motivo, tentativi, scartato_il, "
    " messaggio) "
    "SELECT r.chiave_origine, r.fonte, r.coda, r.topic_origine, r.partizione, r.posizione, "
    " r.servizio, r.tenant_id, r.pdv_id, r.aip_id, r.codice, r.motivo, r.tentativi, "
    " r.scartato_il, r.messaggio "
    "FROM jsonb_to_recordset(CAST(:righe AS jsonb)) AS r(chiave_origine varchar, fonte varchar, "
    " coda varchar, topic_origine varchar, partizione int, posizione bigint, servizio varchar, "
    " tenant_id uuid, pdv_id uuid, aip_id uuid, codice varchar, motivo text, tentativi int, "
    " scartato_il timestamptz, messaggio jsonb) "
    "ON CONFLICT (chiave_origine) DO NOTHING RETURNING id"
)


async def registra(session: Any, righe: Sequence[RigaScarto]) -> int:
    """Inserisce le righe (una query); quelle già presenti si saltano. Restituisce le nuove."""
    if not righe:
        return 0
    nuove = (await session.execute(_INSERISCI, {
        "righe": json.dumps([r.come_parametri() for r in righe], default=str)})).fetchall()
    return len(nuove)


_RILEVA_INDICIZZAZIONE = text(
    "WITH candidate AS ("
    " SELECT i.aip_id, i.tenant_id, i.tentativi, i.ultimo_errore, i.prossimo_tentativo, "
    "  'tabella:indicizzazione_aip:' || i.aip_id::text || ':' "
    "   || to_char(i.prossimo_tentativo AT TIME ZONE 'UTC', 'YYYYMMDD\"T\"HH24MISS.US') AS chiave "
    " FROM public.indicizzazione_aip i WHERE i.stato = 'ERRORE') "
    "INSERT INTO public.scarti (chiave_origine, fonte, coda, servizio, tenant_id, aip_id, codice, "
    " motivo, tentativi, scartato_il, messaggio) "
    "SELECT c.chiave, 'TABELLA', 'indicizzazione_aip', :servizio, c.tenant_id, c.aip_id, "
    " 'INDICIZZAZIONE', COALESCE(left(c.ultimo_errore, 2000), 'indicizzazione non riuscita'), "
    " c.tentativi, c.prossimo_tentativo, jsonb_build_object('tabella', 'indicizzazione_aip', "
    " 'aip_id', c.aip_id, 'tenant_id', c.tenant_id, 'tentativi', c.tentativi) "
    "FROM candidate c "
    "WHERE NOT EXISTS (SELECT 1 FROM public.scarti s WHERE s.chiave_origine = c.chiave) "
    "ORDER BY c.prossimo_tentativo, c.aip_id LIMIT :n "
    "ON CONFLICT (chiave_origine) DO NOTHING RETURNING id"
)


async def rileva_indicizzazione(session: Any, limite: int) -> int:
    """Riporta nelle code di scarto le righe ERRORE di ``indicizzazione_aip`` non ancora
    presenti (indice parziale ``idx_indicizzazione_aip_errore``). Idempotente."""
    return len((await session.execute(_RILEVA_INDICIZZAZIONE, {
        "servizio": SERVIZIO_INDICIZZAZIONE, "n": limite})).fetchall())


# Chiavi BLOCCATE dell'outbox → scarti (una riga per blocco: un nuovo blocco dopo un
# «ritenta» fallito ha un istante diverso e quindi una riga nuova). Join con
# l'evento per l'ente.
_RILEVA_OUTBOX = text(
    "WITH candidate AS ("
    " SELECT f.origine, f.chiave, f.evento_id, f.evento_creato_il, f.topic, f.tentativi, "
    "  f.ultimo_errore, f.bloccato_il, o.tenant_id, "
    "  'tabella:outbox_chiavi_ferme:' || f.origine || ':' || f.evento_id::text || ':' "
    "   || to_char(f.bloccato_il AT TIME ZONE 'UTC', 'YYYYMMDD\"T\"HH24MISS.US') AS chiave_origine "
    " FROM public.outbox_chiavi_ferme f "
    " JOIN public.outbox_eventi o ON o.id = f.evento_id AND o.creato_il = f.evento_creato_il "
    " WHERE f.bloccato_il IS NOT NULL) "
    "INSERT INTO public.scarti (chiave_origine, fonte, coda, topic_origine, servizio, tenant_id, "
    " codice, motivo, tentativi, scartato_il, messaggio) "
    "SELECT c.chiave_origine, 'TABELLA', CAST(:coda AS varchar), c.topic, c.origine, c.tenant_id, "
    " 'OUTBOX_BLOCCATO', COALESCE(left(c.ultimo_errore, 2000), 'pubblicazione non riuscita'), "
    " c.tentativi, c.bloccato_il, jsonb_build_object('tabella', CAST(:coda AS text), "
    " 'origine', c.origine, "
    " 'chiave', c.chiave, 'evento_id', c.evento_id, 'evento_creato_il', c.evento_creato_il, "
    " 'topic', c.topic, 'tentativi', c.tentativi) "
    "FROM candidate c "
    "WHERE NOT EXISTS (SELECT 1 FROM public.scarti s WHERE s.chiave_origine = c.chiave_origine) "
    "ORDER BY c.bloccato_il LIMIT :n "
    "ON CONFLICT (chiave_origine) DO NOTHING RETURNING id"
)


async def rileva_outbox_bloccati(session: Any, limite: int) -> int:
    """Riporta nelle code di scarto le chiavi BLOCCATE dell'outbox di tutti i
    servizi (tabella piccola: solo le chiavi in errore). Idempotente."""
    return len((await session.execute(_RILEVA_OUTBOX, {
        "coda": CODA_OUTBOX, "n": limite})).fetchall())


# ── Lettura ──────────────────────────────────────────────────────────

def cursore_di(scarto_id: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"s": scarto_id}).encode()).decode().rstrip("=")


def leggi_cursore(cursore: str) -> int:
    try:
        dati = json.loads(base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)))
        valore = int(dati["s"])
    except (ValueError, KeyError, TypeError) as exc:
        raise CursoreNonValido(str(exc)) from None
    if valore < 1:
        raise CursoreNonValido("posizione non valida")
    return valore


_COLONNE = (
    "s.id, s.coda, s.servizio, s.fonte, s.topic_origine, s.stato, s.codice, s.motivo, "
    "s.tentativi, s.scartato_il, s.registrato_il, s.tenant_id, s.pdv_id, s.aip_id, "
    "s.gestito_il, s.gestito_da, s.motivo_gestione, s.rielaborazioni, s.partizione, "
    "s.posizione, s.messaggio, t.ragione_sociale AS ente"
)


def _iso(valore: Any) -> str | None:
    return valore.isoformat() if isinstance(valore, datetime) else None


def vista(r: Mapping[str, Any], *, messaggio: bool = False) -> dict[str, Any]:
    """Vista di una riga (senza il contenuto del messaggio, REGOLE §19.9)."""
    corpo = r["messaggio"] if isinstance(r["messaggio"], dict) else {}
    dati: dict[str, Any] = {
        "id": int(r["id"]), "coda": r["coda"], "servizio": r["servizio"], "fonte": r["fonte"],
        "topic_origine": r["topic_origine"], "stato": r["stato"], "codice": r["codice"],
        "motivo": r["motivo"], "tentativi": int(r["tentativi"]),
        "scartato_il": _iso(r["scartato_il"]), "registrato_il": _iso(r["registrato_il"]),
        "tenant_id": str(r["tenant_id"]) if r["tenant_id"] else None,
        "ente": r.get("ente"),
        "pdv_id": str(r["pdv_id"]) if r["pdv_id"] else None,
        "aip_id": str(r["aip_id"]) if r["aip_id"] else None,
        "gestito_il": _iso(r["gestito_il"]),
        "gestito_da": str(r["gestito_da"]) if r["gestito_da"] else None,
        "motivo_gestione": r["motivo_gestione"], "rielaborazioni": int(r["rielaborazioni"]),
        "forma": forma_del_messaggio(corpo),
        "azioni_consentite": ["rielabora", "archivia"] if r["stato"] == DA_GESTIRE else [],
    }
    if messaggio:
        dati["messaggio"] = riepilogo_messaggio(corpo, r)
    return dati


def riepilogo_messaggio(corpo: Mapping[str, Any], r: Mapping[str, Any]) -> dict[str, Any]:
    """Dati tecnici del messaggio senza contenuti (indice, metadati, destinatari):
    il personale del conservatore vede metadati tecnici, non i contenuti (§19.9)."""
    interno = corpo.get("evento") or corpo.get("event")
    return {
        "partizione": r["partizione"], "posizione": r["posizione"],
        "campi": sorted(str(k) for k in corpo)[:50],
        "campi_evento": sorted(str(k) for k in interno)[:50] if isinstance(interno, Mapping)
        else [],
        "dimensione_byte": len(json.dumps(corpo, default=str).encode("utf-8")),
        "leggibile": "__non_decodificabile__" not in corpo,
    }


@dataclass(frozen=True)
class Filtri:
    """Filtri dell'elenco (tutti facoltativi)."""

    stato: str | None = None
    coda: str | None = None
    servizio: str | None = None
    tenant_id: str | None = None
    pdv_id: str | None = None

    def condizioni(self) -> tuple[list[str], dict[str, Any]]:
        cond: list[str] = []
        par: dict[str, Any] = {}
        for nome in ("stato", "coda", "servizio"):
            valore = getattr(self, nome)
            if valore:
                cond.append(f"s.{nome} = :{nome}")
                par[nome] = valore
        for nome in ("tenant_id", "pdv_id"):
            valore = getattr(self, nome)
            if valore:
                cond.append(f"s.{nome} = CAST(:{nome} AS uuid)")
                par[nome] = valore
        return cond, par


async def elenco(session: Any, filtri: Filtri, *, cursore: str | None, limite: int
                 ) -> dict[str, Any]:
    """Pagina dal più recente: ``{items, limit, cursore, altri}`` (niente OFFSET né COUNT)."""
    cond, par = filtri.condizioni()
    if cursore:
        cond.append("s.id < :dopo")
        par["dopo"] = leggi_cursore(cursore)
    dove = ("WHERE " + " AND ".join(cond)) if cond else ""
    righe = (await session.execute(text(
        f"SELECT {_COLONNE} FROM public.scarti s "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
        "LEFT JOIN public.tenants t ON t.id = s.tenant_id "
        f"{dove} ORDER BY s.id DESC LIMIT :n"), {**par, "n": limite + 1})).mappings().all()
    altri = len(righe) > limite
    righe = righe[:limite]
    return {"items": [vista(r) for r in righe], "limit": limite,
            "cursore": cursore_di(int(righe[-1]["id"])) if altri and righe else None,
            "altri": altri}


async def leggi(session: Any, scarto_id: int, *, per_aggiornare: bool = False
                ) -> Mapping[str, Any] | None:
    blocco = " FOR UPDATE OF s" if per_aggiornare else ""
    return (await session.execute(text(
        f"SELECT {_COLONNE} FROM public.scarti s "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
        "LEFT JOIN public.tenants t ON t.id = s.tenant_id "
        f"WHERE s.id = :id{blocco}"), {"id": scarto_id})).mappings().first()


# ── Piano di rielaborazione (REGOLE §13) ─────────────────────────────

@dataclass
class Elemento:
    """Esito del piano per un messaggio scartato."""

    scarto_id: int
    coda: str
    forma: str
    azione: str | None
    rielaborabile: bool
    motivo_blocco: str | None = None
    avvisi: list[str] = field(default_factory=list)
    pdv_id: str | None = None
    tenant_id: str | None = None
    #: Dati interni per l'applicazione (non esposti né nell'impronta)
    interno: dict[str, Any] = field(default_factory=dict, repr=False)

    def pubblico(self) -> dict[str, Any]:
        return {"scarto_id": self.scarto_id, "coda": self.coda, "forma": self.forma,
                "azione": self.azione, "rielaborabile": self.rielaborabile,
                "motivo_blocco": self.motivo_blocco, "avvisi": list(self.avvisi),
                "pdv_id": self.pdv_id, "tenant_id": self.tenant_id}


def _scadenza_staging(data_errore: datetime, giorni: int) -> datetime:
    """R07 (D15): ``giorni`` = STAGING_SCARTATI_GIORNI dell'ente del PdV."""
    return data_errore + timedelta(days=giorni)


def impronta_piano(elementi: Iterable[Elemento], troncato: bool) -> str:
    dati = {"troncato": troncato, "elementi": [e.pubblico() for e in elementi]}
    testo = json.dumps(dati, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()


async def _righe_da_pianificare(session: Any, ids: Sequence[int] | None, filtri: Filtri | None,
                                *, blocca: bool) -> tuple[list[Mapping[str, Any]], bool]:
    massimo = settings.scarti_rielabora_max
    blocco = " FOR UPDATE OF s" if blocca else ""
    if ids:
        righe = (await session.execute(text(
            f"SELECT {_COLONNE} FROM public.scarti s "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
            "LEFT JOIN public.tenants t ON t.id = s.tenant_id "
            f"WHERE s.id = ANY(CAST(:ids AS bigint[])) ORDER BY s.id{blocco}"),
            {"ids": list(ids)})).mappings().all()
        return list(righe), False
    filtri = filtri or Filtri()
    cond, par = Filtri(stato=DA_GESTIRE, coda=filtri.coda, servizio=filtri.servizio,
                       tenant_id=filtri.tenant_id, pdv_id=filtri.pdv_id).condizioni()
    righe = (await session.execute(text(
        f"SELECT {_COLONNE} FROM public.scarti s "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
        "LEFT JOIN public.tenants t ON t.id = s.tenant_id "
        f"WHERE {' AND '.join(cond)} ORDER BY s.id LIMIT :n{blocco}"),
        {**par, "n": massimo + 1})).mappings().all()
    return list(righe[:massimo]), len(righe) > massimo


async def calcola_piano(session: Any, *, ids: Sequence[int] | None, filtri: Filtri | None,
                        adesso: datetime, blocca: bool,
                        solo_pipeline: bool = False) -> dict[str, Any]:
    """Piano completo: cosa si rielabora, cosa è bloccato e perché, avvisi, impronta.

    Non scrive (anteprima); con ``blocca`` prende i lock delle righe di scarto e
    dei PdV coinvolti, in ordine di id (applicazione, §12.7).

    R07 (richiesta del committente): ``solo_pipeline`` = «Rielabora» dell'ente
    sui propri versamenti rifiutati: solo i messaggi della verifica e della
    costruzione del PdA (le altre forme restano al conservatore)."""
    righe, troncato = await _righe_da_pianificare(session, ids, filtri, blocca=blocca)
    if solo_pipeline:
        righe = [r for r in righe if forma_del_messaggio(r["messaggio"]) == PIPELINE]
    trovati = {int(r["id"]) for r in righe}
    elementi: list[Elemento] = [
        Elemento(scarto_id=i, coda="?", forma=SCONOSCIUTA, azione=None, rielaborabile=False,
                 motivo_blocco="messaggio scartato inesistente")
        for i in sorted(set(ids or ()) - trovati)]

    pdv_ids = sorted({str(r["pdv_id"]) for r in righe
                      if r["pdv_id"] and forma_del_messaggio(r["messaggio"]) == PIPELINE})
    pdv = await _pdv_coinvolti(session, pdv_ids, blocca=blocca)
    conflitti = await _conflitti_impronta(session, pdv_ids)
    notifiche = await _stati_notifiche(session, [
        uuid_o_none(r["messaggio"].get("notifica_id")) for r in righe
        if forma_del_messaggio(r["messaggio"]) == INVIO_POSTA])
    indicizzazioni = await _stati_indicizzazione(session, [
        str(r["aip_id"]) for r in righe if r["aip_id"]
        and forma_del_messaggio(r["messaggio"]) == INDICIZZAZIONE])
    outbox = await _stati_outbox(session, [
        r["messaggio"] for r in righe if forma_del_messaggio(r["messaggio"]) == OUTBOX_BLOCCATO],
        blocca=blocca)

    for r in righe:
        elementi.append(_valuta(r, adesso=adesso, pdv=pdv, conflitti=conflitti,
                                notifiche=notifiche, indicizzazioni=indicizzazioni,
                                outbox=outbox))
    elementi.sort(key=lambda e: e.scarto_id)
    _una_ripartenza_per_pdv(elementi)
    rielaborabili = [e for e in elementi if e.rielaborabile]
    avvisi = []
    if troncato:
        avvisi.append(f"Selezione limitata ai primi {settings.scarti_rielabora_max} messaggi: "
                      "gli altri restano da gestire")
    return {
        "elementi": elementi,
        "rielaborabili": len(rielaborabili),
        "bloccati": len(elementi) - len(rielaborabili),
        "avvisi": avvisi,
        "troncato": troncato,
        "chi_sara_avvisato": ("Nessun avviso immediato: i clienti ricevono gli eventi della "
                              "nuova elaborazione (esito della verifica, conservazione) "
                              "come per ogni versamento."),
        "impronta_piano": impronta_piano(elementi, troncato),
    }


def piano_pubblico(piano: Mapping[str, Any]) -> dict[str, Any]:
    return {**piano, "elementi": [e.pubblico() for e in piano["elementi"]]}


async def _pdv_coinvolti(session: Any, pdv_ids: Sequence[str], *, blocca: bool
                         ) -> dict[str, Mapping[str, Any]]:
    if not pdv_ids:
        return {}
    from libs.shared.parametri import SQL_GIORNI_STAGING, parametri_staging

    blocco = " FOR UPDATE OF p" if blocca else ""
    righe = (await session.execute(text(
        "SELECT p.id::text AS id, p.tenant_id::text AS tenant_id, p.stato::text AS stato, "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
        " p.data_aggiornamento, p.staging_eliminato_il, g.giorni FROM public.pdv p "
        + SQL_GIORNI_STAGING +
        f"WHERE p.id = ANY(CAST(:ids AS uuid[])) ORDER BY p.id{blocco}"),
        {"ids": list(pdv_ids), **await parametri_staging()})).mappings().all()
    return {r["id"]: r for r in righe}


async def _conflitti_impronta(session: Any, pdv_ids: Sequence[str]) -> dict[str, str]:
    """PdV (fra quelli indicati) la cui impronta o chiave UD è già attiva in un ALTRO
    PdV dello stesso ente: rielaborarli creerebbe un doppione (VD-C-016, VD-C-095)."""
    if not pdv_ids:
        return {}
    # Join a 2 copie di versamento_impronte: stessa impronta dell'indice o stessa
    # chiave UD, ente uguale, PdV diverso e attivo
    righe = (await session.execute(text(
        "SELECT v.pdv_id::text AS pdv_id, min(a.pdv_id::text) AS altro "
        "FROM public.versamento_impronte v "
        "JOIN public.versamento_impronte a ON a.tenant_id = v.tenant_id "
        " AND a.pdv_id <> v.pdv_id AND a.attivo "
        " AND (a.impronta_indice = v.impronta_indice OR (v.chiave_ud_numero IS NOT NULL "
        "  AND a.chiave_ud_registro IS NOT DISTINCT FROM v.chiave_ud_registro "
        "  AND a.chiave_ud_anno IS NOT DISTINCT FROM v.chiave_ud_anno "
        "  AND a.chiave_ud_numero = v.chiave_ud_numero)) "
        "WHERE v.pdv_id = ANY(CAST(:ids AS uuid[])) GROUP BY v.pdv_id"),
        {"ids": list(pdv_ids)})).mappings().all()
    return {r["pdv_id"]: r["altro"] for r in righe}


async def _stati_notifiche(session: Any, ids: Sequence[str | None]) -> dict[str, str]:
    validi = sorted({i for i in ids if i})
    if not validi:
        return {}
    righe = (await session.execute(text(
        "SELECT id::text AS id, stato FROM public.notifications "
        "WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": validi})).mappings().all()
    return {r["id"]: r["stato"] for r in righe}


async def _stati_indicizzazione(session: Any, ids: Sequence[str]) -> dict[str, str]:
    if not ids:
        return {}
    righe = (await session.execute(text(
        "SELECT aip_id::text AS id, stato FROM public.indicizzazione_aip "
        "WHERE aip_id = ANY(CAST(:ids AS uuid[]))"), {"ids": sorted(set(ids))})).mappings().all()
    return {r["id"]: r["stato"] for r in righe}


async def _stati_outbox(session: Any, messaggi: Sequence[Mapping[str, Any]], *,
                        blocca: bool) -> dict[str, Mapping[str, Any]]:
    """Chiavi ancora BLOCCATE sull'evento indicato (per id dell'evento); con
    ``blocca`` le righe restano bloccate fino al commit (il relay non le tocca:
    una chiave bloccata non si ritenta da sola)."""
    ids = sorted({i for i in (uuid_o_none(m.get("evento_id")) for m in messaggi) if i})
    if not ids:
        return {}
    blocco = " FOR UPDATE" if blocca else ""
    righe = (await session.execute(text(
        "SELECT evento_id::text AS evento_id, origine, chiave, evento_creato_il, topic, "  # noqa: S608 — frammenti SQL costanti
        " tentativi FROM public.outbox_chiavi_ferme "
        "WHERE evento_id = ANY(CAST(:ids AS uuid[])) AND bloccato_il IS NOT NULL "
        f"ORDER BY origine, chiave{blocco}"), {"ids": ids})).mappings().all()
    return {r["evento_id"]: r for r in righe}


#: Ordine di preferenza della ripartenza di un PdV con più scarti nel piano: dalla
#: fase più a monte (la verifica rifà anche la costruzione), poi lo scarto più vecchio.
_FASE = {"pdv.received": 0, "pdv.validated": 1}


def _una_ripartenza_per_pdv(elementi: list[Elemento]) -> None:
    """Un solo elemento rielaborabile per PdV (rev4 rilievo 4); gli altri sono
    bloccati con il motivo. Modifica ``elementi`` sul posto."""
    scelto: dict[str, Elemento] = {}
    for e in sorted((e for e in elementi if e.rielaborabile and e.forma == PIPELINE
                     and e.pdv_id),
                    key=lambda e: (_FASE.get(str(e.interno.get("topic")), 9), e.scarto_id)):
        primo = scelto.setdefault(str(e.pdv_id), e)
        if primo is e:
            continue
        e.rielaborabile = False
        e.azione = None
        e.interno = {}
        e.motivo_blocco = (f"stesso PdV dello scarto {primo.scarto_id}, già nel piano: una "
                           "sola ripartenza per versamento (archiviare questo con un motivo)")


def _valuta(r: Mapping[str, Any], *, adesso: datetime, pdv: Mapping[str, Mapping[str, Any]],
            conflitti: Mapping[str, str], notifiche: Mapping[str, str],
            indicizzazioni: Mapping[str, str],
            outbox: Mapping[str, Mapping[str, Any]] | None = None) -> Elemento:
    """Esito del piano per una riga (motivo di blocco, avvisi, azione)."""
    corpo = r["messaggio"] if isinstance(r["messaggio"], dict) else {}
    forma = forma_del_messaggio(corpo)
    e = Elemento(scarto_id=int(r["id"]), coda=r["coda"], forma=forma, azione=None,
                 rielaborabile=False, pdv_id=str(r["pdv_id"]) if r["pdv_id"] else None,
                 tenant_id=str(r["tenant_id"]) if r["tenant_id"] else None)
    if r["stato"] != DA_GESTIRE:
        e.motivo_blocco = f"messaggio già {r['stato'].lower()}"
        return e
    if forma == PIPELINE:
        return _valuta_pipeline(e, r, corpo, adesso=adesso, pdv=pdv, conflitti=conflitti)
    if forma == EVENTO_NOTIFICA:
        topic = str(corpo.get("original_topic") or "")
        if topic not in settings.scarti_topic_ripubblicabili:
            e.motivo_blocco = f"topic d'origine {topic!r} non ripubblicabile da qui"
            return e
        if not isinstance(corpo.get("event"), dict):
            e.motivo_blocco = "evento originale non leggibile"
            return e
        e.azione = f"ripubblica su {topic}"
        e.rielaborabile = True
        e.interno = {"topic": topic, "payload": corpo["event"],
                     "chiave": str(corpo["event"].get("pdv_id") or corpo["event"].get("id") or "")
                     or None}
        if str(r["codice"]) == "SCARTO_EVENTO":
            e.avvisi.append("Scarto definitivo del consumer (per esempio ente non risolvibile): "
                            "senza correggere la causa l'evento tornerà nella coda di scarto")
        return e
    if forma == INVIO_POSTA:
        nid = uuid_o_none(corpo.get("notifica_id"))
        stato = notifiche.get(nid or "")
        if stato is None:
            e.motivo_blocco = "notifica inesistente"
        elif stato != "NON_PARTITA":
            e.motivo_blocco = f"notifica in stato {stato}: nulla da rielaborare"
        else:
            e.azione = "rimette in coda la notifica"
            e.rielaborabile = True
            e.interno = {"notifica_id": nid}
        return e
    if forma == INDICIZZAZIONE:
        aip = str(r["aip_id"]) if r["aip_id"] else ""
        stato = indicizzazioni.get(aip)
        if stato != "ERRORE":
            e.motivo_blocco = ("indicizzazione inesistente" if stato is None
                               else f"indicizzazione in stato {stato}: nulla da rielaborare")
        else:
            e.azione = "rimette in coda l'indicizzazione"
            e.rielaborabile = True
            e.interno = {"aip_id": aip}
        return e
    if forma == OUTBOX_BLOCCATO:
        stato = (outbox or {}).get(uuid_o_none(corpo.get("evento_id")) or "")
        if stato is None:
            e.motivo_blocco = ("evento non più bloccato: già ritentato con successo o "
                               "scartato; archiviarlo con un motivo")
            return e
        e.azione = (f"ritenta la pubblicazione su {stato['topic']} (la chiave si sblocca e "
                    "riparte dal primo evento, in ordine)")
        e.rielaborabile = True
        e.interno = {"origine": stato["origine"], "chiave": stato["chiave"],
                     "evento_id": str(stato["evento_id"]),
                     "evento_creato_il": stato["evento_creato_il"]}
        e.avvisi.append("Senza correggere la causa (topic, permessi, dimensione del messaggio) "
                        "l'evento tornerà bloccato dopo i ritentativi. «Archivia» lo scarta "
                        "definitivamente e fa ripartire gli eventi successivi della stessa "
                        "chiave")
        return e
    e.motivo_blocco = "formato del messaggio non riconosciuto: archiviarlo con un motivo"
    return e


def _valuta_pipeline(e: Elemento, r: Mapping[str, Any], corpo: Mapping[str, Any], *,
                     adesso: datetime, pdv: Mapping[str, Mapping[str, Any]],
                     conflitti: Mapping[str, str]) -> Elemento:
    topic = str(corpo.get("topic_origine") or "")
    ripartenza = RIPARTENZA_PDV.get(topic)
    evento = corpo.get("evento") if isinstance(corpo.get("evento"), dict) else None
    if ripartenza is None or evento is None:
        e.motivo_blocco = f"topic d'origine {topic!r} senza ripartenza nota"
        return e
    if not e.pdv_id:
        e.motivo_blocco = "il messaggio non indica un PdV valido"
        return e
    riga = pdv.get(e.pdv_id)
    if riga is None:
        e.motivo_blocco = "PdV inesistente"
        return e
    if e.tenant_id and riga["tenant_id"] != e.tenant_id:
        e.motivo_blocco = "l'ente del messaggio non coincide con quello del PdV"
        return e
    if riga["stato"] != "ERRORE":
        e.motivo_blocco = f"PdV in stato {riga['stato']}: nulla da rielaborare"
        return e
    if riga["staging_eliminato_il"] is not None:
        e.motivo_blocco = ("copie di transito eliminate il "
                           f"{riga['staging_eliminato_il'].date().isoformat()}: PdV non "
                           "rielaborabile, il produttore deve versare di nuovo")
        return e
    scade = _scadenza_staging(riga["data_aggiornamento"], int(riga["giorni"]))
    if scade <= adesso:
        e.motivo_blocco = (f"copie di transito scadute il {scade.date().isoformat()} "
                           f"({riga['giorni']} giorni, D15): PdV non rielaborabile")
        return e
    if e.pdv_id in conflitti:
        e.motivo_blocco = ("la stessa unità documentaria o lo stesso indice sono già in un altro "
                           f"versamento attivo (PdV {conflitti[e.pdv_id]}): rielaborare creerebbe "
                           "un doppione")
        return e
    e.azione = f"PdV da ERRORE a {ripartenza} e ripubblica su {topic}"
    e.rielaborabile = True
    e.interno = {"topic": topic, "payload": evento, "stato": ripartenza,
                 "errore_il": riga["data_aggiornamento"]}
    if (scade - adesso) <= timedelta(days=7):
        e.avvisi.append(f"Le copie di transito scadono il {scade.date().isoformat()}")
    motivo = corpo.get("motivo") if isinstance(corpo.get("motivo"), Mapping) else {}
    if motivo.get("tipo") == "permanente":
        e.avvisi.append(f"Errore permanente ({motivo.get('codice')}): senza correggere la causa "
                        "il PdV tornerà nella coda di scarto")
    return e


# ── Applicazione ─────────────────────────────────────────────────────

async def applica(session: Any, *, ids: Sequence[int] | None, filtri: Filtri | None,
                  impronta: str, motivo: str, utente: Mapping[str, Any],
                  adesso: datetime, solo_pipeline: bool = False,
                  tenant_id: str | None = None) -> dict[str, Any]:
    """Ricalcola il piano sotto lock; impronta diversa → ``PianoCambiato`` (nulla
    si scrive); altrimenti applica in questa transazione e registra UNA voce di audit
    (con l'ente, se la rielaborazione è dell'ente)."""
    piano = await calcola_piano(session, ids=ids, filtri=filtri, adesso=adesso, blocca=True,
                                solo_pipeline=solo_pipeline)
    if piano["impronta_piano"] != impronta:
        raise PianoCambiato(piano)
    da_fare = [e for e in piano["elementi"] if e.rielaborabile]
    if not da_fare:
        raise ErroreApi("DSH-422-PIANO", "Nessun messaggio rielaborabile nel piano")
    utente_id = uuid_o_none(utente.get("user_id"))
    for e in da_fare:
        if not await _esegui(session, e, adesso=adesso):
            # Lo stato non è più quello del piano (es. il PdV non è più in ERRORE):
            # nessuna scrittura di questa richiesta resta (rollback del chiamante)
            raise PianoCambiato(piano_pubblico_ricalcolabile(piano))
    await session.execute(text(
        "UPDATE public.scarti SET stato = 'RIELABORATO', gestito_il = :ora, gestito_da = :utente, "
        " motivo_gestione = :motivo, rielaborazioni = rielaborazioni + 1 "
        "WHERE id = ANY(CAST(:ids AS bigint[])) AND stato = 'DA_GESTIRE'"),
        {"ora": adesso, "utente": utente_id, "motivo": motivo,
         "ids": [e.scarto_id for e in da_fare]})
    conteggi: dict[str, int] = {}
    for e in da_fare:
        conteggi[e.coda] = conteggi.get(e.coda, 0) + 1
    dettagli = {
        "rielaborati": len(da_fare), "bloccati": piano["bloccati"], "per_coda": conteggi,
        "scarti": [e.scarto_id for e in da_fare][:200], "motivo": motivo,
        "impronta_piano": impronta, "ruolo": utente.get("ruolo")}
    await registra_audit(session, evento="scarti.rielaborati", user_id=utente_id,
                         tenant_id=tenant_id, dettagli=dettagli)
    logger.info("scarti_rielaborati", rielaborati=len(da_fare), bloccati=piano["bloccati"],
                utente=utente_id)
    return {"rielaborati": len(da_fare), "bloccati": piano["bloccati"],
            "impronta_piano": impronta, "elementi": [e.pubblico() for e in piano["elementi"]]}


def piano_pubblico_ricalcolabile(piano: dict[str, Any]) -> dict[str, Any]:
    """Il piano con impronta azzerata: il client deve ricalcolarlo (anteprima)."""
    return {**piano, "impronta_piano": ""}


async def _esegui(session: Any, e: Elemento, *, adesso: datetime) -> bool:
    """Applica un elemento; False se lo stato non è più quello del piano."""
    dati = e.interno
    if e.forma == PIPELINE:
        # Vincolo: una sola ripartenza per PdV e per episodio d'errore (istante in
        # cui il PdV è entrato in ERRORE), anche fra richieste concorrenti
        nuova = (await session.execute(text(
            "INSERT INTO public.scarti_ripartenze (pdv_id, errore_il, scarto_id, creato_il) "
            "VALUES (CAST(:id AS uuid), :errore_il, :scarto, :ora) "
            "ON CONFLICT (pdv_id, errore_il) DO NOTHING RETURNING pdv_id"),
            {"id": e.pdv_id, "errore_il": dati["errore_il"], "scarto": e.scarto_id,
             "ora": adesso})).first()
        if nuova is None:
            return False
        aggiornati = await session.execute(text(
            "UPDATE public.pdv SET stato = CAST(:stato AS public.pdv_stato), "
            " data_aggiornamento = :ora, staging_avvisato_il = NULL "
            "WHERE id = CAST(:id AS uuid) AND stato = 'ERRORE'"),
            {"stato": dati["stato"], "ora": adesso, "id": e.pdv_id})
        if aggiornati.rowcount != 1:
            return False  # l'evento di ripartenza parte solo se il PdV è ripartito davvero
        await session.execute(text(
            "UPDATE public.versamento_impronte SET attivo = true WHERE pdv_id = CAST(:id AS uuid)"),
            {"id": e.pdv_id})
        await accoda_evento(session, topic=dati["topic"], chiave=e.pdv_id, payload=dati["payload"],
                            tenant_id=e.tenant_id, origine=ORIGINE_OUTBOX)
    elif e.forma == OUTBOX_BLOCCATO:
        sbloccata = await session.execute(text(
            "DELETE FROM public.outbox_chiavi_ferme WHERE origine = :origine "
            "AND chiave = :chiave AND evento_id = CAST(:evento AS uuid) "
            "AND bloccato_il IS NOT NULL"),
            {"origine": dati["origine"], "chiave": dati["chiave"], "evento": dati["evento_id"]})
        if sbloccata.rowcount != 1:
            return False
        await session.execute(text(
            "UPDATE public.outbox_eventi SET tentativi = 0, ultimo_errore = NULL "
            "WHERE id = CAST(:evento AS uuid) AND creato_il = :creato AND pubblicato_il IS NULL"),
            {"evento": dati["evento_id"], "creato": dati["evento_creato_il"]})
    elif e.forma == EVENTO_NOTIFICA:
        await accoda_evento(session, topic=dati["topic"], chiave=dati["chiave"],
                            payload=dati["payload"], tenant_id=e.tenant_id, origine=ORIGINE_OUTBOX)
    elif e.forma == INVIO_POSTA:
        await session.execute(text(
            "UPDATE public.notifications SET stato = 'IN_CODA', tentativi = 0, errore = NULL, "
            " prossimo_tentativo = :ora WHERE id = CAST(:id AS uuid) AND stato = 'NON_PARTITA'"),
            {"ora": adesso, "id": dati["notifica_id"]})
    elif e.forma == INDICIZZAZIONE:
        # Stessa regola di distribution app/services/indicizzazione.py rimetti_in_coda
        await session.execute(text(
            "UPDATE public.indicizzazione_aip SET stato = 'IN_CODA', tentativi = 0, "
            " prossimo_tentativo = :ora WHERE aip_id = CAST(:id AS uuid) AND stato = 'ERRORE'"),
            {"ora": adesso, "id": dati["aip_id"]})
    return True


async def _scarta_evento_outbox(session: Any, corpo: Mapping[str, Any], *, motivo: str,
                                adesso: datetime) -> dict[str, Any]:
    """«Archivia» di un evento bloccato dell'outbox: l'evento è SCARTATO (non sarà
    mai pubblicato) e la chiave si sblocca, così gli eventi successivi della
    stessa chiave ripartono. Se la chiave non è più bloccata su quell'evento
    (già ritentato o scartato) non si tocca l'outbox."""
    evento = uuid_o_none(corpo.get("evento_id"))
    stato = (await _stati_outbox(session, [corpo], blocca=True)).get(evento or "")
    if stato is None:
        return {"evento_outbox": evento, "evento_scartato": False}
    await session.execute(text(
        "UPDATE public.outbox_eventi SET pubblicato_il = :ora, scartato_il = :ora, "
        " ultimo_errore = left('scartato dalle code di scarto: ' || :motivo, 500) "
        "WHERE id = CAST(:evento AS uuid) AND creato_il = :creato AND pubblicato_il IS NULL"),
        {"ora": adesso, "motivo": motivo, "evento": evento,
         "creato": stato["evento_creato_il"]})
    await session.execute(text(
        "DELETE FROM public.outbox_chiavi_ferme WHERE origine = :origine AND chiave = :chiave "
        "AND evento_id = CAST(:evento AS uuid)"),
        {"origine": stato["origine"], "chiave": stato["chiave"], "evento": evento})
    logger.warning("outbox_evento_scartato", origine=stato["origine"], evento_id=evento,
                   topic=stato["topic"])
    return {"evento_outbox": evento, "evento_scartato": True, "origine": stato["origine"],
            "topic": stato["topic"], "ordine_interrotto_per_la_chiave": stato["chiave"]}


async def archivia(session: Any, scarto_id: int, *, motivo: str, utente: Mapping[str, Any],
                   adesso: datetime) -> dict[str, Any]:
    """DA_GESTIRE → ARCHIVIATO con motivo e audit (stato atteso: DA_GESTIRE, §12.5)."""
    riga = await leggi(session, scarto_id, per_aggiornare=True)
    if riga is None:
        raise ErroreApi("DSH-404-SCARTO")
    if riga["stato"] != DA_GESTIRE:
        raise ErroreApi("DSH-409-SCARTO", f"Messaggio già {riga['stato'].lower()}",
                        dettagli={"stato": riga["stato"]})
    utente_id = uuid_o_none(utente.get("user_id"))
    corpo = riga["messaggio"] if isinstance(riga["messaggio"], dict) else {}
    extra: dict[str, Any] = {}
    if forma_del_messaggio(corpo) == OUTBOX_BLOCCATO:
        extra = await _scarta_evento_outbox(session, corpo, motivo=motivo, adesso=adesso)
    await session.execute(text(
        "UPDATE public.scarti SET stato = 'ARCHIVIATO', gestito_il = :ora, gestito_da = :utente, "
        " motivo_gestione = :motivo WHERE id = :id"),
        {"ora": adesso, "utente": utente_id, "motivo": motivo, "id": scarto_id})
    await registra_audit(session, evento="scarti.archiviato", user_id=utente_id,
                         tenant_id=str(riga["tenant_id"]) if riga["tenant_id"] else None,
                         dettagli={"scarto_id": scarto_id, "coda": riga["coda"],
                                   "pdv_id": str(riga["pdv_id"]) if riga["pdv_id"] else None,
                                   "motivo": motivo, "ruolo": utente.get("ruolo"), **extra})
    dopo = await leggi(session, scarto_id)
    assert dopo is not None
    return vista(dopo, messaggio=True)
