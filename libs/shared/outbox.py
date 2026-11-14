# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/outbox.py — outbox transazionale condiviso dai servizi.

Riferimenti: VD-C-015 (R02), VD-C-076 (R04); REGOLE_SVILUPPO §12.3; piano §4.2.
Tabella ``public.outbox_eventi`` partizionata per giorno (migrazione
20261023_028). Ogni servizio che scrive eventi ha il PROPRIO relay e pubblica
SOLO i propri eventi (colonna ``origine``): nessun servizio dipende più dal
gateway per far partire i suoi eventi.

  - ``accoda_evento(session, ...)``: INSERT nella transazione del chiamante.
    Se la transazione fallisce, l'evento non esiste; se riesce, l'evento è
    durevole anche con Kafka giù. ``origine`` = servizio che scrive (vedi
    ``origine_corrente``); ``gruppo`` = funzione della chiave (vedi sotto).
  - ``pubblica_dopo_commit()``: tentativo immediato, dopo il commit, di
    pubblicare gli eventi in sospeso del servizio (best effort, non solleva mai).
  - ``pubblica_in_sospeso(invia)``: un giro del relay (vedi «Ordine»).
  - ``relay_outbox(...)``: ciclo continuo; ``avvia_relay_servizio(origine)`` lo
    avvia come task del processo con la manutenzione del database.

ORDINE PER CHIAVE (stessa chiave → nessun sorpasso)
  Ogni evento ha un ``gruppo`` = ``hashtext(chiave) & 63`` (64 gruppi; senza
  chiave: dall'id, cioè nessun vincolo d'ordine). Un giro del relay:
    1. fra i gruppi che hanno ALMENO un evento pubblicabile (una sonda per
       gruppo sull'indice ``(origine, gruppo, sequenza)`` delle righe in attesa:
       nessuna «finestra» degli eventi più vecchi, un arretrato enorme in un
       gruppo non ferma gli altri) prende in esclusiva fino a
       ``OUTBOX_GRUPPI_PER_GIRO`` gruppi liberi con lock consultivi di
       TRANSAZIONE (``pg_try_advisory_xact_lock``, chiave = origine + gruppo;
       ordine casuale, così le repliche si dividono i gruppi);
    2. legge fino a ``OUTBOX_LOTTO`` eventi in attesa di quei gruppi, esclusi
       quelli delle chiavi FERME (vedi sotto), A TURNO fra i gruppi (un gruppo
       con molto arretrato non riempie il lotto da solo) e in ordine di
       ``sequenza`` dentro ogni gruppo, con ``FOR UPDATE SKIP LOCKED``;
    3. pubblica: gruppi in parallelo, eventi di un gruppo in serie e in ordine
       di sequenza. Errore DI CONNESSIONE (broker giù, timeout): il gruppo si
       ferma per questo giro. Errore DEL TOPIC (permesso negato sul topic o sul
       cluster, topic inesistente o non valido): si sospende il TOPIC (vedi
       «TOPIC SOSPESI»). Errore DELL'EVENTO (messaggio troppo grande, payload
       non serializzabile…): si ferma SOLO la sua chiave (gli eventi successivi
       della stessa chiave NON partono prima di quello fallito), le altre chiavi
       del gruppo proseguono;
    4. marca i pubblicati con UNA UPDATE e chiude la transazione (lock rilasciati).

CHIAVI FERME E BLOCCATE (R04 correzioni, rev4 rilievo 2)
  Al primo errore dell'evento la sua chiave entra in ``outbox_chiavi_ferme``
  (tentativi, ultimo errore, prossimo tentativo): da quel momento i suoi eventi
  sono esclusi dalle letture normali, così non occupano il lotto né la sonda
  dei gruppi. Ogni giro ritenta il PRIMO evento delle chiavi ferme la cui pausa
  è scaduta (``OUTBOX_RIPROVA_SECONDI`` raddoppiata fino a
  ``OUTBOX_RIPROVA_MASSIMA_SECONDI``): riuscito → la chiave riparte, in ordine.
  Dopo ``OUTBOX_MAX_TENTATIVI`` errori dell'evento la chiave è BLOCCATA
  (``bloccato_il``): log ``outbox_evento_bloccato``, metriche
  ``verumdoc_outbox_bloccati`` e ``verumdoc_outbox_eventi_bloccati_total``; la
  console delle code di scarto (dashboard-api) la mostra con «Rielabora» (=
  ritenta) e «Archivia» (= scarta l'evento con motivo: l'ordine si rompe solo
  per quella chiave, in modo dichiarato e registrato nell'audit). Gli errori di
  connessione non contano come tentativi: con Kafka giù nessun evento si blocca.
  Garanzia: per ogni chiave gli eventi escono nell'ordine di ``sequenza``,
  purché eventi della stessa chiave siano scritti da transazioni serializzate
  sulla risorsa (lock della riga del padre prima di ``accoda_evento``, REGOLE
  §12.6–12.7): è così per tutti gli scrittori attuali (PdV, sessione di
  caricamento, ente, PdD, checkpoint). Il numero di gruppi è fisso: cambiarlo
  con eventi in attesa sposterebbe chiavi fra gruppi (vedi ``NUMERO_GRUPPI``).
  Consegna «almeno una volta» (crash fra invio e UPDATE): i consumer sono
  idempotenti (VD-P-19).

TOPIC SOSPESI (R04 secondo giro, N1, migrazione 20261023_033)
  Un errore del topic vale per TUTTE le chiavi: trattarlo come errore della
  chiave fermava ogni chiave una per una e il recupero procedeva a 20 riprove
  per giro (3.000 chiavi: 300 s; un'ora di errore: 70 minuti di ritardo). Ora il
  topic entra in ``outbox_topic_sospesi`` (per origine, condivisa fra le
  repliche) per ``OUTBOX_PAUSA_TOPIC_SECONDI`` (2), raddoppiata a ogni nuovo
  errore fino a ``OUTBOX_PAUSA_TOPIC_MASSIMA_SECONDI`` (30): nel frattempo i
  suoi eventi non si leggono né si tentano, nessuna chiave si ferma e nessun
  tentativo si conta; le chiavi con un evento precedente sul topic sospeso non
  avanzano sugli altri topic (ordine per chiave). Allo scadere il topic è «in
  prova»: si tentano i suoi eventi; un invio riuscito lo ripristina (log
  ``outbox_topic_ripristinato``, le chiavi ferme di quel topic si ritentano
  subito) e l'arretrato riparte a lotto pieno (3.000 chiavi in 6 giri). Allarme
  specifico: log ``outbox_topic_sospeso``, metriche ``verumdoc_outbox_topic_sospesi``
  e ``verumdoc_outbox_topic_sospensioni_total``, regola ``OutboxTopicSospeso``.
  Le riprove delle chiavi ferme per giro sono ``max(OUTBOX_RIPROVE_PER_GIRO,
  OUTBOX_LOTTO / 5)``.

PUBBLICAZIONE DOPO IL COMMIT (N2): ``pubblica_dopo_commit`` (nella richiesta HTTP
del versamento, onboarding, distribution) pubblica solo eventi nuovi, MAI i
ritentativi delle chiavi ferme: quelli li fa solo il relay, fuori dalla latenza
del cliente.

KAFKA GIÙ (R04 integrazione, prova di carico di r04-infra): CORTOCIRCUITO per
origine. Al primo giro in cui nessun evento parte e almeno un invio fallisce per
un errore DI CONNESSIONE (broker giù o producer non attivo; mai per un errore
del singolo evento, che ferma solo la sua chiave) il circuito si apre per
``OUTBOX_PAUSA_KAFKA_SECONDI`` (raddoppia a ogni
riprova fallita fino a ``OUTBOX_PAUSA_KAFKA_MASSIMA_SECONDI``): nel frattempo
``pubblica_in_sospeso`` (e quindi ``pubblica_dopo_commit`` dopo ogni versamento)
esce subito, senza DB, senza tentativi e senza log. Allo scadere UN giro fa da
prova: riuscito → circuito chiuso (log ``outbox_kafka_ripristinato``), fallito →
pausa doppia. Un solo avviso (``outbox_kafka_non_disponibile``) per apertura,
non uno per evento (prima: 21.933 righe di log in 4,5 minuti). Un producer non
partito all'avvio si ritenta (``libs.shared.kafka.riprova_producer``).

PULIZIA: le righe pubblicate restano ``RETENZIONE_OUTBOX_GIORNI`` (predefinito
3) per la diagnosi; poi la partizione del giorno si stacca e si elimina
(``libs.shared.db.partizioni``), mai DELETE di massa. Una partizione con eventi
ancora in attesa non si stacca.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import DateTime

from libs.shared.db import get_async_session
from libs.shared.db import metriche as metriche_db
from libs.shared.kafka import KafkaNonDisponibile, kafka_produce

logger = structlog.get_logger(__name__)

Invio = Callable[[str, str | None, dict[str, Any]], Awaitable[None]]

__all__ = ["NUMERO_GRUPPI", "ConfigRelay", "Invio", "KafkaNonDisponibile", "OrigineNonConfigurata",
           "Cortocircuito", "RelayServizio", "accoda_evento", "avvia_relay_servizio",
           "configura_origine", "cortocircuito", "errore_del_topic", "errore_di_connessione",
           "ferma_relay_servizio", "invia_kafka", "misura_arretrato", "origine_corrente",
           "pubblica_dopo_commit", "pubblica_in_sospeso", "relay_outbox"]

#: Gruppi d'ordine. Costante tecnica (non normativa): cambiarla richiede outbox
#: vuota (nessun evento in attesa), altrimenti chiavi in attesa cambiano gruppo.
NUMERO_GRUPPI = 64
_MASCHERA_GRUPPO = NUMERO_GRUPPI - 1

#: Tetto del conteggio dell'arretrato (oltre si riporta il tetto: la misura
#: resta di costo limitato anche con Kafka fermo da ore).
TETTO_ARRETRATO = 1_000_000


class ConfigRelay(BaseSettings):
    """Configurazione del relay (variabili d'ambiente ``OUTBOX_*``).

    OUTBOX_RELAY_ATTIVO=false quando il relay gira come processo dedicato
    (``python -m app.services.outbox`` nel gateway, o l'analogo del servizio);
    più relay dello stesso servizio insieme sono previsti (gruppi e SKIP LOCKED).
    """
    model_config = SettingsConfigDict(env_prefix="OUTBOX_", case_sensitive=False)
    relay_attivo: bool = True
    intervallo_secondi: float = 2.0
    lotto: int = 500
    gruppi_per_giro: int = 16
    misura_ogni_secondi: float = 15.0
    #: Facoltativa: forza l'origine (di norma = nome del servizio).
    origine: str = ""
    #: Cortocircuito con Kafka giù (R04): prima pausa e pausa massima fra le prove.
    pausa_kafka_secondi: float = 2.0
    pausa_kafka_massima_secondi: float = 60.0
    #: R04 correzioni (rev4 rilievo 2): un evento che fallisce per colpa sua
    #: (non di Kafka) ferma SOLO la sua chiave; si ritenta con pausa crescente
    #: (``riprova_secondi`` raddoppiata fino a ``riprova_massima_secondi``) e dopo
    #: ``max_tentativi`` la chiave è BLOCCATA (allarme, code di scarto).
    max_tentativi: int = 10
    riprova_secondi: float = 2.0
    riprova_massima_secondi: float = 300.0
    #: Ritentativi di chiavi ferme per giro (almeno ``lotto // 5``)
    riprove_per_giro: int = 20
    #: Sospensione di un topic per errori del topic (N1): prima attesa e massima
    pausa_topic_secondi: float = 2.0
    pausa_topic_massima_secondi: float = 30.0


@dataclass
class Cortocircuito:
    """Stato del cortocircuito verso Kafka di un'origine (vedi docstring del modulo).
    ``orologio`` iniettabile per i test."""
    pausa_iniziale: float = 2.0
    pausa_massima: float = 60.0
    orologio: Callable[[], float] = time.monotonic
    aperto_fino: float = 0.0
    pausa: float = 0.0
    fallimenti: int = 0
    saltati: int = 0

    def aperto(self) -> bool:
        if self.orologio() < self.aperto_fino:
            self.saltati += 1
            return True
        return False

    def fallimento(self, origine: str, errore: BaseException | str, eventi: int) -> None:
        self.fallimenti += 1
        self.pausa = min(self.pausa_massima,
                         self.pausa_iniziale if self.pausa <= 0 else self.pausa * 2)
        self.aperto_fino = self.orologio() + self.pausa
        logger.warning("outbox_kafka_non_disponibile", origine=origine,
                       tentativo=self.fallimenti, eventi_in_attesa_nel_giro=eventi,
                       prossima_prova_secondi=self.pausa, giri_saltati=self.saltati,
                       errore=str(errore)[:200])
        metriche_db.imposta("verumdoc_outbox_kafka_cortocircuito", 1, origine=origine)

    def successo(self, origine: str) -> None:
        if self.fallimenti:
            logger.info("outbox_kafka_ripristinato", origine=origine,
                        tentativi_falliti=self.fallimenti, giri_saltati=self.saltati)
            metriche_db.imposta("verumdoc_outbox_kafka_cortocircuito", 0, origine=origine)
        self.aperto_fino = self.pausa = 0.0
        self.fallimenti = self.saltati = 0


_cortocircuiti: dict[tuple[str, int, int], Cortocircuito] = {}


def cortocircuito(origine: str, invia: Any = None, producer: Any = None) -> Cortocircuito:
    """Cortocircuito per (origine, funzione d'invio, producer): un producer nuovo
    (riavviato da ``riprova_producer``) o un invio diverso partono a circuito
    chiuso. Creato alla prima richiesta con i valori di ``ConfigRelay``."""
    chiave = (origine, id(invia), id(producer))
    c = _cortocircuiti.get(chiave)
    if c is None:
        if len(_cortocircuiti) > 64:  # producer e invii cambiati molte volte: si riparte
            _cortocircuiti.clear()
        cfg = ConfigRelay()
        c = _cortocircuiti[chiave] = Cortocircuito(cfg.pausa_kafka_secondi,
                                                    cfg.pausa_kafka_massima_secondi)
    return c


class OrigineNonConfigurata(RuntimeError):
    """Nessuna origine per gli eventi di questo processo: l'evento non si scrive
    (finirebbe senza un relay che lo pubblichi)."""


_origine: str | None = None


def configura_origine(nome: str) -> None:
    """Origine degli eventi scritti da questo processo (= nome del servizio)."""
    global _origine
    nome = (nome or "").strip()
    if not nome or len(nome) > 40:
        raise ValueError("origine dell'outbox: nome del servizio fra 1 e 40 caratteri")
    _origine = nome


def origine_corrente() -> str:
    """Origine del processo: ``configura_origine`` (chiamata dall'avvio del relay),
    poi ``OUTBOX_ORIGINE``, poi ``app.config.settings.service_name`` del servizio
    (tutti i servizi lo dichiarano). Senza nessuna delle tre:
    ``OrigineNonConfigurata``."""
    if _origine:
        return _origine
    da_ambiente = os.environ.get("OUTBOX_ORIGINE", "").strip()
    if da_ambiente:
        return da_ambiente
    try:
        from app.config import settings  # type: ignore[import-not-found]

        nome = str(getattr(settings, "service_name", "") or "").strip()
    except Exception:  # noqa: BLE001 — processo senza app.config (strumenti, test di libreria)
        nome = ""
    if nome:
        return nome
    raise OrigineNonConfigurata(
        "origine dell'outbox non configurata: chiama configura_origine() o imposta OUTBOX_ORIGINE")


def _chiave_lock(origine: str) -> int:
    """Prima metà (int4) della chiave del lock consultivo dei gruppi di un'origine."""
    return int.from_bytes(uuid.uuid5(uuid.NAMESPACE_URL, f"verumdoc/outbox/{origine}").bytes[:4],
                          "big", signed=True)


async def accoda_evento(
    session: Any,
    *,
    topic: str,
    chiave: str | None,
    payload: dict[str, Any],
    tenant_id: str | uuid.UUID | None = None,
    origine: str | None = None,
) -> uuid.UUID:
    """Scrive l'evento in outbox nella transazione aperta di ``session``."""
    evento_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO public.outbox_eventi "
            "(id, origine, gruppo, tenant_id, topic, chiave, payload) "
            "VALUES (:id, :origine, "
            "        (hashtext(COALESCE(CAST(:chiave AS text), :id_testo)) & :maschera)::smallint, "
            "        :tenant, :topic, :chiave, CAST(:payload AS jsonb))"
        ),
        {
            "id": evento_id,
            "id_testo": str(evento_id),
            "origine": origine or origine_corrente(),
            "maschera": _MASCHERA_GRUPPO,
            "tenant": uuid.UUID(str(tenant_id)) if tenant_id else None,
            "topic": topic,
            "chiave": chiave,
            "payload": json.dumps(payload, default=str),
        },
    )
    return evento_id


async def invia_kafka(topic: str, chiave: str | None, payload: dict[str, Any]) -> None:
    """
    Invio con il producer Kafka del processo (``init_kafka_producer``). Se il
    producer non è attivo ``kafka_produce`` solleva ``KafkaNonDisponibile``:
    l'evento resta in outbox.
    """
    await kafka_produce(topic=topic, key=chiave, value=payload)


# «Chiave d'ordine» di un evento: la chiave, o l'id se l'evento non ne ha (nessun
# vincolo d'ordine). È anche la chiave di ``outbox_chiavi_ferme``.
_CHIAVE_ORDINE = "COALESCE(o.chiave, o.id::text)"
_NON_FERMA = (
    "NOT EXISTS (SELECT 1 FROM public.outbox_chiavi_ferme f "  # noqa: S608 — frammenti SQL costanti
    f"WHERE f.origine = o.origine AND f.chiave = {_CHIAVE_ORDINE}) "
    # R04 correzioni, secondo giro (N1): esclusi gli eventi dei topic SOSPESI
    "AND o.topic <> ALL(CAST(:sospesi AS varchar[]))"
)

# Gruppi candidati = i gruppi (0..63) che hanno ALMENO UN evento pubblicabile
# (in attesa e di una chiave non ferma): una sonda per gruppo sull'indice
# ``idx_outbox_gruppi_in_sospeso`` (origine, gruppo, sequenza) delle righe in
# attesa. Nessuna «finestra» degli eventi più vecchi: un gruppo con un
# arretrato enorme non toglie il posto agli altri (rev4 rilievo 2). Ordine
# casuale, lock consultivo di TRANSAZIONE tentato riga per riga. Le CTE
# MATERIALIZED impediscono al pianificatore di spingere la condizione con il
# lock sotto l'ordinamento (altrimenti si bloccherebbero TUTTI i candidati).
_PRENDI_GRUPPI = text(
    "WITH attivi AS MATERIALIZED ("  # noqa: S608 — frammenti SQL costanti
    "  SELECT g FROM generate_series(0, :ultimo) AS g WHERE EXISTS ("
    "    SELECT 1 FROM public.outbox_eventi o "
    "    WHERE o.origine = :origine AND o.gruppo = g AND o.pubblicato_il IS NULL "
    f"    AND {_NON_FERMA})"
    "), mescolati AS MATERIALIZED (SELECT g FROM attivi ORDER BY random()) "
    "SELECT g FROM mescolati WHERE pg_try_advisory_xact_lock(:k, g) LIMIT :quanti"
)
# Lettura EQUA fra i gruppi presi: per ogni gruppo i primi eventi pubblicabili
# (indice per gruppo), poi a turno (1° di ogni gruppo, 2° di ogni gruppo…) fino al
# lotto: un gruppo con un arretrato enorme non riempie il lotto da solo. Il lock
# delle righe (SKIP LOCKED) si prende solo sulle righe scelte.
_LEGGI = text(
    "WITH scelti AS MATERIALIZED ("  # noqa: S608 — frammenti SQL costanti
    "  SELECT x.id, x.creato_il FROM unnest(CAST(:gruppi AS smallint[])) AS g(gruppo) "
    "  CROSS JOIN LATERAL ("
    "    SELECT o.id, o.creato_il, o.sequenza, "
    "           row_number() OVER (ORDER BY o.sequenza) AS turno "
    "    FROM public.outbox_eventi o "
    "    WHERE o.origine = :origine AND o.pubblicato_il IS NULL AND o.gruppo = g.gruppo "
    f"    AND {_NON_FERMA} ORDER BY o.sequenza LIMIT :limite) x "
    "  ORDER BY x.turno, x.sequenza LIMIT :limite) "
    "SELECT o.id, o.creato_il, o.sequenza, o.gruppo, o.topic, o.chiave, o.payload, "
    f"{_CHIAVE_ORDINE} AS chiave_ordine "
    "FROM public.outbox_eventi o JOIN scelti s ON o.id = s.id AND o.creato_il = s.creato_il "
    "WHERE o.pubblicato_il IS NULL "
    "ORDER BY o.sequenza FOR UPDATE OF o SKIP LOCKED"
)
# Le chiavi (id, creato_il) portano la chiave di partizione: l'UPDATE tocca solo
# le partizioni dei giorni coinvolti.
_MARCA_PUBBLICATI = text(
    "UPDATE public.outbox_eventi o SET pubblicato_il = now(), tentativi = o.tentativi + 1, "
    "ultimo_errore = NULL "
    "FROM unnest(CAST(:ids AS uuid[]), :creati) AS p(id, creato_il) "
    "WHERE o.id = p.id AND o.creato_il = p.creato_il"
).bindparams(bindparam("creati", type_=ARRAY(DateTime(timezone=True))))
# Errore di connessione (Kafka giù): l'evento non è «colpevole», nessun tentativo contato
_MARCA_ERRORE_CONNESSIONE = text(
    "UPDATE public.outbox_eventi SET ultimo_errore = :errore "
    "WHERE id = :id AND creato_il = :creato_il"
)
# Errore dell'evento (topic inesistente, messaggio troppo grande, non serializzabile,
# permesso negato…): tentativo contato e chiave FERMA (le altre chiavi proseguono);
# oltre ``max`` tentativi la chiave è BLOCCATA (allarme, code di scarto).
_FERMA_CHIAVE = text(
    "WITH ev AS (UPDATE public.outbox_eventi SET tentativi = tentativi + 1, "
    "  ultimo_errore = :errore WHERE id = :id AND creato_il = :creato_il "
    "  RETURNING tentativi) "
    "INSERT INTO public.outbox_chiavi_ferme AS f (origine, chiave, gruppo, evento_id, "
    "  evento_creato_il, sequenza, topic, tentativi, ultimo_errore, prossimo_tentativo_il, "
    "  bloccato_il) "
    "SELECT :origine, :chiave, :gruppo, :id, :creato_il, :sequenza, :topic, ev.tentativi, "
    "  :errore, CAST(:adesso AS timestamptz) + make_interval(secs => LEAST("
    "    CAST(:pausa_massima AS float8), CAST(:pausa AS float8) "
    "    * power(2, LEAST(GREATEST(ev.tentativi - 1, 0), 30)))), "
    "  CASE WHEN ev.tentativi >= :massimo THEN CAST(:adesso AS timestamptz) END FROM ev "
    "ON CONFLICT (origine, chiave) DO UPDATE SET tentativi = EXCLUDED.tentativi, "
    "  ultimo_errore = EXCLUDED.ultimo_errore, "
    "  prossimo_tentativo_il = EXCLUDED.prossimo_tentativo_il, "
    "  bloccato_il = COALESCE(f.bloccato_il, EXCLUDED.bloccato_il) "
    "WHERE f.evento_id = EXCLUDED.evento_id "
    "RETURNING f.tentativi, f.bloccato_il"
)
# Primo evento delle chiavi ferme da ritentare (non bloccate, pausa scaduta)
_RIPROVE = text(
    "SELECT f.chiave, f.gruppo, o.id, o.creato_il, o.sequenza, o.topic, o.chiave AS chiave_ev, "
    "  o.payload FROM public.outbox_chiavi_ferme f "
    "JOIN public.outbox_eventi o ON o.id = f.evento_id AND o.creato_il = f.evento_creato_il "
    "WHERE f.origine = :origine AND f.bloccato_il IS NULL "
    "AND f.prossimo_tentativo_il <= :adesso AND o.pubblicato_il IS NULL "
    "AND f.topic <> ALL(CAST(:sospesi AS varchar[])) "
    "ORDER BY f.prossimo_tentativo_il LIMIT :limite FOR UPDATE OF f SKIP LOCKED"
)
_SBLOCCA_PUBBLICATI = text(
    "DELETE FROM public.outbox_chiavi_ferme f USING unnest(CAST(:ids AS uuid[])) AS p(id) "
    "WHERE f.origine = :origine AND f.evento_id = p.id"
)
_RINVIA_RIPROVA = text(
    "UPDATE public.outbox_chiavi_ferme SET prossimo_tentativo_il = :prossimo, "
    "ultimo_errore = :errore WHERE origine = :origine AND chiave = :chiave"
)

# ── Topic sospesi (R04 correzioni, secondo giro, N1) ─────────────────────────
# Un errore del TOPIC (permesso negato sul topic o sul cluster, topic inesistente
# o non valido) non è colpa della singola chiave: si sospende il topic per
# l'origine (tabella ``outbox_topic_sospesi``, condivisa fra le repliche) con
# attesa crescente, senza fermare chiavi né contare tentativi. Allo scadere
# dell'attesa il topic è «in prova»: un invio riuscito lo ripristina e tutto
# l'arretrato riparte a lotto pieno; un nuovo errore raddoppia l'attesa.
_TOPIC_SOSPESI = text(
    "SELECT topic, sospeso_fino_a > :adesso AS fermo FROM public.outbox_topic_sospesi "
    "WHERE origine = :origine"
)
_SOSPENDI_TOPIC = text(
    "INSERT INTO public.outbox_topic_sospesi AS t (origine, topic, sospeso_fino_a, "
    "  pausa_secondi, sospensioni, ultimo_errore, dal) "
    "VALUES (:origine, :topic, CAST(:adesso AS timestamptz) + make_interval(secs => :pausa), "
    "  :pausa, 1, :errore, :adesso) "
    "ON CONFLICT (origine, topic) DO UPDATE SET "
    "  pausa_secondi = LEAST(:pausa_massima, t.pausa_secondi * 2), "
    "  sospeso_fino_a = CAST(:adesso AS timestamptz) "
    "    + make_interval(secs => LEAST(:pausa_massima, t.pausa_secondi * 2)), "
    "  sospensioni = t.sospensioni + 1, ultimo_errore = EXCLUDED.ultimo_errore "
    "RETURNING sospensioni, pausa_secondi"
)
_RIPRISTINA_TOPIC = text(
    "DELETE FROM public.outbox_topic_sospesi WHERE origine = :origine AND topic = :topic "
    "RETURNING sospensioni, dal"
)
# Chiavi ferme da errori precedenti sullo stesso topic: ritentate subito al ripristino
_RIPRENDI_CHIAVI_DEL_TOPIC = text(
    "UPDATE public.outbox_chiavi_ferme SET prossimo_tentativo_il = :adesso "
    "WHERE origine = :origine AND topic = :topic AND bloccato_il IS NULL"
)
# Ordine per chiave con topic sospesi: chiavi lette che hanno un evento PRECEDENTE
# in attesa su un topic sospeso (indice ``idx_outbox_chiavi_in_sospeso``, 033)
_CHIAVI_DIETRO_SOSPESI = text(
    "SELECT o.chiave, min(o.sequenza) AS sequenza FROM public.outbox_eventi o "
    "WHERE o.origine = :origine AND o.pubblicato_il IS NULL "
    "AND o.topic = ANY(CAST(:sospesi AS varchar[])) "
    "AND o.chiave = ANY(CAST(:chiavi AS varchar[])) GROUP BY o.chiave"
)

#: Errori del TOPIC (non della chiave, non della connessione): vedi sopra.
_NOMI_ERRORI_TOPIC = frozenset({
    "TopicAuthorizationFailedError", "ClusterAuthorizationFailedError",
    "UnknownTopicOrPartitionError", "InvalidTopicError",
})


def errore_del_topic(exc: BaseException) -> bool:
    """True se l'errore riguarda il topic per tutte le chiavi (autorizzazione,
    topic inesistente o non valido): sospensione del topic, non della chiave."""
    return any(t.__name__ in _NOMI_ERRORI_TOPIC for t in type(exc).__mro__)


#: Errori dell'invio che indicano il BROKER non raggiungibile (non l'evento):
#: non contano come tentativi dell'evento e aprono il cortocircuito.
_NOMI_ERRORI_CONNESSIONE = frozenset({
    "KafkaConnectionError", "NodeNotReadyError", "KafkaTimeoutError", "RequestTimedOutError",
    "BrokerNotAvailableError", "LeaderNotAvailableError", "NotLeaderForPartitionError",
    "KafkaUnavailableError", "NotEnoughReplicasError", "NotEnoughReplicasAfterAppendError",
})


def errore_di_connessione(exc: BaseException) -> bool:
    """True se l'errore riguarda la connessione a Kafka (broker giù, timeout,
    producer non attivo), False se riguarda il topic (``errore_del_topic``) o il
    singolo evento (messaggio troppo grande, payload non valido…)."""
    if isinstance(exc, KafkaNonDisponibile | ConnectionError | TimeoutError):
        return True
    return any(t.__name__ in _NOMI_ERRORI_CONNESSIONE for t in type(exc).__mro__)


def pausa_riprova(tentativi: int, cfg: ConfigRelay) -> float:
    """Pausa prima del tentativo successivo di una chiave ferma (raddoppia)."""
    esponente = max(0, min(tentativi - 1, 30))
    return float(min(cfg.riprova_massima_secondi, cfg.riprova_secondi * (2 ** esponente)))


@dataclass
class _Riga:
    id: uuid.UUID
    creato_il: Any
    sequenza: int
    gruppo: int
    topic: str
    chiave: str | None
    payload: dict[str, Any]
    chiave_ordine: str = ""


def _payload(valore: Any) -> dict[str, Any]:
    return json.loads(valore) if isinstance(valore, str) else valore


async def pubblica_in_sospeso(invia: Invio = invia_kafka, *, limite: int = 100,
                              origine: str | None = None,
                              gruppi_per_giro: int | None = None,
                              config: ConfigRelay | None = None,
                              adesso: datetime | None = None,
                              riprove: bool = True) -> int:
    """Un giro del relay per ``origine`` (predefinita: quella del processo).
    ``gruppi_per_giro`` None = tutti i gruppi liberi (pubblicazione subito dopo
    il commit); restituisce quanti eventi ha pubblicato (ritentativi compresi).
    ``adesso`` (iniettabile nei test) decide quali chiavi ferme ritentare e quali
    topic sono sospesi. ``riprove=False``: niente ritentativi delle chiavi ferme
    (pubblicazione dopo il commit, dentro una richiesta HTTP: N2)."""
    origine = origine or origine_corrente()
    cfg = config or ConfigRelay()
    adesso = adesso or datetime.now(UTC)
    from libs.shared import kafka as kafka_condiviso

    producer = None
    senza_producer = False
    if invia is invia_kafka:
        # producer non partito all'avvio: un tentativo ogni tanto (limitato da kafka)
        senza_producer = not await kafka_condiviso.riprova_producer()
        producer = kafka_condiviso._producer  # noqa: SLF001 — identità per il circuito
    circuito = cortocircuito(origine, invia, producer)
    if circuito.aperto():
        return 0  # Kafka giù: niente DB, niente tentativi, niente log fino alla prova
    quanti = NUMERO_GRUPPI if gruppi_per_giro is None else max(1, min(NUMERO_GRUPPI,
                                                                       gruppi_per_giro))
    pubblicati: list[_Riga] = []
    errori: list[tuple[_Riga, Exception]] = []
    errori_topic: dict[str, Exception] = {}
    async with get_async_session() as session:
        stato_topic = {str(r.topic): bool(r.fermo) for r in (await session.execute(
            _TOPIC_SOSPESI, {"origine": origine, "adesso": adesso})).all()}
        sospesi = sorted(t for t, fermo in stato_topic.items() if fermo)
        in_prova = {t for t, fermo in stato_topic.items() if not fermo}
        gruppi = [int(r[0]) for r in (await session.execute(_PRENDI_GRUPPI, {
            "origine": origine, "ultimo": NUMERO_GRUPPI - 1, "sospesi": sospesi,
            "k": _chiave_lock(origine), "quanti": quanti})).all()]
        lotto_riprove = max(1, cfg.riprove_per_giro, limite // 5)
        riprove = [] if (senza_producer or not riprove) else (await session.execute(_RIPROVE, {
            "origine": origine, "adesso": adesso, "sospesi": sospesi,
            "limite": lotto_riprove})).all()
        if not gruppi and not riprove:
            return 0
        if senza_producer:
            circuito.fallimento(origine, KafkaNonDisponibile("producer Kafka non attivo"),
                                eventi=len(gruppi))
            return 0
        righe = [
            _Riga(r.id, r.creato_il, int(r.sequenza), int(r.gruppo), r.topic, r.chiave,
                  _payload(r.payload), str(r.chiave_ordine))
            for r in (await session.execute(_LEGGI, {
                "origine": origine, "gruppi": gruppi, "limite": limite,
                "sospesi": sospesi})).all()
        ] if gruppi else []
        # Ordine per chiave: una chiave con un evento precedente su un topic sospeso
        # non va avanti (l'evento sospeso non è nel lotto ma viene prima)
        dietro: dict[str, int] = {}
        if sospesi and righe:
            dietro = {str(r.chiave): int(r.sequenza) for r in (await session.execute(
                _CHIAVI_DIETRO_SOSPESI, {"origine": origine, "sospesi": sospesi, "chiavi": sorted(
                    {x.chiave for x in righe if x.chiave})})).all()}
        per_gruppo: dict[int, list[_Riga]] = {}
        for riga in righe:  # già in ordine di sequenza
            if riga.chiave in dietro and dietro[riga.chiave] < riga.sequenza:
                continue
            per_gruppo.setdefault(riga.gruppo, []).append(riga)

        async def _gruppo(eventi: list[_Riga]) -> None:
            ferme: set[str] = set()
            for ev in eventi:
                if ev.chiave_ordine in ferme:
                    continue  # stessa chiave di un evento fallito: nessun sorpasso
                if ev.topic in errori_topic:
                    ferme.add(ev.chiave_ordine)  # topic appena sospeso: si salta (giro)
                    continue
                try:
                    await invia(ev.topic, ev.chiave, ev.payload)
                except Exception as exc:  # noqa: BLE001 — registrato sulla riga, ritentato
                    if errore_di_connessione(exc):
                        errori.append((ev, exc))
                        return  # broker giù: inutile insistere sul gruppo in questo giro
                    ferme.add(ev.chiave_ordine)  # la chiave non sorpassa (in questo giro)
                    if errore_del_topic(exc):
                        errori_topic.setdefault(ev.topic, exc)  # sospensione del TOPIC
                        continue
                    errori.append((ev, exc))  # errore dell'evento: la chiave si ferma
                    continue
                pubblicati.append(ev)

        await asyncio.gather(*(_gruppo(ev) for ev in per_gruppo.values()))
        # Ritentativi delle chiavi ferme (il primo evento di ciascuna)
        ripresi: list[_Riga] = []
        for r in riprove:
            ev = _Riga(r.id, r.creato_il, int(r.sequenza), int(r.gruppo), r.topic,
                       r.chiave_ev, _payload(r.payload), str(r.chiave))
            if ev.topic in errori_topic:
                continue
            try:
                await invia(ev.topic, ev.chiave, ev.payload)
            except Exception as exc:  # noqa: BLE001 — registrato, ritentato o bloccato
                if errore_del_topic(exc) and not errore_di_connessione(exc):
                    errori_topic.setdefault(ev.topic, exc)
                    continue
                errori.append((ev, exc))
                continue
            ripresi.append(ev)
        tutti = pubblicati + ripresi
        if tutti:
            await session.execute(_MARCA_PUBBLICATI, {
                "ids": [str(r.id) for r in tutti], "creati": [r.creato_il for r in tutti]})
        if ripresi:
            await session.execute(_SBLOCCA_PUBBLICATI, {
                "origine": origine, "ids": [str(r.id) for r in ripresi]})
        bloccati = await _registra_errori(session, origine, errori, cfg, adesso)
        sospensioni: list[tuple[str, Exception, int, float]] = []
        for topic, exc in errori_topic.items():
            r = (await session.execute(_SOSPENDI_TOPIC, {
                "origine": origine, "topic": topic, "adesso": adesso,
                "pausa": cfg.pausa_topic_secondi, "pausa_massima": cfg.pausa_topic_massima_secondi,
                "errore": f"{type(exc).__name__}: {exc}"[:500]})).one()
            sospensioni.append((topic, exc, int(r.sospensioni), float(r.pausa_secondi)))
        ripristinati: list[tuple[str, int, Any]] = []
        riusciti = {r.topic for r in tutti}
        for topic in sorted((in_prova & riusciti) - set(errori_topic)):
            r = (await session.execute(_RIPRISTINA_TOPIC, {
                "origine": origine, "topic": topic})).first()
            await session.execute(_RIPRENDI_CHIAVI_DEL_TOPIC, {
                "origine": origine, "topic": topic, "adesso": adesso})
            if r is not None:
                ripristinati.append((topic, int(r.sospensioni), r.dal))
    for topic, exc, volte, pausa in sospensioni:
        logger.error("outbox_topic_sospeso", origine=origine, topic=topic, sospensioni=volte,
                     prossima_prova_secondi=pausa, errore=str(exc)[:200],
                     azione="controllare permessi e esistenza del topic; nessuna chiave "
                            "si ferma, l'arretrato riparte al ripristino")
        metriche_db.incrementa("verumdoc_outbox_topic_sospensioni", origine=origine)
    for topic, volte, dal in ripristinati:
        logger.info("outbox_topic_ripristinato", origine=origine, topic=topic,
                    sospensioni=volte, sospeso_dal=str(dal))
    pubblicati = tutti
    di_connessione = [e for e in errori if errore_di_connessione(e[1])]
    dell_evento = [e for e in errori if not errore_di_connessione(e[1])]
    if di_connessione and not pubblicati:
        # Nessun evento partito e il broker non risponde: Kafka giù. Un avviso solo.
        circuito.fallimento(origine, di_connessione[0][1], eventi=len(righe) + len(riprove))
    else:
        circuito.successo(origine)
    if dell_evento:
        # Kafka risponde ma alcuni eventi no (topic, dimensione, permessi): UN
        # riepilogo per giro, distinto da «Kafka giù»; la chiave è ferma, le altre no
        primo, exc = dell_evento[0]
        logger.warning("outbox_invio_fallito", origine=origine, falliti=len(dell_evento),
                       pubblicati=len(pubblicati), evento_id=str(primo.id),
                       topic=primo.topic, chiave=primo.chiave, errore=str(exc)[:200])
        metriche_db.incrementa("verumdoc_outbox_errori_evento", len(dell_evento),
                               origine=origine)
    if di_connessione:
        metriche_db.incrementa("verumdoc_outbox_errori_kafka", len(di_connessione),
                               origine=origine)
    for ev, tentativi, errore in bloccati:
        logger.error("outbox_evento_bloccato", origine=origine, evento_id=str(ev.id),
                     topic=ev.topic, chiave=ev.chiave, tentativi=tentativi, errore=errore[:200],
                     azione="code di scarto della console: ritenta o scarta")
        metriche_db.incrementa("verumdoc_outbox_eventi_bloccati", origine=origine)
    if pubblicati:
        metriche_db.incrementa("verumdoc_outbox_pubblicati", len(pubblicati), origine=origine)
    if errori:
        metriche_db.incrementa("verumdoc_outbox_errori", len(errori), origine=origine)
    return len(pubblicati)


async def _registra_errori(session: Any, origine: str, errori: list[tuple[_Riga, Exception]],
                           cfg: ConfigRelay, adesso: datetime
                           ) -> list[tuple[_Riga, int, str]]:
    """Scrive gli errori del giro; restituisce gli eventi diventati BLOCCATI."""
    bloccati: list[tuple[_Riga, int, str]] = []
    for ev, exc in errori:
        testo = f"{type(exc).__name__}: {exc}"[:500]
        if errore_di_connessione(exc):
            await session.execute(_MARCA_ERRORE_CONNESSIONE, {
                "id": ev.id, "creato_il": ev.creato_il, "errore": testo})
            await session.execute(_RINVIA_RIPROVA, {
                "origine": origine, "chiave": ev.chiave_ordine, "errore": testo,
                "prossimo": adesso + timedelta(seconds=cfg.riprova_secondi)})
            continue
        esito = (await session.execute(_FERMA_CHIAVE, {
            "origine": origine, "chiave": ev.chiave_ordine, "gruppo": ev.gruppo, "id": ev.id,
            "creato_il": ev.creato_il, "sequenza": ev.sequenza, "topic": ev.topic,
            "errore": testo, "massimo": cfg.max_tentativi, "adesso": adesso,
            "pausa": cfg.riprova_secondi, "pausa_massima": cfg.riprova_massima_secondi,
        })).first()
        # Bloccata IN QUESTO giro (istante del blocco = adesso): un allarme solo
        if esito is not None and esito.bloccato_il is not None and esito.bloccato_il == adesso:
            bloccati.append((ev, int(esito.tentativi), testo))
    return bloccati


async def pubblica_dopo_commit(*, limite: int = 20, **contesto: Any) -> int:
    """
    Tentativo immediato, dopo il commit del chiamante, di pubblicare gli eventi
    in sospeso del servizio (best effort). Non solleva mai: se Kafka o il DB non
    rispondono l'evento resta in outbox e lo pubblica il relay. Restituisce i
    pubblicati.
    """
    try:
        # N2: niente ritentativi delle chiavi ferme nella richiesta del cliente
        return await pubblica_in_sospeso(limite=limite, riprove=False)
    except Exception as exc:  # noqa: BLE001 — l'evento è già durevole in outbox
        logger.warning("outbox_pubblicazione_rinviata", errore=str(exc)[:200], **contesto)
        return 0


async def misura_arretrato(origine: str | None = None) -> tuple[int, float]:
    """(eventi in attesa, età in secondi del più vecchio PUBBLICABILE) per
    l'origine; aggiorna le metriche, comprese le chiavi ferme e bloccate. Costo
    limitato: conteggio con tetto ``TETTO_ARRETRATO``, primo evento dall'indice
    ``(origine, sequenza)`` delle righe in attesa, chiavi ferme da una tabella
    piccola. L'età esclude le chiavi ferme: un evento bloccato ha il SUO allarme
    (``verumdoc_outbox_bloccati``) e non si scambia per un relay fermo."""
    origine = origine or origine_corrente()
    async with get_async_session() as session:
        arretrato = int((await session.execute(text(
            "SELECT count(*) FROM (SELECT 1 FROM public.outbox_eventi "
            "WHERE origine = :o AND pubblicato_il IS NULL LIMIT :tetto) s"),
            {"o": origine, "tetto": TETTO_ARRETRATO})).scalar_one())
        sospesi = [str(r[0]) for r in (await session.execute(text(
            "SELECT topic FROM public.outbox_topic_sospesi WHERE origine = :o"),
            {"o": origine})).all()]
        eta = (await session.execute(text(
            "SELECT EXTRACT(EPOCH FROM (now() - o.creato_il)) FROM public.outbox_eventi o "  # noqa: S608 — frammenti SQL costanti
            f"WHERE o.origine = :o AND o.pubblicato_il IS NULL AND {_NON_FERMA} "
            "ORDER BY o.sequenza LIMIT 1"),
            {"o": origine, "sospesi": sospesi})).scalar_one_or_none()
        ferme, bloccate = (await session.execute(text(
            "SELECT count(*) FILTER (WHERE bloccato_il IS NULL), "
            "count(*) FILTER (WHERE bloccato_il IS NOT NULL) "
            "FROM public.outbox_chiavi_ferme WHERE origine = :o"), {"o": origine})).one()
    eta_s = max(0.0, float(eta)) if eta is not None else 0.0
    metriche_db.imposta("verumdoc_outbox_arretrato", arretrato, origine=origine)
    metriche_db.imposta("verumdoc_outbox_eta_piu_vecchio_secondi", eta_s, origine=origine)
    metriche_db.imposta("verumdoc_outbox_chiavi_ferme", int(ferme), origine=origine)
    metriche_db.imposta("verumdoc_outbox_bloccati", int(bloccate), origine=origine)
    metriche_db.imposta("verumdoc_outbox_topic_sospesi", len(sospesi), origine=origine)
    return arretrato, eta_s


async def relay_outbox(
    invia: Invio = invia_kafka,
    *,
    intervallo: float = 2.0,
    fine: asyncio.Event | None = None,
    origine: str | None = None,
    config: ConfigRelay | None = None,
) -> None:
    """Svuota l'outbox dell'origine finché non viene fermato. A lotto pieno
    riparte subito; altrimenti attende ``intervallo`` (interrompibile con ``fine``)."""
    cfg = config or ConfigRelay()
    origine = origine or cfg.origine or origine_corrente()
    ultima_misura = 0.0
    while fine is None or not fine.is_set():
        pieno = False
        try:
            pubblicati = await pubblica_in_sospeso(
                invia, limite=cfg.lotto, origine=origine, gruppi_per_giro=cfg.gruppi_per_giro,
                config=cfg)
            pieno = pubblicati >= cfg.lotto
        except Exception as exc:  # noqa: BLE001 — DB momentaneamente giù: si riprova
            logger.error("outbox_relay_errore", origine=origine, errore=str(exc)[:300])
        if time.monotonic() - ultima_misura >= cfg.misura_ogni_secondi:
            ultima_misura = time.monotonic()
            try:
                await misura_arretrato(origine)
            except Exception as exc:  # noqa: BLE001 — la misura non ferma il relay
                logger.warning("outbox_misura_errore", origine=origine, errore=str(exc)[:200])
        if pieno:
            continue
        if fine is None:
            await asyncio.sleep(intervallo)
        else:
            # Attesa interrompibile: l'arresto non aspetta l'intervallo intero
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(fine.wait(), timeout=intervallo)
    logger.info("outbox_relay_fermato", origine=origine)


@dataclass
class RelayServizio:
    """Relay (e manutenzione del database) avviati da ``avvia_relay_servizio``."""
    origine: str
    fine: asyncio.Event
    task: asyncio.Task | None
    manutenzione: Any = None
    producer_proprio: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


async def avvia_relay_servizio(
    origine: str,
    *,
    app: Any = None,
    bootstrap: str | None = None,
    invia: Invio | None = None,
    config: ConfigRelay | None = None,
    manutenzione: bool = True,
) -> RelayServizio:
    """Avvia nel processo il relay degli eventi di ``origine`` (VD-C-076).

    - ``configura_origine(origine)``: gli eventi scritti da questo processo
      portano questa origine;
    - con ``app`` registra le metriche (arretrato, età) sul registro di
      ``installa_metriche``;
    - con ``bootstrap`` avvia il producer Kafka condiviso se il servizio non
      l'ha già fatto (e lo chiude in ``ferma_relay_servizio``);
    - ``OUTBOX_RELAY_ATTIVO=false``: solo origine e metriche, nessun task (relay
      come processo dedicato);
    - ``manutenzione``: avvia anche la manutenzione del database
      (``libs.shared.db.manutenzione``: partizioni e contatori; una replica alla
      volta grazie ai lock consultivi).

    Riga da aggiungere nel lifespan, dopo ``init_db``:
        app.state.relay_outbox = await avvia_relay_servizio("<servizio>", app=app,
                                                            bootstrap=settings.kafka_bootstrap)
    e all'arresto, prima di ``close_db``:
        await ferma_relay_servizio(app.state.relay_outbox)
    """
    from libs.shared import kafka as kafka_condiviso

    cfg = config or ConfigRelay()
    configura_origine(cfg.origine or origine)
    if app is not None:
        metriche_db.registra_metriche_db(metriche_db.registro_da_app(app))
    producer_proprio = False
    if bootstrap and not kafka_condiviso.producer_attivo():
        await kafka_condiviso.init_kafka_producer(bootstrap)
        producer_proprio = kafka_condiviso.producer_attivo()
    fine = asyncio.Event()
    task = None
    if cfg.relay_attivo:
        task = asyncio.create_task(
            relay_outbox(invia or invia_kafka, intervallo=cfg.intervallo_secondi, fine=fine,
                         origine=origine_corrente(), config=cfg),
            name=f"relay-outbox-{origine}")
    lavoro_manutenzione = None
    if manutenzione:
        from libs.shared.db.manutenzione import avvia_manutenzione_db

        lavoro_manutenzione = await avvia_manutenzione_db(app=app)
    logger.info("outbox_relay_avviato", origine=origine_corrente(), attivo=task is not None)
    return RelayServizio(origine_corrente(), fine, task, lavoro_manutenzione, producer_proprio)


async def ferma_relay_servizio(relay: RelayServizio | None, *, attesa: float = 10.0) -> None:
    """Arresto pulito: segnala la fine, attende il giro in corso (che chiude la
    sua transazione), cancella solo se non termina entro ``attesa``."""
    if relay is None:
        return
    relay.fine.set()
    if relay.task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(relay.task), timeout=attesa)
        except TimeoutError:
            relay.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await relay.task
            logger.warning("relay_outbox_cancellato", origine=relay.origine, attesa_secondi=attesa)
        except Exception as exc:  # noqa: BLE001 — il relay è già terminato con errore
            logger.error("relay_outbox_terminato_con_errore", origine=relay.origine,
                         errore=str(exc)[:300])
    if relay.manutenzione is not None:
        from libs.shared.db.manutenzione import ferma_manutenzione_db

        await ferma_manutenzione_db(relay.manutenzione, attesa=attesa)
    if relay.producer_proprio:
        from libs.shared import kafka as kafka_condiviso

        await kafka_condiviso.close_kafka()
