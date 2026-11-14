# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Accesso al database del canale SFTP: account, chiavi, lavorazioni con lease.

Servizio: sftp-ingest. Tabelle della migrazione 20261021_016 (modelli
``SftpAccount``, ``SftpChiave``, ``SftpLavorazione`` in libs/shared/models).

R03 f2 (REV-15): ``sftp_segnalazioni`` (migrazione 20261021_022) dice quali
account hanno cartelle da guardare: il server SFTP le scrive quando un cliente
scrive o rinomina in ``in/``, il controllo periodico le scrive per tutti; una
replica del lavoratore prende in lease solo gli account segnalati (SKIP LOCKED)
e le lavorazioni da riprendere si leggono dal database, senza scandire le aree.

Lease (REGOLE §12.9): al massimo una lavorazione IN_CORSO per account e
cartella (indice unico parziale ``uq_sftp_lavorazioni_attiva``). Una replica
la prende inserendola, oppure rilevandola quando il lease è scaduto; ogni
scrittura successiva verifica di essere ancora il detentore. L'orologio dei
lease è quello del database (``now()``), uguale per tutte le repliche.

R05 (VD-C-080): il canale SFTP serve TUTTI gli enti (accesso per nome utente e
chiave prima di sapere l'ente, coda delle lavorazioni e delle segnalazioni):
ogni sessione di questo modulo usa l'ambito conservatore esplicito della Row
Level Security (``_sessione``). I versamenti passano dal gateway con il token
di canale dell'ente.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import JSONB

from libs.shared.db import get_async_session
from libs.shared.db.ambito import ambito_conservatore
from libs.shared.models import SftpAccount, SftpChiave, Tenant, User
from libs.shared.security.revoca import STATI_ENTE_BLOCCATI

IN_CORSO, VERSATA, SCARTATA = "IN_CORSO", "VERSATA", "SCARTATA"

_MOTIVO_AMBITO = "canale SFTP: account, chiavi e lavorazioni di tutti gli enti"


@asynccontextmanager
async def _sessione() -> AsyncIterator[Any]:
    """Sessione del database nell'ambito conservatore (vedi docstring del modulo)."""
    with ambito_conservatore(_MOTIVO_AMBITO, registra=False):
        async with get_async_session() as sessione:
            yield sessione


@dataclass(frozen=True)
class AccountAccesso:
    """Dati per autenticare un account SFTP."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    nome_utente: str
    #: Chiavi attive: impronta «SHA256:…» → «tipo base64».
    chiavi: dict[str, str]
    #: R10 (D12): indirizzi o reti ammessi (vuoto = nessun filtro, salvo obbligo)
    ip_ammessi: tuple[str, ...] = ()
    #: R10 (D12): SFTP_ALLOWLIST_OBBLIGATORIA dell'ente (senza elenco: accesso negato)
    elenco_obbligatorio: bool = False


@dataclass(frozen=True)
class AccountLavoro:
    """Dati per versare per conto di un account (token di servizio C2)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    responsabile_id: uuid.UUID
    ruolo: str


@dataclass
class Lavorazione:
    id: uuid.UUID
    tenant_id: uuid.UUID
    account_id: uuid.UUID
    cartella: str
    tentativi: int
    impronta_indice: str | None
    chiave_idempotenza: str | None
    caricamenti: dict[str, Any]
    creata_il: datetime | None = None


def _toccate(esito: Any) -> int:
    """Righe toccate da UPDATE/DELETE (``CursorResult.rowcount``)."""
    return int(getattr(esito, "rowcount", 0) or 0)


def _ruolo(valore: Any) -> str:
    return str(getattr(valore, "value", valore))


async def account_per_accesso(nome_utente: str) -> AccountAccesso | None:
    """Account che può accedere: ATTIVO, ente non sospeso/disattivato, responsabile
    attivo dello stesso ente. Altrimenti ``None`` (accesso negato)."""
    async with _sessione() as s:
        riga = (await s.execute(
            select(SftpAccount.id, SftpAccount.tenant_id, SftpAccount.nome_utente,
                   SftpAccount.ip_ammessi)
            .join(Tenant, Tenant.id == SftpAccount.tenant_id)
            .join(User, User.id == SftpAccount.responsabile_id)
            .where(SftpAccount.nome_utente == nome_utente,
                   SftpAccount.stato == "ATTIVO",
                   Tenant.stato.notin_(list(STATI_ENTE_BLOCCATI)),
                   User.attivo.is_(True),
                   User.tenant_id == SftpAccount.tenant_id)
        )).one_or_none()
        if riga is None:
            return None
        chiavi = (await s.execute(
            select(SftpChiave.impronta, SftpChiave.chiave_pubblica)
            .where(SftpChiave.account_id == riga.id, SftpChiave.revocata_il.is_(None))
        )).all()
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import (
        ALLOWLIST_OBBLIGATORIA,
        SFTP_ALLOWLIST_OBBLIGATORIA,
    )

    obbligo = (await parametri([SFTP_ALLOWLIST_OBBLIGATORIA], riga.tenant_id)
               ).get(SFTP_ALLOWLIST_OBBLIGATORIA) == ALLOWLIST_OBBLIGATORIA
    return AccountAccesso(riga.id, riga.tenant_id, riga.nome_utente,
                          {c.impronta: c.chiave_pubblica for c in chiavi},
                          tuple(riga.ip_ammessi or ()), obbligo)


async def registra_accesso(account_id: uuid.UUID | None, tenant_id: uuid.UUID | None, *,
                           ip: str | None, esito: str, motivo: str | None = None) -> None:
    """R10 (D12): accesso SFTP (riuscito o negato per indirizzo) nel registro
    attività con l'IP REALE del cliente. Senza account (nome sconosciuto) nessuna
    voce: il nome non si rivela e il log basta."""
    import json

    from libs.shared.security.rete import indirizzo

    if tenant_id is None:
        return
    valido = indirizzo(ip or "")
    dettagli = {"account_id": str(account_id), "ip": str(valido or ip or ""),
                **({"motivo": motivo} if motivo else {})}
    async with _sessione() as s:
        await s.execute(text(
            "INSERT INTO public.audit_global (id, tenant_id, evento, dettagli, ip_address) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), :e, CAST(:d AS jsonb), "
            "CAST(:ip AS inet))"),
            {"t": str(tenant_id), "e": f"sftp.accesso.{esito}", "d": json.dumps(dettagli),
             "ip": str(valido) if valido is not None else None})


async def account_per_lavoro(account_id: uuid.UUID) -> AccountLavoro | None:
    async with _sessione() as s:
        riga = (await s.execute(
            select(SftpAccount.id, SftpAccount.tenant_id, SftpAccount.responsabile_id, User.ruolo)
            .join(User, User.id == SftpAccount.responsabile_id)
            .where(SftpAccount.id == account_id)
        )).one_or_none()
    if riga is None:
        return None
    return AccountLavoro(riga.id, riga.tenant_id, riga.responsabile_id, _ruolo(riga.ruolo))


#: Nuova lavorazione, se non ce n'è già una IN_CORSO per account e cartella.
_SQL_NUOVA = (
    "INSERT INTO public.sftp_lavorazioni (tenant_id, account_id, cartella, stato, "
    " detentore, lease_scade_il, tentativi) "
    "VALUES (:t, :a, :c, 'IN_CORSO', :d, now() + make_interval(secs => :s), 1) "
    "ON CONFLICT (account_id, cartella) WHERE stato = 'IN_CORSO' DO NOTHING "
    "RETURNING id, tenant_id, account_id, cartella, tentativi, impronta_indice, "
    " chiave_idempotenza, caricamenti, created_at")
#: Ripresa di una lavorazione IN_CORSO con lease scaduto (tentativo successivo).
_SQL_RIPRESA = (
    "UPDATE public.sftp_lavorazioni SET detentore = :d, "
    " lease_scade_il = now() + make_interval(secs => :s), "
    " tentativi = tentativi + 1, updated_at = now() "
    "WHERE account_id = :a AND cartella = :c AND stato = 'IN_CORSO' "
    " AND lease_scade_il <= now() "
    "RETURNING id, tenant_id, account_id, cartella, tentativi, impronta_indice, "
    " chiave_idempotenza, caricamenti, created_at")


def _lavorazione(riga: Any) -> Lavorazione:
    m = riga._mapping
    return Lavorazione(m["id"], m["tenant_id"], m["account_id"], m["cartella"], m["tentativi"],
                       m["impronta_indice"], m["chiave_idempotenza"], dict(m["caricamenti"] or {}),
                       m.get("created_at"))


async def prendi_in_carico(
    tenant_id: uuid.UUID, account_id: uuid.UUID, cartella: str, detentore: str,
    lease_secondi: int,
) -> Lavorazione | None:
    """Lease sulla cartella: nuova lavorazione o ripresa di una con lease scaduto.
    ``None`` se un'altra replica la sta lavorando (o il prossimo tentativo non è
    ancora arrivato)."""
    parametri = {"t": tenant_id, "a": account_id, "c": cartella, "d": detentore,
                 "s": lease_secondi}
    async with _sessione() as s:
        riga = (await s.execute(text(_SQL_NUOVA), parametri)).first()
        if riga is None:
            riga = (await s.execute(text(_SQL_RIPRESA), parametri)).first()
    return _lavorazione(riga) if riga is not None else None


async def rinnova_lease(lavorazione_id: uuid.UUID, detentore: str, lease_secondi: int) -> bool:
    async with _sessione() as s:
        esito = await s.execute(text(
            "UPDATE public.sftp_lavorazioni SET updated_at = now(), "
            " lease_scade_il = now() + make_interval(secs => :s) "
            "WHERE id = :i AND detentore = :d AND stato = 'IN_CORSO'"),
            {"i": lavorazione_id, "d": detentore, "s": lease_secondi})
    return _toccate(esito) > 0


async def aggiorna_lavoro(
    lavorazione_id: uuid.UUID, detentore: str, *, impronta_indice: str | None,
    chiave_idempotenza: str | None, caricamenti: dict[str, Any], azzera_tentativi: bool = False,
) -> bool:
    """Salva impronta dell'indice, chiave e sessioni di caricamento (ripresa dopo crash)."""
    istruzione = text(
        "UPDATE public.sftp_lavorazioni SET impronta_indice = :im, chiave_idempotenza = :k, "
        " caricamenti = :c, tentativi = CASE WHEN :z THEN 1 ELSE tentativi END, "
        " updated_at = now() WHERE id = :i AND detentore = :d AND stato = 'IN_CORSO'"
    ).bindparams(bindparam("c", type_=JSONB))
    async with _sessione() as s:
        esito = await s.execute(istruzione, {
            "i": lavorazione_id, "d": detentore, "im": impronta_indice, "k": chiave_idempotenza,
            "c": caricamenti, "z": azzera_tentativi})
    return _toccate(esito) > 0


async def rimanda(lavorazione_id: uuid.UUID, detentore: str, secondi: int,
                  errore: dict[str, Any], *, consuma_tentativo: bool = True) -> bool:
    """Errore temporaneo: rilascia il lease, prossimo tentativo fra ``secondi``.
    ``consuma_tentativo=False`` (quota del piano esaurita): la ripresa non conta
    come tentativo (la ripresa aggiunge 1, qui si toglie)."""
    istruzione = text(
        "UPDATE public.sftp_lavorazioni SET detentore = NULL, "
        " lease_scade_il = now() + make_interval(secs => :s), ultimo_errore = :e, "
        " tentativi = CASE WHEN :c THEN tentativi ELSE tentativi - 1 END, "
        " updated_at = now() WHERE id = :i AND detentore = :d AND stato = 'IN_CORSO'"
    ).bindparams(bindparam("e", type_=JSONB))
    async with _sessione() as s:
        esito = await s.execute(istruzione, {"i": lavorazione_id, "d": detentore, "s": secondi,
                                             "e": errore, "c": consuma_tentativo})
    return _toccate(esito) > 0


async def concludi(lavorazione_id: uuid.UUID, detentore: str, stato: str,
                   pdv_id: str | None, esito: dict[str, Any]) -> bool:
    """VERSATA o SCARTATA con l'esito scritto al cliente; ``False`` se il lease è perso."""
    istruzione = text(
        "UPDATE public.sftp_lavorazioni SET stato = :st, pdv_id = CAST(:p AS uuid), "
        " esito = :e, detentore = NULL, concluso_il = now(), updated_at = now() "
        "WHERE id = :i AND detentore = :d AND stato = 'IN_CORSO'"
    ).bindparams(bindparam("e", type_=JSONB))
    async with _sessione() as s:
        r = await s.execute(istruzione, {"i": lavorazione_id, "d": detentore, "st": stato,
                                         "p": pdv_id, "e": esito})
    return _toccate(r) > 0


async def annulla_presa(lavorazione_id: uuid.UUID, detentore: str) -> None:
    """Cartella non più presente: una presa appena creata (nessun lavoro fatto) si
    elimina; una ripresa di un tentativo precedente si chiude SCARTATA con il motivo."""
    async with _sessione() as s:
        eliminata = await s.execute(text(
            "DELETE FROM public.sftp_lavorazioni WHERE id = :i AND detentore = :d "
            " AND stato = 'IN_CORSO' AND tentativi = 1 AND impronta_indice IS NULL "
            " AND caricamenti = '{}'::jsonb"), {"i": lavorazione_id, "d": detentore})
        if not _toccate(eliminata):
            await s.execute(text(
                "UPDATE public.sftp_lavorazioni SET stato = 'SCARTATA', detentore = NULL, "
                " esito = CAST(:e AS jsonb), concluso_il = now(), updated_at = now() "
                "WHERE id = :i AND detentore = :d AND stato = 'IN_CORSO'"),
                {"i": lavorazione_id, "d": detentore,
                 "e": '{"motivo": "cartella non più presente"}'})


async def esito_versato(account_id: uuid.UUID, cartella: str,
                        impronta_indice: str) -> dict[str, Any] | None:
    """Esito dell'ultima lavorazione VERSATA della stessa cartella con lo stesso
    indice (reinvio o ripresa dopo crash): si riusa senza versare di nuovo."""
    async with _sessione() as s:
        riga = (await s.execute(text(
            "SELECT esito FROM public.sftp_lavorazioni WHERE account_id = :a AND cartella = :c "
            " AND impronta_indice = :im AND stato = 'VERSATA' "
            "ORDER BY concluso_il DESC LIMIT 1"),
            {"a": account_id, "c": cartella, "im": impronta_indice})).first()
    return dict(riga[0]) if riga is not None and riga[0] else None


# ── Lavorazioni da riprendere e segnalazioni (R03 f2, REV-15) ─────────

#: Condizioni di un account le cui cartelle si lavorano: stesse dell'accesso (account
#: ATTIVO, ente non bloccato, responsabile attivo dello stesso ente). Le cartelle
#: degli altri restano in attesa, senza esito, finché tornano operativi.
_ACCOUNT_OPERATIVO = (
    "JOIN public.sftp_account a ON a.id = {alias}.account_id "
    "JOIN public.tenants t ON t.id = a.tenant_id "
    "JOIN public.users u ON u.id = a.responsabile_id "
    "WHERE a.stato = 'ATTIVO' AND u.attivo AND u.tenant_id = a.tenant_id "
    "  AND t.stato::text <> ALL(:bloccati) ")


def _bloccati() -> list[str]:
    return [str(getattr(v, "value", v)) for v in STATI_ENTE_BLOCCATI]


async def lavorazioni_da_riprendere(limite: int) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """(account, ente, cartella) delle lavorazioni IN_CORSO con lease scaduto o
    prossimo tentativo arrivato, degli account operativi: dall'indice
    ``idx_sftp_lavorazioni_lease``, senza leggere il disco."""
    async with _sessione() as s:
        # solo frammenti costanti, valori come parametri
        righe = (await s.execute(text(
            "SELECT l.account_id, l.tenant_id, l.cartella FROM public.sftp_lavorazioni l "  # noqa: S608
            + _ACCOUNT_OPERATIVO.format(alias="l")
            + " AND l.stato = 'IN_CORSO' AND l.lease_scade_il <= now() "
            "ORDER BY l.lease_scade_il LIMIT :n"),
            {"n": limite, "bloccati": _bloccati()})).all()
    return [(r[0], r[1], r[2]) for r in righe]


@dataclass(frozen=True)
class Segnalazione:
    account_id: uuid.UUID
    tenant_id: uuid.UUID
    versione: int
    controllo_completo: bool


async def segnala_attivita(account: Mapping[uuid.UUID, tuple[uuid.UUID, bool]], *,
                           stabilita_secondi: float) -> None:
    """Account con attività in ``in/`` (account → (ente, pronta)). Una cartella
    rinominata «pronta» si guarda subito; altre scritture dopo la stabilità.
    ``versione`` cresce: chi sta guardando l'account non elimina la riga."""
    if not account:
        return
    ids = list(account)
    async with _sessione() as s:
        await s.execute(text(
            "INSERT INTO public.sftp_segnalazioni AS g (account_id, tenant_id, da_guardare_dal) "
            "SELECT x.a, x.t, CASE WHEN x.p THEN now() "
            "                 ELSE now() + make_interval(secs => :s) END "
            "FROM unnest(CAST(:a AS uuid[]), CAST(:t AS uuid[]), CAST(:p AS boolean[])) "
            "  AS x(a, t, p) "
            "ON CONFLICT (account_id) DO UPDATE SET versione = g.versione + 1, "
            " segnalata_il = now(), "
            " da_guardare_dal = LEAST(g.da_guardare_dal, EXCLUDED.da_guardare_dal)"),
            {"a": ids, "t": [account[i][0] for i in ids], "p": [account[i][1] for i in ids],
             "s": float(stabilita_secondi)})


async def richiedi_controllo_completo() -> int:
    """Rete di sicurezza: segnala TUTTI gli account operativi per un controllo
    completo (in/ e lavorazione/, pulizia). Restituisce gli account segnalati."""
    async with _sessione() as s:
        esito = await s.execute(text(
            "INSERT INTO public.sftp_segnalazioni AS g (account_id, tenant_id, "
            " controllo_completo) "
            "SELECT a.id, a.tenant_id, true FROM public.sftp_account a "
            "JOIN public.tenants t ON t.id = a.tenant_id "
            "JOIN public.users u ON u.id = a.responsabile_id "
            "WHERE a.stato = 'ATTIVO' AND u.attivo AND u.tenant_id = a.tenant_id "
            "  AND t.stato::text <> ALL(:bloccati) "
            "ON CONFLICT (account_id) DO UPDATE SET controllo_completo = true, "
            " versione = g.versione + 1, da_guardare_dal = LEAST(g.da_guardare_dal, now())"),
            {"bloccati": _bloccati()})
    return _toccate(esito)


async def prendi_segnalazioni(detentore: str, limite: int,
                              lease_secondi: int) -> list[Segnalazione]:
    """Lease su fino a ``limite`` account segnalati e da guardare ora, degli
    account operativi (gli altri restano segnalati finché tornano operativi)."""
    async with _sessione() as s:
        # solo frammenti costanti, valori come parametri
        righe = (await s.execute(text(
            "UPDATE public.sftp_segnalazioni g SET detentore = :d, "  # noqa: S608
            " lease_scade_il = now() + make_interval(secs => :l) "
            "FROM (SELECT x.account_id FROM public.sftp_segnalazioni x "
            + _ACCOUNT_OPERATIVO.format(alias="x")
            + "  AND x.da_guardare_dal <= now() "
            "  AND (x.lease_scade_il IS NULL OR x.lease_scade_il <= now()) "
            "  ORDER BY x.da_guardare_dal LIMIT :n FOR UPDATE OF x SKIP LOCKED) scelte "
            "WHERE g.account_id = scelte.account_id "
            "RETURNING g.account_id, g.tenant_id, g.versione, g.controllo_completo"),
            {"d": detentore, "l": lease_secondi, "n": limite,
             "bloccati": _bloccati()})).all()
    return [Segnalazione(r[0], r[1], int(r[2]), bool(r[3])) for r in righe]


async def rilascia_segnalazione(seg: Segnalazione, detentore: str, *,
                                riguarda_fra_secondi: float | None) -> bool:
    """Fine dell'esame di un account. ``None``: niente più da guardare, la riga si
    elimina (solo se nessuno l'ha segnalato di nuovo nel frattempo; altrimenti
    torna da guardare subito). Un numero: da riguardare fra tanti secondi.
    Restituisce True se la riga è stata eliminata."""
    async with _sessione() as s:
        if riguarda_fra_secondi is None:
            eliminata = await s.execute(text(
                "DELETE FROM public.sftp_segnalazioni WHERE account_id = :a "
                " AND detentore = :d AND versione = :v"),
                {"a": seg.account_id, "d": detentore, "v": seg.versione})
            if _toccate(eliminata):
                return True
        await s.execute(text(
            "UPDATE public.sftp_segnalazioni SET detentore = NULL, lease_scade_il = NULL, "
            " controllo_completo = CASE WHEN versione = :v THEN false "
            "                      ELSE controllo_completo END, "
            " da_guardare_dal = CASE WHEN versione = :v "
            "   THEN now() + make_interval(secs => :r) ELSE now() END "
            "WHERE account_id = :a AND detentore = :d"),
            {"a": seg.account_id, "d": detentore, "v": seg.versione,
             "r": float(riguarda_fra_secondi or 0)})
    return False
