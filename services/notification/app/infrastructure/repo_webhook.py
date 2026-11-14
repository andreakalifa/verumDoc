# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/infrastructure/repo_webhook.py
Sottoscrizioni e consegne webhook su PostgreSQL — contratto C3.

Ogni lettura e scrittura filtra per ente (``tenant_id``) E per l'id indicato
nell'URL: una risorsa di un altro ente è «non trovata» (REGOLE §14.4, §14.6).

Consegne (lavoratore):
  - ``preleva_consegne``: con ``FOR UPDATE OF c SKIP LOCKED`` più repliche non
    prendono la stessa consegna; la riga passa IN_CONSEGNA con un ``lease_id``
    nuovo e ``prossimo_tentativo`` = scadenza del lease. Una consegna rimasta
    IN_CONSEGNA oltre il lease (crash) si riprende al giro successivo;
  - ``chiudi_tentativo``: aggiorna solo se il ``lease_id`` è ancora quello
    preso (un lavoratore in ritardo non sovrascrive l'esito di un altro).
Solo le sottoscrizioni attive e non eliminate si consegnano: disattivare una
sottoscrizione mette in pausa le sue consegne; eliminarla le ANNULLA.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.domain.webhook import ANNULLATA, CONSEGNATA, FALLITA, IN_CODA, IN_CONSEGNA
from libs.shared.db.ambito import con_ambito_conservatore


def _fabbrica_predefinita() -> Any:
    from libs.shared.db import get_async_session

    return get_async_session


# ── Sottoscrizioni ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Sottoscrizione:
    id: str
    tenant_id: str
    url: str
    tipi: list[str]
    descrizione: str | None
    segreto_suffisso: str
    attiva: bool
    creata_il: datetime
    aggiornata_il: datetime
    # R13b: rotazione (segreto precedente ancora valido fino a ``transizione_fino_al``)
    segreto_precedente_suffisso: str | None = None
    transizione_fino_al: datetime | None = None
    segreto_ruotato_il: datetime | None = None


_COLONNE_S = ("id::text AS id, tenant_id::text AS tenant_id, url, tipi, descrizione, "
              "segreto_suffisso, attiva, creata_il, aggiornata_il, "
              "segreto_precedente_suffisso, segreto_precedente_scade_il, segreto_ruotato_il")


def _sottoscrizione(r: Any) -> Sottoscrizione:
    return Sottoscrizione(r.id, r.tenant_id, r.url, list(r.tipi or []), r.descrizione,
                          r.segreto_suffisso, bool(r.attiva), r.creata_il, r.aggiornata_il,
                          r.segreto_precedente_suffisso, r.segreto_precedente_scade_il,
                          r.segreto_ruotato_il)


class LimiteSottoscrizioni(Exception):
    """L'ente ha già il numero massimo di sottoscrizioni."""


#: Lavoro del chiamante nella transazione della creazione: (sessione, id creato).
NellaTransazione = Callable[[Any, str], Awaitable[None]]


class RepositoryWebhook:
    def __init__(self, sessione_factory: Any = None) -> None:
        self._sessione = sessione_factory or _fabbrica_predefinita()

    async def crea(self, *, id_: str, tenant_id: str, url: str, tipi: list[str],
                   descrizione: str | None, segreto_cifrato: str, segreto_suffisso: str,
                   attiva: bool, creata_da: str | None, massimo: int,
                   nella_transazione: NellaTransazione | None = None) -> Sottoscrizione:
        """Crea la sottoscrizione. ``nella_transazione(sessione, id)`` gira nella
        STESSA transazione dopo l'INSERT (R04: completamento della Idempotency-Key):
        se fallisce, la sottoscrizione non esiste."""
        async with self._sessione() as s:
            # «Controlla poi crea» sotto lock dell'ente (REGOLE §12.6)
            await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                            {"k": f"webhook:{tenant_id}"})
            esistenti = (await s.execute(text(
                "SELECT count(*) FROM public.webhook_sottoscrizioni "
                "WHERE tenant_id = CAST(:t AS uuid) AND eliminata_il IS NULL"
            ), {"t": tenant_id})).scalar_one()
            if int(esistenti) >= massimo:
                raise LimiteSottoscrizioni()
            riga = (await s.execute(text(
                "INSERT INTO public.webhook_sottoscrizioni "  # noqa: S608 — colonne costanti
                "(id, tenant_id, url, tipi, descrizione, segreto_cifrato, segreto_suffisso, "
                " attiva, creata_da) VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :url, "
                " CAST(:tipi AS jsonb), :descrizione, :cifrato, :suffisso, :attiva, "
                " CAST(:creata_da AS uuid)) "
                f"RETURNING {_COLONNE_S}"
            ), {"id": id_, "t": tenant_id, "url": url, "tipi": json.dumps(tipi),
                "descrizione": descrizione, "cifrato": segreto_cifrato,
                "suffisso": segreto_suffisso, "attiva": attiva, "creata_da": creata_da})).one()
            if nella_transazione is not None:
                await nella_transazione(s, id_)
        return _sottoscrizione(riga)

    async def elenca(self, tenant_id: str) -> list[Sottoscrizione]:
        async with self._sessione() as s:
            righe = (await s.execute(text(
                f"SELECT {_COLONNE_S} FROM public.webhook_sottoscrizioni "  # noqa: S608 — colonne costanti
                "WHERE tenant_id = CAST(:t AS uuid) AND eliminata_il IS NULL "
                "ORDER BY creata_il, id"
            ), {"t": tenant_id})).all()
        return [_sottoscrizione(r) for r in righe]

    async def leggi(self, tenant_id: str, id_: str) -> Sottoscrizione | None:
        async with self._sessione() as s:
            riga = (await s.execute(text(
                f"SELECT {_COLONNE_S} FROM public.webhook_sottoscrizioni "  # noqa: S608 — colonne costanti
                "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:t AS uuid) "
                "AND eliminata_il IS NULL"
            ), {"id": id_, "t": tenant_id})).first()
        return _sottoscrizione(riga) if riga else None

    async def aggiorna(self, tenant_id: str, id_: str,
                       modifiche: dict[str, Any]) -> tuple[Sottoscrizione, Sottoscrizione] | None:
        """(prima, dopo) oppure None se non trovata. Chiavi ammesse: url, tipi,
        descrizione, attiva."""
        ammesse = {"url", "tipi", "descrizione", "attiva"}
        if not set(modifiche) <= ammesse:
            raise ValueError(f"campi non modificabili: {sorted(set(modifiche) - ammesse)}")
        async with self._sessione() as s:
            prima = (await s.execute(text(
                f"SELECT {_COLONNE_S} FROM public.webhook_sottoscrizioni "  # noqa: S608 — colonne costanti
                "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:t AS uuid) "
                "AND eliminata_il IS NULL FOR UPDATE"
            ), {"id": id_, "t": tenant_id})).first()
            if prima is None:
                return None
            insiemi = ["aggiornata_il = now()"]
            parametri: dict[str, Any] = {"id": id_, "t": tenant_id}
            for campo in sorted(modifiche):
                if campo == "tipi":
                    insiemi.append("tipi = CAST(:tipi AS jsonb)")
                    parametri["tipi"] = json.dumps(modifiche["tipi"])
                else:
                    insiemi.append(f"{campo} = :{campo}")
                    parametri[campo] = modifiche[campo]
            dopo = (await s.execute(text(
                f"UPDATE public.webhook_sottoscrizioni SET {', '.join(insiemi)} "  # noqa: S608 — campi da elenco chiuso
                "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:t AS uuid) "
                f"RETURNING {_COLONNE_S}"
            ), parametri)).one()
        return _sottoscrizione(prima), _sottoscrizione(dopo)

    async def elimina(self, tenant_id: str, id_: str) -> int | None:
        """Eliminazione logica; consegne in sospeso ANNULLATE. Restituisce il
        numero di consegne annullate, o None se la sottoscrizione non c'è."""
        async with self._sessione() as s:
            riga = (await s.execute(text(
                "UPDATE public.webhook_sottoscrizioni SET eliminata_il = now(), attiva = false, "
                "aggiornata_il = now() WHERE id = CAST(:id AS uuid) "
                "AND tenant_id = CAST(:t AS uuid) AND eliminata_il IS NULL RETURNING id"
            ), {"id": id_, "t": tenant_id})).first()
            if riga is None:
                return None
            annullate = await s.execute(text(
                "UPDATE public.webhook_consegne SET stato = :annullata, lease_id = NULL, "
                "prossimo_tentativo = NULL, aggiornata_il = now() "
                "WHERE sottoscrizione_id = CAST(:id AS uuid) AND stato IN (:in_coda, :in_consegna)"
            ), {"id": id_, "annullata": ANNULLATA, "in_coda": IN_CODA, "in_consegna": IN_CONSEGNA})
        return int(annullate.rowcount or 0)

    # ── R13b: rotazione del segreto ─────────────────────────────────

    async def ruota_segreto(self, tenant_id: str, id_: str, *, cifrato: str, suffisso: str,
                            scade_il: datetime) -> Sottoscrizione | None:
        """Il segreto attuale diventa il PRECEDENTE (valido fino a ``scade_il``) e il
        nuovo prende il suo posto, in un solo UPDATE con la riga bloccata. None se la
        sottoscrizione non c'è (o è di un altro ente)."""
        async with self._sessione() as s:
            riga = (await s.execute(text(
                "UPDATE public.webhook_sottoscrizioni SET "  # noqa: S608 — colonne costanti
                " segreto_precedente_cifrato = segreto_cifrato, "
                " segreto_precedente_suffisso = segreto_suffisso, "
                " segreto_precedente_scade_il = :scade, segreto_cifrato = :cifrato, "
                " segreto_suffisso = :suffisso, segreto_ruotato_il = now(), aggiornata_il = now() "
                "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:t AS uuid) "
                f"AND eliminata_il IS NULL RETURNING {_COLONNE_S}"
            ), {"id": id_, "t": tenant_id, "scade": scade_il, "cifrato": cifrato,
                "suffisso": suffisso})).first()
        return _sottoscrizione(riga) if riga else None

    async def chiudi_transizione(self, tenant_id: str, id_: str
                                 ) -> tuple[str, Sottoscrizione | None]:
        """Ritira subito il segreto precedente: ("ok", s) | ("non_trovata", None) |
        ("nessuna", s) se non c'è un precedente ancora valido."""
        async with self._sessione() as s:
            riga = (await s.execute(text(
                "UPDATE public.webhook_sottoscrizioni SET segreto_precedente_cifrato = NULL, "  # noqa: S608
                " segreto_precedente_suffisso = NULL, segreto_precedente_scade_il = NULL, "
                " aggiornata_il = now() WHERE id = CAST(:id AS uuid) "
                "AND tenant_id = CAST(:t AS uuid) AND eliminata_il IS NULL "
                "AND segreto_precedente_scade_il > now() "
                f"RETURNING {_COLONNE_S}"), {"id": id_, "t": tenant_id})).first()
            if riga is not None:
                return "ok", _sottoscrizione(riga)
            esistente = (await s.execute(text(
                f"SELECT {_COLONNE_S} FROM public.webhook_sottoscrizioni "  # noqa: S608
                "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:t AS uuid) "
                "AND eliminata_il IS NULL"), {"id": id_, "t": tenant_id})).first()
        return ("nessuna", _sottoscrizione(esistente)) if esistente else ("non_trovata", None)

    # ── Consegne: consultazione e riprova ───────────────────────────

    async def elenca_consegne(self, tenant_id: str, sottoscrizione_id: str, *, dopo: int,
                              limite: int, stato: str | None) -> tuple[list[dict[str, Any]], bool]:
        filtro = " AND c.stato = :stato" if stato else ""
        async with self._sessione() as s:
            righe = (await s.execute(text(
                "SELECT c.id, c.evento_id, e.tipo AS tipo_evento, c.stato, c.tentativi, "  # noqa: S608 — filtro costante
                "c.riprove, c.prossimo_tentativo, c.ultimo_stato_http, c.ultimo_codice, "
                "c.ultimo_errore, c.ultimo_tentativo_il, c.consegnata_il, c.creata_il "
                "FROM public.webhook_consegne c "
                # R04 (migrazione 028): la chiave di partizione nel join limita
                # la ricerca dell'evento alla sua partizione
                "JOIN public.eventi_cliente e ON e.id = c.evento_id "
                "AND e.creato_il = c.evento_creato_il "
                "WHERE c.sottoscrizione_id = CAST(:sid AS uuid) "
                "AND c.tenant_id = CAST(:t AS uuid) AND c.id > :dopo"
                f"{filtro} ORDER BY c.id LIMIT :n"
            ), {"sid": sottoscrizione_id, "t": tenant_id, "dopo": dopo, "n": limite + 1,
                "stato": stato})).mappings().all()
        return [dict(r) for r in righe[:limite]], len(righe) > limite

    async def riprova(self, tenant_id: str, sottoscrizione_id: str,
                      consegna_id: int) -> tuple[str, dict[str, Any] | None]:
        """("ok", consegna) | ("non_trovata", None) | ("stato", {"stato": ...}) |
        ("oltre_retention", {...}). Solo una consegna FALLITA torna IN_CODA, con i
        tentativi azzerati, e solo se il suo evento è dentro la retention degli
        eventi per i clienti (R04 correzioni, rev4 rilievo 11: una consegna
        rimessa in coda in una partizione che la manutenzione sta staccando
        sparirebbe senza traccia; la manutenzione inoltre ricontrolla dopo il
        distacco e ri-aggancia)."""
        from libs.shared.db.partizioni import RETENZIONE_EVENTI_CLIENTE_GIORNI, retenzione_giorni

        async with self._sessione() as s:
            riga = (await s.execute(text(
                "SELECT c.stato, c.evento_creato_il, "
                "c.evento_creato_il < now() - make_interval(days => :giorni) AS scaduta "
                "FROM public.webhook_consegne c "
                "JOIN public.webhook_sottoscrizioni w ON w.id = c.sottoscrizione_id "
                "WHERE c.id = :cid AND c.sottoscrizione_id = CAST(:sid AS uuid) "
                "AND c.tenant_id = CAST(:t AS uuid) AND w.eliminata_il IS NULL "
                "FOR UPDATE OF c"
            ), {"cid": consegna_id, "sid": sottoscrizione_id, "t": tenant_id,
                "giorni": await retenzione_giorni(s, RETENZIONE_EVENTI_CLIENTE_GIORNI)})).first()
            if riga is None:
                return "non_trovata", None
            if riga.stato != FALLITA:
                return "stato", {"stato": riga.stato}
            if riga.scaduta:
                return "oltre_retention", {"evento_creato_il": riga.evento_creato_il.isoformat()}
            aggiornata = (await s.execute(text(
                "UPDATE public.webhook_consegne SET stato = :in_coda, tentativi = 0, "
                "riprove = riprove + 1, prossimo_tentativo = now(), lease_id = NULL, "
                "aggiornata_il = now() WHERE id = :cid "
                "RETURNING id, evento_id, stato, tentativi, riprove, prossimo_tentativo"
            ), {"cid": consegna_id, "in_coda": IN_CODA})).mappings().one()
        return "ok", dict(aggiornata)


# ── Consegne: lavoratore ────────────────────────────────────────────

@dataclass(frozen=True)
class ConsegnaDaFare:
    id: int
    lease_id: str
    tenant_id: str
    sottoscrizione_id: str
    tentativi: int
    url: str
    segreto_cifrato: str
    evento: dict[str, Any]
    # R13b: segreto precedente (rotazione) e sua scadenza
    segreto_precedente_cifrato: str | None = None
    segreto_precedente_scade_il: datetime | None = None


_PRELEVA = (
    "WITH presi AS ("
    "  UPDATE public.webhook_consegne c SET stato = :in_consegna, lease_id = :lease, "
    "    prossimo_tentativo = :scadenza, aggiornata_il = :adesso "
    "  WHERE (c.id, c.evento_creato_il) IN ("
    "    SELECT c2.id, c2.evento_creato_il FROM public.webhook_consegne c2 "
    "    JOIN public.webhook_sottoscrizioni s2 ON s2.id = c2.sottoscrizione_id "
    "    WHERE c2.stato IN (:in_coda, :in_consegna) AND c2.prossimo_tentativo <= :adesso "
    "      AND s2.attiva AND s2.eliminata_il IS NULL "
    "    ORDER BY c2.prossimo_tentativo, c2.id LIMIT :n FOR UPDATE OF c2 SKIP LOCKED) "
    "  RETURNING c.id, c.tenant_id, c.sottoscrizione_id, c.evento_id, c.evento_creato_il, "
    "            c.tentativi"
    ") SELECT p.id, p.tenant_id::text AS tenant_id, p.sottoscrizione_id::text AS sid, "
    "         p.tentativi, s.url, s.segreto_cifrato, s.segreto_precedente_cifrato, "
    "         s.segreto_precedente_scade_il, e.id AS evento_id, e.tipo, "
    "         e.risorsa_tipo, e.risorsa_id, e.creato_il, e.dati "
    "  FROM presi p JOIN public.webhook_sottoscrizioni s ON s.id = p.sottoscrizione_id "
    "  JOIN public.eventi_cliente e ON e.id = p.evento_id AND e.creato_il = p.evento_creato_il "
    "  ORDER BY p.id"
)


@con_ambito_conservatore("webhook: prelievo delle consegne di tutti gli enti")
async def preleva_consegne(*, adesso: datetime, lotto: int, lease_secondi: int,
                           sessione_factory: Any = None) -> list[ConsegnaDaFare]:
    fabbrica = sessione_factory or _fabbrica_predefinita()
    lease = str(uuid.uuid4())
    async with fabbrica() as s:
        righe = (await s.execute(text(_PRELEVA), {
            "in_consegna": IN_CONSEGNA, "in_coda": IN_CODA, "lease": uuid.UUID(lease),
            "scadenza": adesso + timedelta(seconds=lease_secondi), "adesso": adesso,
            "n": lotto,
        })).all()
    return [
        ConsegnaDaFare(
            id=int(r.id), lease_id=lease, tenant_id=r.tenant_id, sottoscrizione_id=r.sid,
            tentativi=int(r.tentativi), url=r.url, segreto_cifrato=r.segreto_cifrato,
            evento={"id": int(r.evento_id), "tipo": r.tipo, "creato_il": r.creato_il.isoformat(),
                    "risorsa": {"tipo": r.risorsa_tipo, "id": r.risorsa_id},
                    "dati": dict(r.dati or {})},
            segreto_precedente_cifrato=r.segreto_precedente_cifrato,
            segreto_precedente_scade_il=r.segreto_precedente_scade_il,
        )
        for r in righe
    ]


@con_ambito_conservatore("webhook: esito della consegna (lavoratore multi-ente)")
async def chiudi_tentativo(consegna: ConsegnaDaFare, *, stato: str, tentativi: int,
                           prossimo_tentativo: datetime | None, stato_http: int | None,
                           codice: str | None, errore: str | None, adesso: datetime,
                           sessione_factory: Any = None) -> bool:
    """Registra l'esito del tentativo; False se il lease non è più nostro."""
    fabbrica = sessione_factory or _fabbrica_predefinita()
    async with fabbrica() as s:
        r = await s.execute(text(
            "UPDATE public.webhook_consegne SET stato = :stato, tentativi = :tentativi, "
            "prossimo_tentativo = :prossimo, lease_id = NULL, ultimo_stato_http = :http, "
            "ultimo_codice = :codice, ultimo_errore = :errore, ultimo_tentativo_il = :adesso, "
            "consegnata_il = COALESCE(CAST(:consegnata_il AS timestamptz), consegnata_il), "
            "aggiornata_il = :adesso "
            "WHERE id = :id AND stato = :in_consegna AND lease_id = :lease"
        ), {"stato": stato, "tentativi": tentativi, "prossimo": prossimo_tentativo,
            "http": stato_http, "codice": codice, "errore": (errore or None) and errore[:300],
            "adesso": adesso, "consegnata_il": adesso if stato == CONSEGNATA else None,
            "id": consegna.id, "in_consegna": IN_CONSEGNA,
            "lease": uuid.UUID(consegna.lease_id)})
    return bool(r.rowcount)
