# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R16 (VD-C-166, VD-P-140) — credenziale di firma PERSONALE per persona nella console
(onboarding), su PostgreSQL vero: solo i ruoli fissi del conservatore con il permesso mai
delegabile, solo per persone del conservatore e fornitori CSC del registro, nessun segreto,
una sola attiva per persona (la precedente si disattiva, mai riscritta), versione letta,
audit con prima e dopo.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_g5_gestione_utenti import _chiama, _ente, _tok, _utente

pytestmark = [pytest.mark.integration, richiede_pg]
URL = "/api/v1/onboarding/firma/credenziali-personali"
CERT = "ab" * 32


async def _fornitore(url: str, codice: str = "csc-principale", tipo: str = "CSC") -> None:
    ruolo = "FIRMA" if tipo in ("CSC", "PKCS11") else "MARCA"
    await sql(url, "INSERT INTO public.fornitori_firma (codice, ruolo, tipo, nome, attivo, "
                   "configurazione) VALUES ($1, $2, $3, 'Fornitore di prova', true, '{}') "
                   "ON CONFLICT (codice) DO NOTHING", codice, ruolo, tipo)


async def test_registra_cambia_disattiva_con_audit(db_url) -> None:  # noqa: F811
    tid = await _ente(db_url)
    tok = _tok(tid, await _utente(db_url, tid, "SUPER_ADMIN"), "SUPER_ADMIN")
    persona = await _utente(db_url, tid, "RSC_CONSERVATORE")
    await _fornitore(db_url)
    corpo = {"fornitore_codice": "csc-principale", "credential_id": "responsabile-1",
             "certificato_sha256": CERT.upper(), "versione": 0}
    r = await _chiama("PUT", f"{URL}/{persona}", tok, corpo)
    assert r.status_code == 200, r.text
    prima = r.json()
    assert prima["attiva"] and prima["versione"] == 1 and prima["certificato_sha256"] == CERT
    # versione vecchia: rifiutata
    r = await _chiama("PUT", f"{URL}/{persona}", tok, {**corpo, "credential_id": "x"})
    assert r.status_code == 409
    r = await _chiama("PUT", f"{URL}/{persona}", tok,
                      {**corpo, "credential_id": "responsabile-2", "versione": 1})
    assert r.status_code == 200 and r.json()["versione"] == 2
    righe = await sql(db_url, "SELECT credential_id, attiva FROM public."
                              "credenziali_firma_personali WHERE user_id = $1 ORDER BY versione",
                      persona)
    assert [(x["credential_id"], x["attiva"]) for x in righe] == [
        ("responsabile-1", False), ("responsabile-2", True)]
    r = await _chiama("PUT", f"{URL}/{persona}", tok, {**corpo, "attiva": False, "versione": 2})
    assert r.status_code == 200 and not r.json()["attiva"]
    elenco = await _chiama("GET", URL, tok)
    assert elenco.status_code == 200 and len(elenco.json()["items"]) == 2
    assert str(persona) in {p["user_id"] for p in elenco.json()["persone"]}
    eventi = await sql(db_url, "SELECT dettagli::text AS d FROM public.audit_global WHERE "
                               "evento = 'firma.credenziale_personale_modificata'")
    assert len(eventi) == 3 and json.loads(eventi[-1]["d"])["persona"] == str(persona)


@pytest.mark.parametrize("caso", ["persona_ente", "fornitore_tsa", "fornitore_assente"])
async def test_solo_persone_del_conservatore_e_fornitori_csc(db_url, caso) -> None:  # noqa: F811
    tid = await _ente(db_url)
    tok = _tok(tid, await _utente(db_url, tid, "SUPER_ADMIN"), "SUPER_ADMIN")
    persona = await _utente(db_url, tid, "ADMIN_TENANT" if caso == "persona_ente"
                            else "RSC_CONSERVATORE")
    await _fornitore(db_url)
    await _fornitore(db_url, "tsa-principale", "RFC3161")
    codice = {"fornitore_tsa": "tsa-principale", "fornitore_assente": "nessuno"}.get(
        caso, "csc-principale")
    r = await _chiama("PUT", f"{URL}/{persona}", tok, {
        "fornitore_codice": codice, "credential_id": "c", "certificato_sha256": CERT})
    assert r.status_code == 422 and r.json()["codice"] == "FIRMA-422-CONFIGURAZIONE"


@pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "OPERATORE"])
async def test_solo_il_conservatore(db_url, ruolo) -> None:  # noqa: F811
    tid = await _ente(db_url)
    tok = _tok(tid, await _utente(db_url, tid, ruolo), ruolo)
    r = await _chiama("PUT", f"{URL}/{uuid.uuid4()}", tok, {
        "fornitore_codice": "csc-principale", "credential_id": "c", "certificato_sha256": CERT})
    assert r.status_code == 403


async def test_nessun_segreto_nel_corpo(db_url) -> None:  # noqa: F811
    tid = await _ente(db_url)
    tok = _tok(tid, await _utente(db_url, tid, "SUPER_ADMIN"), "SUPER_ADMIN")
    persona = await _utente(db_url, tid, "RSC_CONSERVATORE")
    await _fornitore(db_url)
    r = await _chiama("PUT", f"{URL}/{persona}", tok, {
        "fornitore_codice": "csc-principale", "credential_id": "c", "certificato_sha256": CERT,
        "pin": "1234"})
    assert r.status_code == 422  # campi in più rifiutati: PIN e OTP non passano di qui


async def test_una_sola_attiva_per_persona_nel_database(db_url) -> None:  # noqa: F811
    import asyncpg

    tid = await _ente(db_url)
    persona = await _utente(db_url, tid, "RSC_CONSERVATORE")
    inserisci = ("INSERT INTO public.credenziali_firma_personali (user_id, fornitore_codice, "
                 "credential_id, certificato_sha256) VALUES ($1, 'csc', 'c', $2)")
    await sql(db_url, inserisci, persona, CERT)
    with pytest.raises(asyncpg.UniqueViolationError):
        await sql(db_url, inserisci, persona, CERT)
    with pytest.raises(asyncpg.CheckViolationError):
        await sql(db_url, inserisci.replace("$2", "'non esadecimale'"), uuid.uuid4())
