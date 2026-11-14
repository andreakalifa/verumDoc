# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R15 (VD-C-177) — documentazione del servizio generata dalla configurazione, su PostgreSQL
vero (login non proprietario dei servizi, RLS) con la migrazione ``20261111_058``.

Permessi (azione mai delegabile ``documentazione:genera``):
  - solo SUPER_ADMIN e RSC_CONSERVATORE con ruolo fisso; ADMIN_TENANT, OPERATORE, VIEWER,
    un ruolo personalizzato che dichiara il permesso e una chiave API: 403, nessuna richiesta;
  - il vincolo del database rifiuta un ruolo personalizzato con il permesso.
Generazione asincrona:
  - 202, richiesta IN_CORSO con audit; il lavoro la chiude COMPLETATA con le quattro
    versioni (versione 1), audit, impronte riverificate dal database;
  - stessa configurazione: nessuna versione nuova; una configurazione diversa (fornitore di
    firma attivo) crea la versione 2 del manuale e del piano con le sezioni cambiate;
  - una sola richiesta in corso alla volta; una richiesta scaduta si dichiara interrotta e
    si riparte; un errore chiude la richiesta in ERRORE (metrica);
  - scaricamento Markdown e HTML con l'impronta, audit dello scaricamento.
Niente valori inventati: con il database di prova ogni dato assente (fornitori, canali PEC,
dati del conservatore) è «DA COMPILARE» con chi lo fornisce; le classi del database
compaiono.
Immutabilità (trigger anche su INSERT): versione non successiva, impronta diversa dal
contenuto, contenuto uguale al precedente, generazione non in corso, archivio WORM già
valorizzato all'inserimento, UPDATE, DELETE e TRUNCATE rifiutati; archivio registrabile una
sola volta, solo dal gruppo della hashchain e con l'impronta nella chiave; il login di
dashboard-api non può modificare né cancellare.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg
BASE = "/api/dashboard/documentazione-servizio"
TIPI = {"MANUALE_CONSERVAZIONE", "PIANO_SICUREZZA", "GUIDA_FUNZIONALITA", "CATALOGO_ROUTE"}


def _token(ruolo: str, **altro) -> dict[str, str]:  # type: ignore[no-untyped-def]
    from app.auth.jwt_handler import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        tenant_id="00000000-0000-0000-0000-0000000000c0", user_id=str(uuid.uuid4()),
        ruolo=ruolo, **altro)}


def _client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _genera(c: AsyncClient, ruolo: str = "RSC_CONSERVATORE") -> dict:  # type: ignore[type-arg]
    r = await c.post(f"{BASE}/generazioni", headers=_token(ruolo))
    assert r.status_code == 202, r.text
    # con ASGITransport il lavoro in BackgroundTasks finisce prima della risposta
    g = await c.get(f"{BASE}/generazioni/{r.json()['id']}", headers=_token(ruolo))
    return g.json()


def _contatore(esito: str) -> float:
    from app.routers.documentazione_servizio import GENERAZIONI

    return GENERAZIONI.labels(esito)._value.get()  # noqa: SLF001


# ── permessi ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("intestazioni", [
    _token("ADMIN_TENANT"), _token("OPERATORE"), _token("VIEWER"),
    _token("OPERATORE", rp=str(uuid.uuid4()), permessi=["documentazione:genera"], pv=1),
    _token("RSC_CONSERVATORE", auth_method="api_key"),
], ids=["admin-ente", "operatore", "consultazione", "ruolo-personalizzato", "metodo-chiave"])
async def test_solo_il_conservatore_con_ruolo_fisso(db_url, intestazioni) -> None:  # noqa: F811
    async with _client() as c:
        for metodo, percorso in (("post", "/generazioni"), ("get", ""),
                                 ("get", "/versioni?tipo=MANUALE_CONSERVAZIONE")):
            r = await getattr(c, metodo)(f"{BASE}{percorso}", headers=intestazioni)
            assert r.status_code in (401, 403), (percorso, r.status_code, r.text)
    assert await sql(db_url, "SELECT id FROM public.documentazione_generazioni") == []


def test_dipendenza_rifiuta_chiave_api_e_ruolo_personalizzato() -> None:
    import asyncio

    from app.routers.documentazione_servizio import conservatore

    from libs.shared.errori import ErroreApi

    for utente in ({"ruolo": "RSC_CONSERVATORE", "auth_method": "api_key"},
                   {"ruolo": "OPERATORE", "rp": "x", "permessi": ["documentazione:genera"]},
                   {"ruolo": "SUPER_ADMIN", "rp": "x", "permessi": ["documentazione:genera"]}):
        with pytest.raises(ErroreApi):
            asyncio.run(conservatore(utente))
    assert asyncio.run(conservatore({"ruolo": "SUPER_ADMIN"}))["ruolo"] == "SUPER_ADMIN"


async def test_vincolo_del_database_sul_permesso(db_url) -> None:  # noqa: F811
    tenant, _ = await crea_tenant_e_classe(db_url)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(db_url, "INSERT INTO public.ruoli_personalizzati (tenant_id, nome, ruolo_base, "
                          "permessi) VALUES ($1, 'abuso', 'OPERATORE', "
                          "ARRAY['archivio:consulta', 'documentazione:genera'])", tenant)


# ── generazione ────────────────────────────────────────────────────────

async def test_generazione_versioni_idempotenza_e_differenze(db_url) -> None:  # noqa: F811
    prima_completate = _contatore("completata")
    async with _client() as c:
        g = await _genera(c)
        assert g["stato"] == "COMPLETATA", g
        assert {n["tipo"] for n in g["esito"]["nuove"]} == TIPI
        assert all(n["versione"] == 1 for n in g["esito"]["nuove"])
        quadro = (await c.get(BASE, headers=_token("SUPER_ADMIN"))).json()
        manuale = next(d["ultima"] for d in quadro["documenti"]
                       if d["tipo"] == "MANUALE_CONSERVAZIONE")
        campi = {x["campo"] for s in manuale["da_compilare"] for x in s["campi"]}
        # niente valori inventati: ciò che il database di prova non ha è da compilare
        assert {"db.fornitori_firma", "db.fornitori_marca", "db.canali_pec",
                "db.fornitori_identita", "conservatore.denominazione",
                "continuita.rpo"} <= campi
        assert "db.classi_documentali" not in campi           # classi presenti nel database
        assert all(x["chi"] for s in manuale["da_compilare"] for x in s["campi"])
        assert manuale["da_verificare"] > 0 and manuale["archivio"]["archiviata"] is False
        md = await c.get(f"{BASE}/versioni/{manuale['id']}/markdown",
                         headers=_token("RSC_CONSERVATORE"))
        assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
        assert hashlib.sha256(md.content).hexdigest() == md.headers["x-impronta-sha256"] \
            == manuale["impronta_sha256"]
        assert "manuale_conservazione_v1.md" in md.headers["content-disposition"]
        assert "Classi documentali attive del servizio (database)" in md.text
        html = await c.get(f"{BASE}/versioni/{manuale['id']}/html",
                           headers=_token("RSC_CONSERVATORE"))
        assert hashlib.sha256(html.content).hexdigest() == manuale["impronta_html"]
        assert "default-src 'none'" in html.headers["content-security-policy"]

        # stessa configurazione: nessuna versione nuova
        g2 = await _genera(c)
        assert g2["stato"] == "COMPLETATA" and g2["esito"]["nuove"] == []
        assert set(g2["esito"]["invariate"]) == TIPI

        # configurazione cambiata: fornitore di firma attivo
        await sql(db_url, "INSERT INTO public.fornitori_firma (codice, ruolo, tipo, nome, "
                          "attivo, priorita, configurazione) VALUES ('csc-prova', 'FIRMA', "
                          "'CSC', 'Prestatore di prova', true, 1, '{}'::jsonb)")
        g3 = await _genera(c)
        nuove = {n["tipo"]: n["versione"] for n in g3["esito"]["nuove"]}
        assert nuove == {"MANUALE_CONSERVAZIONE": 2, "PIANO_SICUREZZA": 2}
        storico = (await c.get(f"{BASE}/versioni?tipo=MANUALE_CONSERVAZIONE",
                               headers=_token("RSC_CONSERVATORE"))).json()["items"]
        assert [v["versione"] for v in storico] == [2, 1]
        assert {s["id"] for s in storico[0]["differenze"]["cambiate"]} == {"man.d", "man.all7"}
        dettaglio = (await c.get(f"{BASE}/versioni/{storico[0]['id']}",
                                 headers=_token("RSC_CONSERVATORE"))).json()
        assert dettaglio["precedente_id"] == storico[1]["id"]
        assert dettaglio["fonti"]["db:fornitori_firma"] == "letta in sola lettura"
    assert _contatore("completata") - prima_completate == 3
    eventi = [r["evento"] for r in await sql(
        db_url, "SELECT evento FROM public.audit_global WHERE evento LIKE 'documentazione.%' "
                "ORDER BY created_at")]
    assert eventi.count("documentazione.generazione_richiesta") == 3
    assert eventi.count("documentazione.generazione_completata") == 3
    assert eventi.count("documentazione.versione_scaricata") == 2
    righe = await sql(db_url, "SELECT markdown, impronta_sha256 FROM "
                              "public.documentazione_versioni")
    assert len(righe) == 6
    assert all(hashlib.sha256(bytes(r["markdown"])).hexdigest() == r["impronta_sha256"].strip()
               for r in righe)


async def test_una_richiesta_alla_volta_e_ripresa_dopo_scadenza(db_url) -> None:  # noqa: F811
    in_corso = (await sql(db_url, "INSERT INTO public.documentazione_generazioni (scade_il) "
                                  "VALUES (now() + interval '5 minutes') RETURNING id"))[0]["id"]
    prima_interrotte = _contatore("interrotta")
    async with _client() as c:
        r = await c.post(f"{BASE}/generazioni", headers=_token("RSC_CONSERVATORE"))
        assert r.status_code == 202 and r.json()["id"] == str(in_corso)
        assert r.json()["nuova"] is False
        # scaduta (processo terminato): si dichiara interrotta e si riparte
        await sql(db_url, "ALTER TABLE public.documentazione_generazioni DISABLE TRIGGER "
                          "documentazione_generazioni_protette")
        await sql(db_url, "UPDATE public.documentazione_generazioni SET scade_il = now() - "
                          "interval '1 minute' WHERE id = $1", in_corso)
        await sql(db_url, "ALTER TABLE public.documentazione_generazioni ENABLE TRIGGER "
                          "documentazione_generazioni_protette")
        g = await _genera(c)
        assert g["id"] != str(in_corso) and g["stato"] == "COMPLETATA"
    vecchia = (await sql(db_url, "SELECT stato, errore FROM public.documentazione_generazioni "
                                 "WHERE id = $1", in_corso))[0]
    assert vecchia["stato"] == "ERRORE" and vecchia["errore"].startswith("interrotta")
    assert _contatore("interrotta") - prima_interrotte == 1


async def test_errore_chiude_la_richiesta_in_errore(db_url, monkeypatch) -> None:  # noqa: F811
    from libs.conformita import fotografia

    def guasta():  # type: ignore[no-untyped-def]
        raise OSError("fotografia del repository illeggibile")

    monkeypatch.setattr(fotografia, "leggi_fotografia", guasta)
    prima = _contatore("errore")
    async with _client() as c:
        g = await _genera(c)
    assert g["stato"] == "ERRORE" and "OSError" in g["errore"]
    assert _contatore("errore") - prima == 1
    assert await sql(db_url, "SELECT id FROM public.documentazione_versioni") == []
    assert await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = "
                             "'documentazione.generazione_fallita'")


async def test_scaricamento_versione_inesistente(db_url) -> None:  # noqa: F811
    async with _client() as c:
        for p in (f"/versioni/{uuid.uuid4()}/markdown", "/versioni/non-un-id",
                  f"/generazioni/{uuid.uuid4()}"):
            assert (await c.get(f"{BASE}{p}", headers=_token("SUPER_ADMIN"))).status_code == 404


# ── immutabilità nel database ──────────────────────────────────────────

async def _generazione(url: str) -> uuid.UUID:
    return (await sql(url, "INSERT INTO public.documentazione_generazioni (scade_il) VALUES "
                           "(now() + interval '5 minutes') RETURNING id"))[0]["id"]


def _riga(gen: uuid.UUID, versione: int = 1, precedente: uuid.UUID | None = None,
          testo: str = "# Manuale\n", **sovrascrivi) -> dict:  # type: ignore[no-untyped-def,type-arg]
    md, html = testo.encode(), f"<p>{testo}</p>".encode()
    r = {"id": uuid.uuid4(), "tipo": "MANUALE_CONSERVAZIONE", "versione": versione,
         "precedente_id": precedente, "generazione_id": gen, "titolo": "Manuale",
         "nome_file": "manuale_conservazione", "markdown": md, "html": html,
         "impronta_contenuto": hashlib.sha256(testo.encode() + b"c").hexdigest(),
         "impronta_sha256": hashlib.sha256(md).hexdigest(),
         "impronta_html": hashlib.sha256(html).hexdigest()}
    r.update(sovrascrivi)
    return r


async def _inserisci(url: str, r: dict, extra: str = "", valori_extra: tuple = ()) -> None:  # type: ignore[type-arg]
    colonne = list(r)
    segnaposti = ", ".join(f"${i}" for i in range(1, len(colonne) + 1))
    await sql(url, f"INSERT INTO public.documentazione_versioni ({', '.join(colonne)}, "  # noqa: S608
                   f"sezioni, differenze, fonti, da_compilare, da_verificare, generata_il{extra}) "
                   f"VALUES ({segnaposti}, '[]', '{{}}', '{{}}', '[]', '[]', now()"
                   + "".join(f", ${len(colonne) + i}" for i in range(1, len(valori_extra) + 1))
                   + ")", *r.values(), *valori_extra)


async def test_trigger_d_inserimento(db_url) -> None:  # noqa: F811
    gen = await _generazione(db_url)
    err = (asyncpg.IntegrityConstraintViolationError, asyncpg.RaiseError)
    with pytest.raises(err):                                   # non parte da 1
        await _inserisci(db_url, _riga(gen, versione=2))
    with pytest.raises(err):                                   # impronta diversa dal contenuto
        await _inserisci(db_url, _riga(gen, impronta_sha256="0" * 64))
    with pytest.raises(err):                                   # HTML con impronta diversa
        await _inserisci(db_url, _riga(gen, impronta_html="0" * 64))
    with pytest.raises(err):                                   # archivio WORM già pieno
        r = _riga(gen)
        await _inserisci(db_url, r, ", worm_chiave_md, worm_chiave_html, archiviata_il",
                         (f"x/{r['impronta_sha256']}.md", f"x/{r['impronta_html']}.html",
                          datetime.now(UTC)))
    prima = _riga(gen)
    await _inserisci(db_url, prima)
    with pytest.raises(err):                                   # precedente sbagliato
        await _inserisci(db_url, _riga(gen, versione=2, testo="# Altro\n"))
    with pytest.raises(err):                                   # contenuto uguale al precedente
        await _inserisci(db_url, _riga(gen, versione=2, precedente=prima["id"],
                                       impronta_contenuto=prima["impronta_contenuto"]))
    await sql(db_url, "UPDATE public.documentazione_generazioni SET stato = 'COMPLETATA' "
                      "WHERE id = $1", gen)
    with pytest.raises(err):                                   # generazione non più in corso
        await _inserisci(db_url, _riga(gen, versione=2, precedente=prima["id"],
                                       testo="# Altro\n"))
    gen2 = await _generazione(db_url)
    await _inserisci(db_url, _riga(gen2, versione=2, precedente=prima["id"], testo="# Altro\n"))
    assert [r["versione"] for r in await sql(
        db_url, "SELECT versione FROM public.documentazione_versioni ORDER BY versione")] == [1, 2]


async def test_versioni_immutabili_e_archivio_una_volta(db_url) -> None:  # noqa: F811
    from libs.shared.db.ruolo_prova import (
        GRUPPO_DOCUMENTAZIONE,
        assicura_ruolo_servizio,
        url_con_utente,
    )

    gen = await _generazione(db_url)
    r = _riga(gen)
    await _inserisci(db_url, r)
    err = (asyncpg.RaiseError, asyncpg.IntegrityConstraintViolationError)
    for istruzione in ("UPDATE public.documentazione_versioni SET markdown = 'x'",
                       "UPDATE public.documentazione_versioni SET titolo = 'Altro'",
                       "UPDATE public.documentazione_versioni SET da_compilare = '[]'::jsonb "
                       "|| '[1]'::jsonb",
                       "DELETE FROM public.documentazione_versioni",
                       "TRUNCATE public.documentazione_versioni CASCADE"):
        with pytest.raises(err):
            await sql(db_url, istruzione)
    md_ok = f"registro/documentazione/manuale_conservazione/{r['impronta_sha256']}.md"
    html_ok = f"registro/documentazione/manuale_conservazione/{r['impronta_html']}.html"
    archivia = ("UPDATE public.documentazione_versioni SET worm_chiave_md = $1, "
                "worm_chiave_html = $2, archiviata_il = now() WHERE id = $3")
    # login di dashboard-api (solo verumdoc_documentazione): né UPDATE né DELETE
    ruolo = "vd_prova_r15_documentazione"
    await assicura_ruolo_servizio(db_url, ruolo, (GRUPPO_DOCUMENTAZIONE,), solo_gruppi=True)
    servizio = url_con_utente(db_url, ruolo)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await sql(servizio, archivia, md_ok, html_ok, r["id"])
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await sql(servizio, "DELETE FROM public.documentazione_versioni")
    assert len(await sql(servizio, "SELECT id FROM public.documentazione_versioni")) == 1
    # chiave senza l'impronta: rifiutata anche alla hashchain
    with pytest.raises(err):
        await sql(db_url, archivia, "registro/documentazione/manuale.md", html_ok, r["id"])
    with pytest.raises(err):                                    # archivio e contenuto insieme
        await sql(db_url, "UPDATE public.documentazione_versioni SET worm_chiave_md = $1, "
                          "worm_chiave_html = $2, archiviata_il = now(), titolo = 'Altro' "
                          "WHERE id = $3", md_ok, html_ok, r["id"])
    await sql(db_url, archivia, md_ok, html_ok, r["id"])        # superutente = ogni gruppo
    with pytest.raises(err):                                    # una sola volta
        await sql(db_url, archivia, md_ok + "x", html_ok, r["id"])
    riga = (await sql(db_url, "SELECT worm_chiave_md FROM public.documentazione_versioni"))[0]
    assert riga["worm_chiave_md"] == md_ok


async def test_generazioni_protette(db_url) -> None:  # noqa: F811
    err = (asyncpg.RaiseError, asyncpg.IntegrityConstraintViolationError,
           asyncpg.UniqueViolationError)
    with pytest.raises(err):                                    # nasce solo IN_CORSO e vuota
        await sql(db_url, "INSERT INTO public.documentazione_generazioni (stato, scade_il, "
                          "conclusa_il) VALUES ('COMPLETATA', now() + interval '1 hour', now())")
    with pytest.raises(err):                                    # scadenza nel passato
        await sql(db_url, "INSERT INTO public.documentazione_generazioni (scade_il) "
                          "VALUES (now() - interval '1 minute')")
    gen = await _generazione(db_url)
    with pytest.raises(err):                                    # una sola IN_CORSO
        await _generazione(db_url)
    await sql(db_url, "UPDATE public.documentazione_generazioni SET stato = 'COMPLETATA', "
                      "esito = $1::jsonb WHERE id = $2", json.dumps({"nuove": []}), gen)
    for istruzione in ("UPDATE public.documentazione_generazioni SET stato = 'ERRORE'",
                       "DELETE FROM public.documentazione_generazioni"):
        with pytest.raises(err):
            await sql(db_url, istruzione)
