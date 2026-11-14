# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/infrastructure/repo_eventi.py
Elenco degli eventi per i clienti su PostgreSQL — contratto C3.

- ``registra_evento``: UNA istruzione (CTE) inserisce l'evento e, solo se è
  nuovo, una consegna IN_CODA per ogni sottoscrizione attiva dell'ente che
  chiede quel tipo. Riconsegna Kafka dello stesso evento: nessuna riga.
  R04 (migrazione 028, VD-C-103): la tabella è partizionata per mese e un
  vincolo unico dovrebbe contenere ``creato_il``, quindi la deduplica è fatta
  così: lock consultivo di transazione sulla chiave (ente, tipo, risorsa),
  poi, in un'istruzione SUCCESSIVA (istantanea nuova, vede chi ha appena
  committato la stessa chiave), INSERT solo se nessun evento con quella chiave
  esiste in alcuna partizione ancora presente (indice
  ``idx_eventi_cliente_risorsa`` per partizione: una ricerca per partizione).
  Garanzia: nessun doppione per tutta la retention (90 giorni predefiniti);
  una riconsegna Kafka più vecchia della retention è fuori dalla ritenzione
  dei topic. La consegna porta ``evento_creato_il`` (chiave di partizione delle
  consegne, uguale a quella dell'evento: si staccano insieme).
  Nessuna chiamata HTTP qui: il consumer non si blocca mai sui destinatari.
- ``pubblica_eventi`` (R03 f2, REV-13): assegna la ``posizione`` agli eventi
  già registrati (committati) e non ancora pubblicati. È un passo SERIALE: la
  riga contatore ``eventi_cliente_pubblicazione`` è bloccata dalla transazione
  che assegna (``FOR UPDATE SKIP LOCKED``: un'altra replica, se la trova
  occupata, salta il giro), quindi le posizioni diventano visibili in ordine
  crescente e senza buchi;
- ``elenca_eventi``: paginazione a cursore sulla POSIZIONE (indice
  ``(tenant_id, posizione)``), mai ``COUNT(*)``; ``altri`` si ricava leggendo
  una riga in più. Prima il cursore era l'id con un ritardo di visibilità di
  2 s: un'insert con commit più lento (lock, fsync, pausa) aveva un id minore di
  eventi già letti e un client in polling la perdeva per sempre. Un evento
  diventa leggibile solo con la posizione, assegnata dopo il suo commit da un
  unico scrittore alla volta: nessun evento con posizione minore può comparire
  dopo che un client ha letto una posizione maggiore.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import String

from app.domain.eventi import EventoCliente
from app.domain.webhook import IN_CODA
from libs.shared.db.ambito import con_ambito_conservatore


def _fabbrica_predefinita() -> Any:
    from libs.shared.db import get_async_session

    return get_async_session


def _fabbrica_lettura() -> Any:
    """R04 (piano §6.4): l'elenco a cursore legge dalla replica se configurata
    (``DATABASE_URL_LETTURA``). Nessun salto possibile: la replica applica i
    commit nell'ordine del primario, quindi vede sempre un prefisso delle
    posizioni pubblicate; il ritardo della replica ritarda, non nasconde."""
    from libs.shared.db import get_async_session_lettura

    return get_async_session_lettura


@dataclass(frozen=True)
class EsitoRegistrazione:
    evento_id: int | None  # None = già registrato (riconsegna)
    consegne: int


_CLIENTE_ESISTE = "SELECT 1 FROM public.tenants WHERE id = CAST(:tenant AS uuid)"

_BLOCCA_CHIAVE = (
    "SELECT pg_advisory_xact_lock(hashtextextended("
    "  CAST(:tenant AS text) || '|' || :tipo || '|' || :risorsa_id, 7))"
)

_REGISTRA = (
    "WITH nuovo AS ("
    "  INSERT INTO public.eventi_cliente "
    "    (tenant_id, tipo, risorsa_tipo, risorsa_id, dati, origine) "
    "  SELECT CAST(:tenant AS uuid), CAST(:tipo AS varchar), CAST(:risorsa_tipo AS varchar), "
    "         CAST(:risorsa_id AS varchar), "
    "         CAST(:dati AS jsonb), CAST(:origine AS varchar) "
    "  WHERE NOT EXISTS (SELECT 1 FROM public.eventi_cliente g "
    "    WHERE g.tenant_id = CAST(:tenant AS uuid) AND g.tipo = CAST(:tipo AS varchar) "
    "      AND g.risorsa_id = CAST(:risorsa_id AS varchar)) "
    "  RETURNING id, tenant_id, tipo, creato_il"
    "), consegne AS ("
    "  INSERT INTO public.webhook_consegne "
    "    (tenant_id, sottoscrizione_id, evento_id, evento_creato_il, stato, "
    "     prossimo_tentativo) "
    "  SELECT n.tenant_id, s.id, n.id, n.creato_il, :in_coda, now() "
    "  FROM nuovo n JOIN public.webhook_sottoscrizioni s "
    "    ON s.tenant_id = n.tenant_id AND s.attiva AND s.eliminata_il IS NULL "
    "   AND s.tipi @> jsonb_build_array(n.tipo) "
    "  RETURNING 1"
    ") SELECT (SELECT id FROM nuovo) AS evento_id, "
    "         (SELECT count(*) FROM consegne) AS consegne"
)


# Evento GLOBALE (catena.checkpoint, contratto K1; R04 correzioni, rev4 rilievo 10):
# UNA riga con ``tenant_id`` NULL, letta da tutti gli enti in ``elenca_eventi``,
# invece di una copia per ente ogni minuto (1.440 × enti righe al giorno). Le
# consegne webhook si creano solo per le sottoscrizioni attive (di enti ATTIVI)
# a quel tipo. Stessa deduplica: lock sulla chiave «*|tipo|risorsa» e NOT EXISTS.
_REGISTRA_TUTTI = (
    "WITH nuovo AS ("
    "  INSERT INTO public.eventi_cliente "
    "    (tenant_id, tipo, risorsa_tipo, risorsa_id, dati, origine) "
    "  SELECT NULL, CAST(:tipo AS varchar), CAST(:risorsa_tipo AS varchar), "
    "         CAST(:risorsa_id AS varchar), CAST(:dati AS jsonb), CAST(:origine AS varchar) "
    "  WHERE NOT EXISTS (SELECT 1 FROM public.eventi_cliente g "
    "      WHERE g.tenant_id IS NULL AND g.tipo = CAST(:tipo AS varchar) "
    "        AND g.risorsa_id = CAST(:risorsa_id AS varchar)) "
    "  RETURNING id, tipo, creato_il"
    "), consegne AS ("
    "  INSERT INTO public.webhook_consegne "
    "    (tenant_id, sottoscrizione_id, evento_id, evento_creato_il, stato, "
    "     prossimo_tentativo) "
    "  SELECT s.tenant_id, s.id, n.id, n.creato_il, :in_coda, now() "
    "  FROM nuovo n JOIN public.webhook_sottoscrizioni s "
    "    ON s.attiva AND s.eliminata_il IS NULL AND s.tipi @> jsonb_build_array(n.tipo) "
    "  JOIN public.tenants t ON t.id = s.tenant_id AND t.stato = 'ATTIVO' "
    "  RETURNING 1"
    ") SELECT (SELECT id FROM nuovo) AS evento_id, "
    "         (SELECT count(*) FROM consegne) AS consegne"
)


class ClienteInesistente(Exception):
    """Il tenant_id dell'evento non esiste: l'evento non si registra."""


@con_ambito_conservatore(
    "eventi per i clienti: registrazione dal consumer (eventi di tutti gli enti e globali)")
async def registra_evento(evento: EventoCliente, *, origine: str | None,
                          sessione_factory: Any = None) -> EsitoRegistrazione:
    fabbrica = sessione_factory or _fabbrica_predefinita()
    if evento.tutti_gli_enti:
        async with fabbrica() as s:
            await s.execute(text(_BLOCCA_CHIAVE), {
                "tenant": "*", "tipo": evento.tipo, "risorsa_id": evento.risorsa_id})
            riga = (await s.execute(text(_REGISTRA_TUTTI), {
                "tipo": evento.tipo, "risorsa_tipo": evento.risorsa_tipo,
                "risorsa_id": evento.risorsa_id,
                "dati": json.dumps(evento.dati, ensure_ascii=False, default=str),
                "origine": (origine or "")[:200] or None, "in_coda": IN_CODA,
            })).one()
        return EsitoRegistrazione(
            evento_id=int(riga.evento_id) if riga.evento_id is not None else None,
            consegne=int(riga.consegne or 0),
        )
    async with fabbrica() as s:
        if (await s.execute(text(_CLIENTE_ESISTE), {"tenant": evento.tenant_id})).first() is None:
            raise ClienteInesistente(evento.tenant_id)
        await s.execute(text(_BLOCCA_CHIAVE), {
            "tenant": evento.tenant_id, "tipo": evento.tipo, "risorsa_id": evento.risorsa_id})
        riga = (await s.execute(text(_REGISTRA), {
            "tenant": evento.tenant_id, "tipo": evento.tipo,
            "risorsa_tipo": evento.risorsa_tipo, "risorsa_id": evento.risorsa_id,
            "dati": json.dumps(evento.dati, ensure_ascii=False, default=str),
            "origine": (origine or "")[:200] or None, "in_coda": IN_CODA,
        })).one()
    return EsitoRegistrazione(
        evento_id=int(riga.evento_id) if riga.evento_id is not None else None,
        consegne=int(riga.consegne or 0),
    )


@dataclass(frozen=True)
class RigaEvento:
    id: int
    tipo: str
    risorsa_tipo: str
    risorsa_id: str
    creato_il: datetime
    dati: dict[str, Any]


_BLOCCA_CONTATORE = text(
    "SELECT ultima_posizione FROM public.eventi_cliente_pubblicazione "
    "WHERE id = 1 FOR UPDATE SKIP LOCKED")
# Statement distinto dal blocco: in READ COMMITTED prende una nuova istantanea,
# che vede ogni pubblicazione committata prima di ottenere il blocco.
_ASSEGNA_POSIZIONI = text(
    "WITH da AS ("
    "  SELECT id, creato_il, row_number() OVER (ORDER BY id) AS n FROM public.eventi_cliente "
    "  WHERE posizione IS NULL ORDER BY id LIMIT :n"
    ") UPDATE public.eventi_cliente e SET posizione = :base + da.n "
    # R04: con la chiave di partizione l'UPDATE va solo nella partizione dell'evento
    "FROM da WHERE e.id = da.id AND e.creato_il = da.creato_il AND e.posizione IS NULL "
    "RETURNING e.posizione")
_AGGIORNA_CONTATORE = text(
    "UPDATE public.eventi_cliente_pubblicazione SET ultima_posizione = :ultima, "
    "aggiornato_il = now() WHERE id = 1")


@con_ambito_conservatore("eventi per i clienti: pubblicazione in ordine di tutti gli enti")
async def pubblica_eventi(*, limite: int = 5000, sessione_factory: Any = None) -> int:
    """Assegna la posizione a fino a ``limite`` eventi non ancora pubblicati, in
    ordine di id; restituisce quanti. 0 anche se un'altra replica sta
    pubblicando (riga contatore occupata): ci riproverà il giro successivo."""
    fabbrica = sessione_factory or _fabbrica_predefinita()
    async with fabbrica() as s:
        base = (await s.execute(_BLOCCA_CONTATORE)).scalar_one_or_none()
        if base is None:
            return 0
        posizioni = [int(r[0]) for r in (await s.execute(
            _ASSEGNA_POSIZIONI, {"n": int(limite), "base": int(base)})).all()]
        if posizioni:
            await s.execute(_AGGIORNA_CONTATORE, {"ultima": max(posizioni)})
    return len(posizioni)


@dataclass(frozen=True)
class RigaEventoPubblicato(RigaEvento):
    posizione: int


async def elenca_eventi(tenant_id: str, *, dopo: int, limite: int, tipi: list[str],
                        sessione_factory: Any = None,
                        ) -> tuple[list[RigaEventoPubblicato], bool]:
    """Eventi PUBBLICATI dell'ente e quelli GLOBALI (``tenant_id`` NULL, es.
    ``catena.checkpoint``) con posizione > ``dopo``, in ordine di posizione;
    (righe, altri). Due letture ordinate e limitate dall'indice
    ``(tenant_id, posizione)`` (che contiene anche i NULL) fuse per posizione:
    la posizione è unica e crescente per tutti gli eventi, quindi il cursore
    non salta nulla."""
    filtro_tipo = " AND tipo = ANY(:tipi)" if tipi else ""
    colonne = "SELECT id, tipo, risorsa_tipo, risorsa_id, creato_il, dati, posizione "
    condizione = f"posizione IS NOT NULL AND posizione > :dopo{filtro_tipo} "
    istruzione = text(
        "SELECT * FROM ("  # noqa: S608 — solo frammenti costanti, valori come parametri
        f"  ({colonne}FROM public.eventi_cliente WHERE tenant_id = CAST(:tenant AS uuid) "
        f"    AND {condizione}ORDER BY posizione LIMIT :n) "
        "  UNION ALL "
        f"  ({colonne}FROM public.eventi_cliente WHERE tenant_id IS NULL "
        f"    AND {condizione}ORDER BY posizione LIMIT :n)"
        ") x ORDER BY posizione LIMIT :n"
    )
    parametri: dict[str, Any] = {"tenant": tenant_id, "dopo": dopo, "n": limite + 1}
    if tipi:
        istruzione = istruzione.bindparams(bindparam("tipi", type_=ARRAY(String)))
        parametri["tipi"] = list(tipi)
    fabbrica = sessione_factory or _fabbrica_lettura()
    async with fabbrica() as s:
        righe = (await s.execute(istruzione, parametri)).all()
    eventi = [RigaEventoPubblicato(int(r.id), r.tipo, r.risorsa_tipo, r.risorsa_id, r.creato_il,
                                   dict(r.dati or {}), int(r.posizione))
              for r in righe[:limite]]
    return eventi, len(righe) > limite


def rappresenta_evento(riga: RigaEvento) -> dict[str, Any]:
    """Forma pubblica di un evento: la stessa nell'API e nel corpo dei webhook
    (nell'elenco in più la ``posizione``, il cursore dell'evento)."""
    rappresentato: dict[str, Any] = {
        "id": riga.id,
        "tipo": riga.tipo,
        "creato_il": riga.creato_il.isoformat(),
        "risorsa": {"tipo": riga.risorsa_tipo, "id": riga.risorsa_id},
        "dati": riga.dati,
    }
    if isinstance(riga, RigaEventoPubblicato):
        rappresentato["posizione"] = riga.posizione
    return rappresentato
