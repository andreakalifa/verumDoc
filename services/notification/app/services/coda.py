# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/services/coda.py
Coda di uscita delle notifiche su public.notifications — VD-C-024, REGOLE §21.3.

Stati: IN_CODA → IN_INVIO → INVIATA | (IN_CODA con prossimo_tentativo) |
NON_PARTITA (con motivo). Ogni riga ha il suo cliente (tenant_id obbligatorio:
un messaggio senza cliente valido non si costruisce nemmeno).
Deduplica ATOMICA per (cliente, tipo, destinatario, oggetto) sull'indice unico
``uq_notifications_dedup`` (migrazione 20261020_013): ON CONFLICT DO NOTHING.
Il prelievo usa FOR UPDATE SKIP LOCKED: più repliche non inviano due volte lo
stesso messaggio; un messaggio rimasto IN_INVIO dopo un crash torna in coda.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text

IN_CODA = "IN_CODA"
IN_INVIO = "IN_INVIO"
INVIATA = "INVIATA"
NON_PARTITA = "NON_PARTITA"


@dataclass(frozen=True)
class NuovoMessaggio:
    tenant_id: str
    tipo: str  # evento di origine, es. «aip.conserved»
    oggetto_id: str  # id dell'oggetto (pdv_id, aip_id) per la deduplica
    destinatario: str
    oggetto: str
    testo: str
    html: str | None = None
    canale: str = "email"
    # R12a (VD-C-152): segreti SIGILLATI per la posta (nome → sigillo), aperti solo
    # all'invio; il testo porta i segnaposto. Mai un segreto in chiaro qui.
    segreti: dict[str, str] | None = None

    def __post_init__(self) -> None:
        # Cliente obbligatorio (notifications.tenant_id NOT NULL + FK): mai una
        # riga «di nessuno». Il chiamante risolve il cliente o scarta l'evento.
        try:
            normalizzato = str(UUID(str(self.tenant_id)))
        except (ValueError, TypeError):
            raise ValueError(
                f"notifica senza cliente valido: tenant_id={self.tenant_id!r}") from None
        object.__setattr__(self, "tenant_id", normalizzato)
        if not self.oggetto_id:
            raise ValueError("notifica senza oggetto_id: la deduplica non è possibile")


@dataclass
class MessaggioUscita:
    id: str
    tenant_id: str | None
    tipo: str
    canale: str
    destinatario: str
    tentativi: int
    payload: dict[str, Any] = field(default_factory=dict)


class CodaUscita(Protocol):
    async def accoda(self, m: NuovoMessaggio, adesso: datetime) -> bool: ...
    async def registra_non_partita(
        self, m: NuovoMessaggio, motivo: str, adesso: datetime
    ) -> bool: ...
    async def preleva(self, adesso: datetime, limite: int) -> list[MessaggioUscita]: ...
    async def segna_inviata(self, id_: str, adesso: datetime, message_id: str | None) -> None: ...
    async def riprogramma(
        self, id_: str, tentativi: int, prossimo: datetime, errore: str, adesso: datetime
    ) -> None: ...
    async def segna_non_partita(
        self, id_: str, tentativi: int, errore: str, adesso: datetime
    ) -> None: ...
    async def recupera_bloccati(self, soglia: datetime, adesso: datetime) -> int: ...


def _payload(m: NuovoMessaggio) -> str:
    corpo: dict[str, Any] = {"oggetto_id": m.oggetto_id, "oggetto": m.oggetto,
                             "testo": m.testo, "html": m.html}
    if m.segreti:
        corpo["segreti"] = dict(m.segreti)
    return json.dumps(corpo, ensure_ascii=False)


#: R12a (VD-C-152): dopo l'invio la riga non conserva segreti: si tolgono i sigilli
#: e, per gli inviti accodati in chiaro prima di R12a, il collegamento dal testo
MINIMIZZA_DOPO_INVIO = (
    "(payload_json - 'segreti') || CASE WHEN tipo = 'accesso.invito' THEN "
    "jsonb_build_object('testo', regexp_replace(COALESCE(payload_json->>'testo', ''), "
    "'#invito=[A-Za-z0-9._~-]+', '#invito=[rimosso dopo l''invio]', 'g')) "
    "ELSE '{}'::jsonb END"
)


_INSERISCI = (
    # Cast espliciti: asyncpg deve dedurre un solo tipo per parametro usato due volte.
    # ON CONFLICT sull'indice unico di deduplica: atomico anche fra repliche.
    "INSERT INTO public.notifications "
    "(id, tenant_id, tipo, canale, destinatario, payload_json, stato, tentativi, errore, "
    " created_at, updated_at) "
    "VALUES (gen_random_uuid(), CAST(:tenant_id AS uuid), "
    "       CAST(:tipo AS varchar), CAST(:canale AS varchar), CAST(:destinatario AS varchar), "
    "       CAST(:payload AS jsonb), CAST(:stato AS varchar), 0, CAST(:errore AS text), "
    "       CAST(:adesso AS timestamptz), CAST(:adesso AS timestamptz)) "
    "ON CONFLICT (tenant_id, tipo, destinatario, ((payload_json->>'oggetto_id'))) DO NOTHING"
)


_PRELEVA = (
    "UPDATE public.notifications SET stato = :in_invio, updated_at = :adesso "
    "WHERE id IN (SELECT id FROM public.notifications "
    "  WHERE stato = :in_coda AND (prossimo_tentativo IS NULL "
    "        OR prossimo_tentativo <= :adesso) "
    "  ORDER BY created_at LIMIT :limite FOR UPDATE SKIP LOCKED) "
    "RETURNING id::text AS id, tenant_id::text AS tenant_id, tipo, canale, "
    "          destinatario, tentativi, payload_json"
)


class CodaUscitaSQL:
    """Implementazione su PostgreSQL (tabella public.notifications)."""

    def __init__(self, sessione_factory: Any = None) -> None:
        if sessione_factory is None:
            from libs.shared.db.ambito import fabbrica_sessione_conservatore

            # R05 (VD-C-080): coda unica di tutti gli enti, usata dai lavoratori
            sessione_factory = fabbrica_sessione_conservatore(
                "coda delle notifiche (email/PEC) di tutti gli enti")
        self._sessione = sessione_factory

    async def _inserisci(
        self, m: NuovoMessaggio, stato: str, errore: str | None, adesso: datetime
    ) -> bool:
        async with self._sessione() as s:
            r = await s.execute(
                text(_INSERISCI),
                {
                    "tenant_id": m.tenant_id,
                    "tipo": m.tipo,
                    "canale": m.canale,
                    "destinatario": m.destinatario,
                    "payload": _payload(m),
                    "stato": stato,
                    "errore": errore,
                    "adesso": adesso,
                },
            )
            return bool(r.rowcount)

    async def accoda(self, m: NuovoMessaggio, adesso: datetime) -> bool:
        return await self._inserisci(m, IN_CODA, None, adesso)

    async def registra_non_partita(self, m: NuovoMessaggio, motivo: str, adesso: datetime) -> bool:
        return await self._inserisci(m, NON_PARTITA, motivo, adesso)

    async def preleva(self, adesso: datetime, limite: int) -> list[MessaggioUscita]:
        async with self._sessione() as s:
            parametri = {
                "in_invio": IN_INVIO,
                "in_coda": IN_CODA,
                "adesso": adesso,
                "limite": limite,
            }
            righe = (await s.execute(text(_PRELEVA), parametri)).mappings().all()
        return [
            MessaggioUscita(
                id=r["id"],
                tenant_id=r["tenant_id"],
                tipo=r["tipo"],
                canale=r["canale"],
                destinatario=r["destinatario"],
                tentativi=int(r["tentativi"] or 0),
                payload=dict(r["payload_json"] or {}),
            )
            for r in righe
        ]

    async def segna_inviata(self, id_: str, adesso: datetime, message_id: str | None) -> None:
        async with self._sessione() as s:
            await s.execute(
                text(
                    "UPDATE public.notifications SET stato = :stato, inviato_at = :adesso, "  # noqa: S608 — SQL da frammenti costanti, valori come parametri
                    "tentativi = tentativi + 1, errore = NULL, updated_at = :adesso, "
                    f"payload_json = {MINIMIZZA_DOPO_INVIO} "
                    "  || jsonb_build_object('message_id', CAST(:mid AS text)) "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"stato": INVIATA, "adesso": adesso, "id": id_, "mid": message_id},
            )

    async def riprogramma(
        self, id_: str, tentativi: int, prossimo: datetime, errore: str, adesso: datetime
    ) -> None:
        async with self._sessione() as s:
            await s.execute(
                text(
                    "UPDATE public.notifications SET stato = :stato, tentativi = :t, "
                    "prossimo_tentativo = :p, errore = :e, updated_at = :adesso "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {
                    "stato": IN_CODA,
                    "t": tentativi,
                    "p": prossimo,
                    "e": errore,
                    "adesso": adesso,
                    "id": id_,
                },
            )

    async def segna_non_partita(
        self, id_: str, tentativi: int, errore: str, adesso: datetime
    ) -> None:
        async with self._sessione() as s:
            await s.execute(
                text(
                    "UPDATE public.notifications SET stato = :stato, tentativi = :t, "
                    "prossimo_tentativo = NULL, errore = :e, updated_at = :adesso "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"stato": NON_PARTITA, "t": tentativi, "e": errore, "adesso": adesso, "id": id_},
            )

    async def recupera_bloccati(self, soglia: datetime, adesso: datetime) -> int:
        async with self._sessione() as s:
            r = await s.execute(
                text(
                    "UPDATE public.notifications SET stato = :in_coda, updated_at = :adesso "
                    "WHERE stato = :in_invio AND updated_at < :soglia"
                ),
                {"in_coda": IN_CODA, "in_invio": IN_INVIO, "soglia": soglia, "adesso": adesso},
            )
            return int(r.rowcount or 0)
