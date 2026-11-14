# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R16 (VD-C-153) — revoca delle sessioni al prossimo uso del token quando cambiano i metodi
d'accesso ammessi o i permessi concedibili (conservatore o ente, anche ``LIMITE.`` e
modifiche programmate scritte alla decorrenza), su PostgreSQL vero.

  - il trigger di ``impostazioni_valori`` alza i contatori di ``versioni_accesso`` (anche su
    UPDATE e DELETE; mai per altri codici) e nessun login dei servizi li scrive;
  - il token porta ``vm`` (ogni sessione) e ``vc`` (ruoli personalizzati);
  - un cambio dei metodi dell'ente revoca le sessioni di QUELL'ente, del conservatore di
    tutti; un cambio dei concedibili revoca solo i ruoli personalizzati;
  - la revoca resta UNA query per controllo (nessun costo in più).
"""
from __future__ import annotations

import json
import uuid

import asyncpg
import pytest
from sqlalchemy import event

from libs.shared.security.revoca import svuota_cache_revoche, token_revocato
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg


async def _ente(url: str) -> uuid.UUID:
    ente = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Ente di prova', $3, 'ATTIVO', "
                   "'e@example.invalid')", ente, ente.hex[:16], f"t_{ente.hex[:20]}")
    return ente


async def _utente(url: str, ente: uuid.UUID, ruolo: str = "OPERATORE", *,
                  personalizzato: bool = False) -> str:
    uid = uuid.uuid4()
    rp = None
    if personalizzato:
        rp = uuid.uuid4()
        await sql(url, "INSERT INTO public.ruoli_personalizzati (id, tenant_id, nome, ruolo_base, "
                       "permessi) VALUES ($1, $2, $3, 'OPERATORE', '{}')", rp, ente,
                  f"Ruolo {rp.hex[:6]}")
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, "
                   "ruolo_personalizzato_id) VALUES ($1, $2, $3, 'Persona', 'Di prova', $4, $5)",
              uid, ente, f"{uid.hex[:8]}@example.invalid", ruolo, rp)
    return str(uid)


async def _payload(ente: uuid.UUID, uid: str, ruolo: str = "OPERATORE") -> dict:
    """Payload del token come lo emette il gateway (stessa funzione dell'accesso)."""
    from app.auth.emissione import permessi_utente

    permessi = await permessi_utente(uid, str(ente), ruolo)
    return {"user_id": uid, "tenant_id": str(ente), "ruolo": ruolo, "jti": uuid.uuid4().hex,
            **permessi.claim}


async def _imposta(url: str, codice: str, valore: list[str], ente: uuid.UUID | None = None
                   ) -> None:
    livello = "ENTE" if ente else "CONSERVATORE"
    await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
                   "motivo) VALUES ($1, $2, $3, CAST($4 AS jsonb), 'prova R16') ON CONFLICT ON "
                   "CONSTRAINT uq_impostazioni_valori_livello DO UPDATE SET valore = "
                   "EXCLUDED.valore", livello, ente, codice, json.dumps(valore))
    svuota_cache_revoche()


async def _revocato(payload: dict) -> bool:
    svuota_cache_revoche()
    return await token_revocato(payload)


async def test_cambio_dei_metodi_dell_ente_revoca_solo_quell_ente(db_url) -> None:  # noqa: F811
    a, b = await _ente(db_url), await _ente(db_url)
    pa = await _payload(a, await _utente(db_url, a))
    pb = await _payload(b, await _utente(db_url, b))
    assert "vm" in pa and "vc" not in pa
    assert not await _revocato(pa) and not await _revocato(pb)
    await _imposta(db_url, "ACCESSO_METODI_AMMESSI", ["PASSKEY"], a)
    assert await _revocato(pa)
    assert not await _revocato(pb)
    # nuovo accesso: nuova versione nel token, sessione valida
    assert not await _revocato(await _payload(a, pa["user_id"]))


async def test_cambio_dei_metodi_del_conservatore_revoca_tutti(db_url) -> None:  # noqa: F811
    a = await _ente(db_url)
    pa = await _payload(a, await _utente(db_url, a, "ADMIN_TENANT"), "ADMIN_TENANT")
    await _imposta(db_url, "ACCESSO_METODI_AMMESSI", ["SPID_CIE", "PASSKEY"])
    assert await _revocato(pa)


async def test_concedibili_revocano_solo_i_ruoli_personalizzati(db_url) -> None:  # noqa: F811
    a = await _ente(db_url)
    fisso = await _payload(a, await _utente(db_url, a))
    personalizzato = await _payload(a, await _utente(db_url, a, personalizzato=True))
    assert personalizzato.get("rp") and "vc" in personalizzato
    await _imposta(db_url, "RUOLI_PERMESSI_CONCEDIBILI", ["pratica:consulta"], a)
    assert await _revocato(personalizzato)
    assert not await _revocato(fisso)
    # anche il limite del conservatore per l'ente (LIMITE.<codice>)
    nuovo = await _payload(a, personalizzato["user_id"])
    assert not await _revocato(nuovo)
    await _imposta(db_url, "LIMITE.RUOLI_PERMESSI_CONCEDIBILI", ["pratica:consulta"], a)
    assert await _revocato(nuovo)


async def test_update_e_delete_alzano_la_versione_altri_codici_no(db_url) -> None:  # noqa: F811
    a = await _ente(db_url)
    await _imposta(db_url, "ACCESSO_METODI_AMMESSI", ["PASSKEY"], a)
    p = await _payload(a, await _utente(db_url, a))
    await sql(db_url, "UPDATE public.impostazioni_valori SET motivo = 'solo il motivo' "
                      "WHERE codice = 'STAGING_SCARTATI_GIORNI'")
    await _imposta(db_url, "STAGING_SCARTATI_GIORNI", [], a)  # altro codice: nessun effetto
    assert not await _revocato(p)
    await sql(db_url, "DELETE FROM public.impostazioni_valori WHERE tenant_id = $1 AND "
                      "codice = 'ACCESSO_METODI_AMMESSI'", a)
    assert await _revocato(p)
    righe = await sql(db_url, "SELECT ambito, tipo, versione FROM public.versioni_accesso "
                              "WHERE ambito = $1", str(a))
    assert [(r["tipo"], r["versione"]) for r in righe] == [("METODI", 2)]


async def test_token_senza_versione_e_versione_non_numerica(db_url) -> None:  # noqa: F811
    a = await _ente(db_url)
    uid = await _utente(db_url, a, personalizzato=True)
    p = await _payload(a, uid)
    # sessioni emesse prima del rilascio (senza vm): nessun confronto
    assert not await _revocato({k: v for k, v in p.items() if k not in ("vm", "vc")})
    assert await _revocato({**p, "vm": "x"})
    # ruolo personalizzato con vm ma senza vc: fail-closed
    assert await _revocato({k: v for k, v in p.items() if k != "vc"})


async def test_una_sola_query_per_controllo(db_url) -> None:  # noqa: F811
    from libs.shared.db import get_engine

    a = await _ente(db_url)
    p = await _payload(a, await _utente(db_url, a, personalizzato=True))
    istruzioni: list[str] = []
    motore = get_engine().sync_engine

    def conta(_conn, _cursor, sql_, *_a) -> None:  # type: ignore[no-untyped-def]
        istruzioni.append(sql_)

    event.listen(motore, "before_cursor_execute", conta)
    try:
        await _revocato(p)
    finally:
        event.remove(motore, "before_cursor_execute", conta)
    lette = [i for i in istruzioni if "versioni_accesso" in i or "public.users" in i]
    assert len(lette) == 1 and "versioni_accesso" in lette[0]


async def test_contatori_scritti_solo_dal_trigger(db_url) -> None:  # noqa: F811
    from libs.shared.db.ruolo_prova import url_ruolo_servizio

    url = await url_ruolo_servizio(db_url)
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        assert await conn.fetchval("SELECT count(*) FROM public.versioni_accesso") >= 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE public.versioni_accesso SET versione = 0")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("SELECT public.vd_alza_versione_accesso('ACCESSO_METODI_AMMESSI',"
                               " NULL)")
    finally:
        await conn.close()
