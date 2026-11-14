# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (agente r05-sicurezza) — Row Level Security per ente (VD-C-080, VD-P-02).

Seconda difesa dietro i filtri applicativi. Provato con il login dei servizi
(membro di ``verumdoc_applicazione``, NON superutente, NON proprietario) su
PostgreSQL vero (migrazione 20261023_038):

  - un servizio che dimentica ``WHERE tenant_id = …`` vede solo il suo ente
    (anche attraverso la dipendenza d'autenticazione vera: token → ambito);
  - senza ambito nessuna riga (fail-closed); ambito conservatore = tutti gli enti;
  - scritture verso un altro ente rifiutate (WITH CHECK) o senza effetto;
  - ``tenant_id`` di documenti, metadati, eventi di ciclo di vita e fascicoli
    ricavato dal padre; fascicoli e PdA di enti diversi non si collegano;
  - l'ambito è LOCALE alla transazione (PgBouncer in modalità transaction):
    non resta sulla connessione riusata dal pool.
Prove di mutazione: rapporto r05/rapporti/sicurezza.md.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from libs.shared.db import get_async_session, get_engine
from libs.shared.db.ambito import (
    NESSUN_AMBITO,
    ambito_conservatore,
    ambito_ente,
    imposta_ambito,
    ripristina_ambito,
)
from libs.shared.models import Aip, Documento, Fascicolo, FascicoloAip, MetadatiAllegato5, Pdv
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = [pytest.mark.integration, richiede_pg]

#: Tabelle che devono avere la politica per ente (stesso elenco della migrazione, tranne
#: la tabella dei vecchi adattatori di compatibilità tolti in R18.2: resta nello schema
#: con la sua politica, ma nessun codice la usa più).
TABELLE_ENTE = (
    "pdv", "aip", "documenti", "metadati_allegato5", "lifecycle_events",
    "fascicoli", "fascicoli_aip", "versamento_impronte",
    "caricamenti", "caricamenti_parti", "richieste_idempotenti",
    "notifications", "webhook_sottoscrizioni", "webhook_consegne", "eventi_cliente",
    "sftp_account", "sftp_chiavi", "sftp_lavorazioni", "sftp_segnalazioni",
    "indicizzazione_aip", "pacchetti_distribuzione", "scarti",
    "verifiche_richieste", "verifiche_programmazione", "verifiche_copertura",
    "contatori_ente", "contatori_ente_delta",
)


@contextmanager
def senza_ambito() -> Iterator[None]:
    token = imposta_ambito(NESSUN_AMBITO)
    try:
        yield
    finally:
        ripristina_ambito(token)


async def _ente_con_dati(url: str) -> dict[str, uuid.UUID]:
    """Ente con PdV, PdA, documento, metadati, evento, fascicolo e collegamento
    (scritti dal superutente, che ignora la RLS)."""
    tenant, classe = await crea_tenant_e_classe(url)
    pdv, aip, doc, fasc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, "
                   "tenant_id) VALUES ($1, 'Prova RLS', 'CONSERVATO', $2, $3, $4)",
              pdv, classe, uuid.uuid4(), tenant)
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, "
                   "s3_bucket, hash_sha256) VALUES ($1, $2, $3, $4, 'CONSERVATO', 'k', 'b', $5)",
              aip, pdv, tenant, f"urn:prova:{aip}", "a" * 64)
    # tenant_id NON indicato: lo ricava il trigger dal padre
    await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                   "dimensione_bytes, hash_sha256, s3_key) VALUES ($1, $2, 'd.pdf', "
                   "'PRINCIPALE', 'application/pdf', 1, $3, 'k/d.pdf')",
              doc, aip, hashlib.sha256(b"x").hexdigest())
    await sql(url, "INSERT INTO public.metadati_allegato5 (documento_id, "
                   "identificativo_documento, modalita_formazione, tipologia_documentale, "
                   "data_documento, soggetto_produttore_id, soggetto_produttore_nome, oggetto, "
                   "formato) VALUES ($1, 'ID', 'a', 'FATTURA', CURRENT_DATE, 'P', 'Prod', "
                   "'Oggetto', 'PDF')", doc)
    await sql(url, "INSERT INTO public.lifecycle_events (aip_id, evento) VALUES ($1, 'prova')",
              aip)
    await sql(url, "INSERT INTO public.fascicoli (id, tenant_id, codice, titolo) "
                   "VALUES ($1, $2, $3, 'Fascicolo')", fasc, tenant, f"F-{fasc.hex[:8]}")
    await sql(url, "INSERT INTO public.fascicoli_aip (fascicolo_id, aip_id) VALUES ($1, $2)",
              fasc, aip)
    await sql(url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, "
                   "risorsa_id) VALUES ($1, 'versamento.accettato', 'pdv', $2)", tenant, str(pdv))
    return {"tenant": tenant, "classe": classe, "pdv": pdv, "aip": aip, "doc": doc,
            "fascicolo": fasc}


@pytest.fixture
async def due_enti(db_url):  # noqa: F811
    a = await _ente_con_dati(db_url)
    b = await _ente_con_dati(db_url)
    # evento globale (senza ente): leggibile da tutti
    await sql(db_url, "INSERT INTO public.eventi_cliente (tenant_id, tipo, risorsa_tipo, "
                      "risorsa_id) VALUES (NULL, 'catena.checkpoint', 'catena', 'globale')")
    return db_url, a, b


async def test_il_servizio_entra_con_un_ruolo_non_privilegiato(db_url):  # noqa: F811
    async with get_engine().connect() as conn:
        riga = (await conn.execute(text(
            "SELECT r.rolsuper, r.rolbypassrls, pg_has_role(current_user, "
            "'verumdoc_applicazione', 'MEMBER') AS app, "
            "pg_has_role(current_user, (SELECT relowner FROM pg_class "
            "WHERE oid = 'public.pdv'::regclass), 'USAGE') AS proprietario "
            "FROM pg_roles r WHERE r.rolname = current_user"))).one()
    assert (riga.rolsuper, riga.rolbypassrls, riga.app, riga.proprietario) == (
        False, False, True, False)


async def test_tutte_le_tabelle_dei_dati_hanno_la_politica(db_url):  # noqa: F811
    righe = await sql(db_url, """
        SELECT c.relname, c.relrowsecurity,
               EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid
                         AND p.polname = 'vd_ente_isolamento') AS politica
        FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace
          AND c.relname = ANY($1::text[])""", list(TABELLE_ENTE))
    stato = {r["relname"]: (r["relrowsecurity"], r["politica"]) for r in righe}
    assert set(stato) == set(TABELLE_ENTE)
    assert {t for t, v in stato.items() if v != (True, True)} == set()
    # le quattro tabelle senza ente proprio ora lo hanno, NOT NULL
    colonne = await sql(db_url, """
        SELECT table_name, is_nullable FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'tenant_id' AND table_name IN
          ('documenti', 'metadati_allegato5', 'lifecycle_events', 'fascicoli_aip')""")
    assert {(r["table_name"], r["is_nullable"]) for r in colonne} == {
        ("documenti", "NO"), ("metadati_allegato5", "NO"), ("lifecycle_events", "NO"),
        ("fascicoli_aip", "NO")}


async def test_query_senza_filtro_vede_solo_il_proprio_ente(due_enti):
    url, a, b = due_enti
    with ambito_ente(a["tenant"]):
        async with get_async_session() as s:
            # «servizio che dimentica il filtro applicativo»: nessun WHERE tenant_id
            assert set((await s.execute(select(Pdv.id))).scalars()) == {a["pdv"]}
            assert set((await s.execute(select(Aip.id))).scalars()) == {a["aip"]}
            assert set((await s.execute(select(Documento.id))).scalars()) == {a["doc"]}
            assert set((await s.execute(select(MetadatiAllegato5.documento_id))).scalars()) \
                == {a["doc"]}
            assert set((await s.execute(select(Fascicolo.id))).scalars()) == {a["fascicolo"]}
            assert set((await s.execute(select(FascicoloAip.aip_id))).scalars()) == {a["aip"]}
            assert (await s.execute(text(
                "SELECT count(*) FROM public.lifecycle_events"))).scalar_one() == 1
            # PdA dell'altro ente per chiave primaria: non esiste
            assert await s.get(Aip, b["aip"]) is None
            # eventi: i propri e quelli globali, mai quelli di B
            eventi = (await s.execute(text(
                "SELECT tenant_id FROM public.eventi_cliente"))).scalars().all()
            assert set(eventi) == {a["tenant"], None}


async def test_senza_ambito_nessuna_riga_e_conservatore_tutte(due_enti):
    url, a, b = due_enti
    with senza_ambito():
        async with get_async_session() as s:
            assert (await s.execute(select(func.count()).select_from(Pdv))).scalar_one() == 0
            assert (await s.execute(select(func.count()).select_from(Documento))).scalar_one() == 0
    with ambito_conservatore("test: il conservatore vede tutti gli enti"):
        async with get_async_session() as s:
            assert set((await s.execute(select(Pdv.id))).scalars()) == {a["pdv"], b["pdv"]}
            assert set((await s.execute(select(Documento.tenant_id))).scalars()) == {
                a["tenant"], b["tenant"]}


async def test_scritture_verso_un_altro_ente_rifiutate_o_senza_effetto(due_enti):
    url, a, b = due_enti
    with ambito_ente(a["tenant"]):
        # INSERT con l'ente di B: WITH CHECK
        with pytest.raises((ProgrammingError, DBAPIError), match="row-level security"):
            async with get_async_session() as s:
                await s.execute(text(
                    "INSERT INTO public.pdv (oggetto, classe_doc_id, user_id, tenant_id) "
                    "VALUES ('intruso', :c, :u, :t)"),
                    {"c": b["classe"], "u": uuid.uuid4(), "t": b["tenant"]})
        # UPDATE/DELETE di righe di B: nessuna riga toccata
        async with get_async_session() as s:
            aggiornate = await s.execute(text(
                "UPDATE public.pdv SET oggetto = 'manomesso' WHERE id = :id"), {"id": b["pdv"]})
            assert aggiornate.rowcount == 0
            # spostare una propria riga verso B: WITH CHECK
        with pytest.raises((ProgrammingError, DBAPIError), match="row-level security"):
            async with get_async_session() as s:
                await s.execute(text("UPDATE public.fascicoli SET tenant_id = :b WHERE id = :f"),
                                {"b": b["tenant"], "f": a["fascicolo"]})
    assert (await sql(url, "SELECT oggetto FROM public.pdv WHERE id = $1", b["pdv"]))[0][
        "oggetto"] == "Prova RLS"


async def test_ente_ricavato_dal_padre_e_collegamenti_misti_rifiutati(due_enti):
    url, a, b = due_enti
    with ambito_ente(a["tenant"]):
        async with get_async_session() as s:
            nuovo = Documento(aip_id=a["aip"], nome_file="n.pdf", tipo="ALLEGATO",
                              formato_mime="application/pdf", dimensione_bytes=1,
                              hash_sha256="b" * 64, s3_key="k/n.pdf")
            s.add(nuovo)
            await s.flush()
            assert (await s.execute(select(Documento.tenant_id).where(
                Documento.id == nuovo.id))).scalar_one() == a["tenant"]
        # documento agganciato al PdA di un altro ente (invisibile): rifiutato
        with pytest.raises(DBAPIError, match="inesistente o di un altro ente"):
            async with get_async_session() as s:
                await s.execute(text(
                    "INSERT INTO public.documenti (aip_id, nome_file, tipo, formato_mime, "
                    "dimensione_bytes, hash_sha256, s3_key) VALUES (:a, 'x', 'ALLEGATO', "
                    "'application/pdf', 1, :h, 'k')"), {"a": b["aip"], "h": "c" * 64})
    # anche il superutente (che ignora la RLS) non collega fascicolo di A e PdA di B
    import asyncpg

    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError,
                       match="non appartiene all'ente del fascicolo"):
        await sql(url, "INSERT INTO public.fascicoli_aip (fascicolo_id, aip_id) "
                       "VALUES ($1, $2)", a["fascicolo"], b["aip"])
    with pytest.raises(asyncpg.exceptions.CheckViolationError, match="tenant_id diverso"):
        await sql(url, "INSERT INTO public.lifecycle_events (aip_id, evento, tenant_id) "
                       "VALUES ($1, 'x', $2)", a["aip"], b["tenant"])


async def test_ambito_locale_alla_transazione_non_resta_sulla_connessione(due_enti):
    url, a, _b = due_enti
    engine = get_engine()
    with ambito_ente(a["tenant"]):
        async with engine.connect() as conn:
            assert (await conn.execute(text(
                "SELECT current_setting('app.ente', true)"))).scalar_one() == str(a["tenant"])
            await conn.commit()
            # stessa connessione, fuori da ogni transazione dell'applicazione:
            # SET LOCAL è finito con il COMMIT (con PgBouncer la connessione
            # passerebbe a un'altra richiesta)
            grezza = await conn.get_raw_connection()
            valore = await grezza.driver_connection.fetchval(
                "SELECT current_setting('app.ente', true)")
            assert valore in (None, "")


async def test_dipendenza_vera_imposta_l_ambito_dal_token(due_enti):
    """Un endpoint SENZA filtro applicativo, dietro l'autenticazione vera dei
    servizi (Bearer RS256 → verifica → ambito): vede solo l'ente del token."""
    from app.auth.jwt_handler import create_access_token, verify_access_token
    from fastapi import Depends, FastAPI
    from httpx import ASGITransport, AsyncClient

    from libs.shared.security.autenticazione_servizi import crea_dipendenza_utente

    url, a, b = due_enti
    utente = crea_dipendenza_utente(verifica_jwt=verify_access_token, pepper=lambda: None)
    app = FastAPI()

    @app.get("/pdv")
    async def elenco(_u: dict = Depends(utente)) -> list[str]:
        async with get_async_session() as s:
            return [str(i) for i in (await s.execute(select(Pdv.id))).scalars()]

    with senza_ambito():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            for ente, atteso in ((a, {str(a["pdv"])}), (b, {str(b["pdv"])})):
                tok = create_access_token(tenant_id=str(ente["tenant"]),
                                          user_id=str(uuid.uuid4()), ruolo="OPERATORE",
                                          scopes=["read"])
                r = await c.get("/pdv", headers={"Authorization": f"Bearer {tok}"})
                assert r.status_code == 200, r.text
                assert set(r.json()) == atteso
            tok = create_access_token(tenant_id=str(a["tenant"]), user_id=str(uuid.uuid4()),
                                      ruolo="RSC_CONSERVATORE", scopes=["*"])
            r = await c.get("/pdv", headers={"Authorization": f"Bearer {tok}"})
            assert set(r.json()) == {str(a["pdv"]), str(b["pdv"])}
            # chiave API dell'ente B: vede solo B
            from libs.shared.security.chiavi_api import genera_chiave_api

            pepper = b"pepper-di-prova-r05-abbastanza-lungo-0123456789"
            generata = genera_chiave_api(pepper)
            await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo) "
                           "VALUES ($1, $2, 'k@example.org', 'Sistema', 'Versante', 'API_SYSTEM')",
                      uid := uuid.uuid4(), b["tenant"])
            await sql(url, "INSERT INTO public.api_keys (id, tenant_id, user_id, nome, key_hash, "
                           "key_prefix, permessi) VALUES ($1, $2, $3, 'Protocollo', $4, $5, "
                           "'[\"read\"]'::jsonb)",
                      uuid.uuid4(), b["tenant"], uid, generata.impronta, generata.identificativo)
            con_chiave = FastAPI()
            utente_chiave = crea_dipendenza_utente(verifica_jwt=verify_access_token,
                                                   pepper=lambda: pepper)

            @con_chiave.get("/pdv")
            async def elenco_chiave(_u: dict = Depends(utente_chiave)) -> list[str]:
                async with get_async_session() as s:
                    return [str(i) for i in (await s.execute(select(Pdv.id))).scalars()]

        async with AsyncClient(transport=ASGITransport(app=con_chiave),
                               base_url="http://t") as c:
            r = await c.get("/pdv", headers={"X-Api-Key": generata.chiave})
            assert r.status_code == 200, r.text
            assert set(r.json()) == {str(b["pdv"])}
