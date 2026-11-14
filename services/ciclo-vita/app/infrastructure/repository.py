# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/infrastructure/repository.py — accesso al database delle pratiche.

Riferimenti: REGOLE_SVILUPPO §16.3 (SQL con parametri, ``CAST(:x AS uuid)``),
§12.6 (controlla poi crea sotto lock del padre), §12.7 (lock nello stesso
ordine: pratica, poi PdA, poi PdV), §14.6 (figli cercati dentro il padre), §9.3.

Tutte le funzioni lavorano nella sessione (transazione) del chiamante.
Elenchi a cursore ``(created_at DESC, id DESC)`` o per ``id`` crescente: mai
OFFSET né ``COUNT(*)`` su tabelle grandi (i conteggi per esito di UNA pratica
usano l'indice ``(pratica_id, esito, id)``).
"""
from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

COLONNE_PRATICA = (
    "p.id, p.tenant_id, p.tipo, p.stato, p.versione, p.motivo, p.dati, p.impronta_piano, "
    "p.richiede_nulla_osta, p.pdv_id, p.pdv_risultato_id, p.idempotency_key, p.creata_da, "
    "p.creata_ruolo, p.created_at, p.updated_at, p.conclusa_il, p.lavoro, "
    "p.prossimo_tentativo, p.tentativi, p.lease_detentore, p.lease_scade_il, "
    "p.ultimo_errore, p.conteggi, p.richiede_autorizzazione_produttore")


class CursoreNonValido(ValueError):
    """Cursore illeggibile o di un altro elenco."""


def codifica_cursore(elenco: str, quando: datetime | None, ultimo: Any) -> str:
    dati = {"e": elenco, "t": quando.isoformat() if quando else None, "i": str(ultimo)}
    return base64.urlsafe_b64encode(json.dumps(dati, separators=(",", ":")).encode()
                                    ).decode().rstrip("=")


def decodifica_cursore(elenco: str, cursore: str) -> tuple[datetime | None, str]:
    try:
        dati = json.loads(base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4)))
        if dati.get("e") != elenco:
            raise CursoreNonValido("cursore di un altro elenco")
        quando = datetime.fromisoformat(dati["t"]) if dati.get("t") else None
        ultimo = str(dati["i"])
    except CursoreNonValido:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CursoreNonValido(str(exc)) from None
    return quando, ultimo


def _json(valore: Any) -> str:
    return json.dumps(valore, default=str, sort_keys=True)


async def leggi_pratica(session: AsyncSession, pratica_id: uuid.UUID, *,
                        per_aggiornare: bool = False) -> dict[str, Any] | None:
    sql = f"SELECT {COLONNE_PRATICA} FROM public.pratiche p WHERE p.id = :id"  # noqa: S608
    if per_aggiornare:
        sql += " FOR UPDATE"
    riga = (await session.execute(text(sql), {"id": pratica_id})).mappings().first()
    return dict(riga) if riga else None


async def pratica_per_idempotenza(session: AsyncSession, tenant_id: uuid.UUID, tipo: str,
                                  chiave: str) -> dict[str, Any] | None:
    riga = (await session.execute(text(
        f"SELECT {COLONNE_PRATICA} FROM public.pratiche p "  # noqa: S608
        "WHERE p.tenant_id = :t AND p.tipo = :tipo AND p.idempotency_key = :k"),
        {"t": tenant_id, "tipo": tipo, "k": chiave})).mappings().first()
    return dict(riga) if riga else None


async def inserisci_pratica(session: AsyncSession, *, tenant_id: uuid.UUID, tipo: str,
                            stato: str, motivo: str, dati: Mapping[str, Any],
                            creata_da: str | None, creata_ruolo: str,
                            impronta_piano: str | None = None,
                            richiede_nulla_osta: bool = False,
                            richiede_autorizzazione_produttore: bool = False,
                            pdv_id: uuid.UUID | None = None,
                            idempotency_key: str | None = None,
                            lavoro: str | None = None, adesso: datetime) -> uuid.UUID:
    pratica_id = uuid.uuid4()
    await session.execute(text(
        "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, dati, impronta_piano, "
        "richiede_nulla_osta, pdv_id, idempotency_key, creata_da, creata_ruolo, created_at, "
        "updated_at, lavoro, prossimo_tentativo, richiede_autorizzazione_produttore) VALUES "
        "(:id, :t, :tipo, :stato, :motivo, "
        "CAST(:dati AS jsonb), :impronta, :nulla_osta, :pdv, :chiave, CAST(:da AS uuid), "
        ":ruolo, :adesso, :adesso, :lavoro, :adesso, :autorizzazione)"),
        {"id": pratica_id, "t": tenant_id, "tipo": tipo, "stato": stato, "motivo": motivo,
         "autorizzazione": richiede_autorizzazione_produttore,
         "dati": _json(dict(dati)), "impronta": impronta_piano, "nulla_osta": richiede_nulla_osta,
         "pdv": pdv_id, "chiave": idempotency_key, "da": creata_da, "ruolo": creata_ruolo,
         "adesso": adesso, "lavoro": lavoro})
    return pratica_id


async def inserisci_stato(session: AsyncSession, *, pratica_id: uuid.UUID, tenant_id: uuid.UUID,
                          da_stato: str | None, a_stato: str, utente_id: str | None,
                          ruolo: str, metodo: str | None, motivo: str | None,
                          allegati: Sequence[str], adesso: datetime) -> None:
    await session.execute(text(
        "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, da_stato, a_stato, utente_id, "
        "ruolo, metodo, motivo, allegati, created_at) VALUES (:p, :t, :da, :a, "
        "CAST(:u AS uuid), :ruolo, :metodo, :motivo, CAST(:allegati AS jsonb), :adesso)"),
        {"p": pratica_id, "t": tenant_id, "da": da_stato, "a": a_stato, "u": utente_id,
         "ruolo": ruolo, "metodo": metodo, "motivo": motivo,
         "allegati": json.dumps(list(allegati)), "adesso": adesso})


async def aggiorna_pratica(session: AsyncSession, pratica_id: uuid.UUID, adesso: datetime,
                           **campi: Any) -> None:
    """UPDATE dei campi indicati (nomi di colonna fissi, mai da input) più
    ``updated_at``; ``dati``/``conteggi``/``ultimo_errore`` sono JSON."""
    ammessi = {"stato", "versione", "dati", "lavoro", "prossimo_tentativo", "tentativi",
               "lease_detentore", "lease_scade_il", "ultimo_errore", "conteggi", "conclusa_il",
               "pdv_risultato_id", "richiede_nulla_osta"}
    assegnazioni = []
    parametri: dict[str, Any] = {"id": pratica_id, "adesso": adesso}
    for nome, valore in campi.items():
        if nome not in ammessi:
            raise ValueError(f"colonna non aggiornabile: {nome}")
        if nome in ("dati", "conteggi", "ultimo_errore"):
            assegnazioni.append(f"{nome} = CAST(:{nome} AS jsonb)")
            parametri[nome] = None if valore is None else _json(valore)
        else:
            assegnazioni.append(f"{nome} = :{nome}")
            parametri[nome] = valore
    assegnazioni.append("updated_at = :adesso")
    await session.execute(text(
        f"UPDATE public.pratiche SET {', '.join(assegnazioni)} WHERE id = :id"),  # noqa: S608
        parametri)


async def cronologia(session: AsyncSession, pratica_id: uuid.UUID) -> list[dict[str, Any]]:
    righe = (await session.execute(text(
        "SELECT id, da_stato, a_stato, utente_id, ruolo, metodo, motivo, allegati, created_at "
        "FROM public.pratiche_stati WHERE pratica_id = :p ORDER BY id"), {"p": pratica_id}
    )).mappings().all()
    return [dict(r) for r in righe]


async def allegati(session: AsyncSession, pratica_id: uuid.UUID) -> list[dict[str, Any]]:
    righe = (await session.execute(text(
        "SELECT id, tipo, nome_file, content_type, dimensione, sha256, bucket, chiave, "
        "version_id, conserva_fino_a, firma, caricato_da, created_at "
        "FROM public.pratiche_allegati WHERE pratica_id = :p ORDER BY created_at, id"),
        {"p": pratica_id})).mappings().all()
    return [dict(r) for r in righe]


async def inserisci_allegato(session: AsyncSession, *, pratica_id: uuid.UUID,
                             tenant_id: uuid.UUID, tipo: str, nome_file: str, content_type: str,
                             dimensione: int, sha256: str, bucket: str, chiave: str,
                             version_id: str | None, conserva_fino_a: datetime,
                             firma: Mapping[str, Any], caricato_da: str | None) -> uuid.UUID:
    """Allegato nuovo; uno identico (stessa pratica, tipo e impronta) si riusa."""
    esistente = (await session.execute(text(
        "SELECT id FROM public.pratiche_allegati WHERE pratica_id = :p AND tipo = :tipo "
        "AND sha256 = :sha"), {"p": pratica_id, "tipo": tipo, "sha": sha256})).scalar()
    if esistente is not None:
        return uuid.UUID(str(esistente))
    allegato_id = uuid.uuid4()
    await session.execute(text(
        "INSERT INTO public.pratiche_allegati (id, pratica_id, tenant_id, tipo, nome_file, "
        "content_type, dimensione, sha256, bucket, chiave, version_id, conserva_fino_a, firma, "
        "caricato_da) VALUES (:id, :p, :t, :tipo, :nome, :ct, :dim, :sha, :b, :k, :v, :fino, "
        "CAST(:firma AS jsonb), CAST(:da AS uuid))"),
        {"id": allegato_id, "p": pratica_id, "t": tenant_id, "tipo": tipo, "nome": nome_file,
         "ct": content_type, "dim": dimensione, "sha": sha256, "b": bucket, "k": chiave,
         "v": version_id, "fino": conserva_fino_a, "firma": _json(dict(firma)),
         "da": caricato_da})
    return allegato_id


async def conteggi_oggetti(session: AsyncSession, pratica_id: uuid.UUID) -> dict[str, int]:
    righe = (await session.execute(text(
        "SELECT esito, count(*) AS n FROM public.pratiche_oggetti WHERE pratica_id = :p "
        "GROUP BY esito"), {"p": pratica_id})).all()
    return {str(r[0]): int(r[1]) for r in righe}


async def oggetti(session: AsyncSession, pratica_id: uuid.UUID, *, esito: str | None,
                  dopo_id: int, limite: int) -> list[dict[str, Any]]:
    filtro = " AND esito = :esito" if esito else ""
    righe = (await session.execute(text(
        # Revisione 3 di R05 (T1): l'inventario delle versioni (interno, anche
        # migliaia di righe per PdA) non entra nelle risposte dell'API
        "SELECT id, aip_id, pdv_id, urn, esito, motivo, dettagli - 'inventario' AS dettagli, "  # noqa: S608 — filtro costante
        "foglia_id, tentativi, "
        "aggiornato_il FROM public.pratiche_oggetti "  # noqa: S608 — filtro costante
        f"WHERE pratica_id = :p AND id > :dopo{filtro} ORDER BY id LIMIT :lim"),
        {"p": pratica_id, "dopo": dopo_id, "esito": esito, "lim": limite})).mappings().all()
    return [dict(r) for r in righe]


async def elenca(session: AsyncSession, *, tenant_id: uuid.UUID | None, tipo: str | None,
                 stato: str | None, dopo: tuple[datetime, uuid.UUID] | None,
                 limite: int, coppie: tuple[tuple[Any, ...], ...] | None = None
                 ) -> list[dict[str, Any]]:
    condizioni = ["TRUE"]
    parametri: dict[str, Any] = {"lim": limite}
    if coppie:
        # R06 (VD-C-128): una delle coppie (tipo, stato) «in attesa di» chi chiede
        alternative = []
        for i, coppia in enumerate(coppie):
            tipo_c, stato_c = coppia[0], coppia[1]
            autorizzazione = coppia[2] if len(coppia) > 2 else None
            parametri[f"as{i}"] = stato_c
            if tipo_c is None:
                alternativa = f"p.stato = :as{i}"
            else:
                parametri[f"at{i}"] = tipo_c
                alternativa = f"(p.tipo = :at{i} AND p.stato = :as{i})"
            if autorizzazione is not None:
                # R13b (VD-C-162): solo le pratiche con (o senza) l'autorizzazione richiesta
                parametri[f"aa{i}"] = bool(autorizzazione)
                alternativa = f"({alternativa} AND p.richiede_autorizzazione_produttore = :aa{i})"
            alternative.append(alternativa)
        condizioni.append("(" + " OR ".join(alternative) + ")")
    if tenant_id is not None:
        condizioni.append("p.tenant_id = :t")
        parametri["t"] = tenant_id
    if tipo:
        condizioni.append("p.tipo = :tipo")
        parametri["tipo"] = tipo
    if stato:
        condizioni.append("p.stato = :stato")
        parametri["stato"] = stato
    if dopo is not None:
        condizioni.append("(p.created_at, p.id) < (:quando, :ultimo)")
        parametri["quando"], parametri["ultimo"] = dopo
    righe = (await session.execute(text(
        f"SELECT {COLONNE_PRATICA} FROM public.pratiche p WHERE "  # noqa: S608 — costanti
        f"{' AND '.join(condizioni)} ORDER BY p.created_at DESC, p.id DESC LIMIT :lim"),
        parametri)).mappings().all()
    return [dict(r) for r in righe]


async def ente(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    riga = (await session.execute(text(
        "SELECT id, ragione_sociale AS denominazione, codice_ipa, natura, stato::text AS stato "
        "FROM public.tenants WHERE id = :t"), {"t": tenant_id})).mappings().first()
    return dict(riga) if riga else None


async def ruolo_utente(session: AsyncSession, utente_id: str | None,
                       tenant_id: uuid.UUID) -> str | None:
    """Ruolo ATTUALE di un utente attivo dell'ente (per i token di canale)."""
    if not utente_id:
        return None
    return (await session.execute(text(
        "SELECT ruolo::text FROM public.users WHERE id = CAST(:u AS uuid) AND tenant_id = :t "
        "AND attivo"), {"u": utente_id, "t": tenant_id})).scalar()


__all__ = [
    "COLONNE_PRATICA", "CursoreNonValido", "aggiorna_pratica", "allegati", "codifica_cursore",
    "conteggi_oggetti", "cronologia", "decodifica_cursore", "elenca", "ente",
    "inserisci_allegato", "inserisci_pratica", "inserisci_stato", "leggi_pratica", "oggetti",
    "pratica_per_idempotenza", "ruolo_utente",
]
