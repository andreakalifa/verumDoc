# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R17 (VD-P-164) — conferma firmata dal gateway delle decisioni sensibili, su PostgreSQL vero
(login non proprietario dei servizi, RLS), S3 moto con Object Lock e registro finto.

  - API: annullamento, approvazione di scarto, esecuzione, ripresa senza conferma, con una
    conferma di un altro contenuto, scaduta o già usata → 403 LC-403-CONFERMA, nessuna
    scrittura; con la conferma giusta la decisione passa e la conferma si registra;
  - LAVORATORE: un'API compromessa che toglie o scambia il collegamento, registra una
    conferma scaduta, di un'altra versione, di una chiave revocata o con un nonce già
    consumato da un'altra conferma, o rimette in esecuzione una pratica sospesa senza la
    conferma della ripresa: pratica BLOCCATA (LC-409-AUTORIZZAZIONI con i difetti
    CONFERMA_*), nessun effetto;
  - transizione: le azioni decise prima della migrazione 059 su pratiche allora aperte non
    si pretendono;
  - database: colonne uguali al payload firmato, istante e versione fissati dal trigger,
    append-only, nonce unico, diritti minimi (l'API non scrive né legge i fatti del
    lavoratore, non sceglie istante né versione).
"""
from __future__ import annotations

import base64
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from libs.shared import conferme_decisione as cd
from libs.shared.db.ruolo_prova import url_ruolo_servizio
from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    PASSATO,
    bucket_worm,
    client_app,
    crea_classe,
    crea_ente,
    crea_pda,
    crea_pdv,
    crea_utente,
    dipendenze,
    intestazioni,
    lavora,
    oggetti_pda,
)

pytestmark = [pytest.mark.integration, richiede_pg]

PRIVATA = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
KID = "gw-prova-1"
CORPO_SCARTO = {"criteri": {"classi": ["contratti"], "anni": [2015]},
                "motivo": "Scarto dei contratti scaduti del 2015"}


@pytest.fixture(autouse=True)
def conferme_attive(monkeypatch: pytest.MonkeyPatch):
    from app.application import conferme
    from app.config import settings

    monkeypatch.setattr(settings, "conferma_decisioni", "ATTIVO")
    monkeypatch.setattr(settings, "conferme_chiavi_pubbliche",
                        json.dumps({KID: cd.chiave_pubblica_b64(PRIVATA)}))
    monkeypatch.setattr(settings, "conferme_kid_revocati", "")
    conferme._chiavi.cache_clear()
    yield
    conferme._chiavi.cache_clear()


def _utente(intestazione: dict[str, str]) -> str:
    """user_id del token di prova (parte centrale del JWT)."""
    corpo = intestazione["Authorization"].split()[1].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))["user_id"]


def _conferma(azione: str, *, pratica: str | None, versione: int, campi: dict[str, Any],
              utente: str, ente: Any, adesso: datetime | None = None, durata: int = 300,
              kid: str = KID, privata: Ed25519PrivateKey = PRIVATA) -> dict[str, str]:
    return cd.firma(cd.Richiesta(azione, pratica, versione, cd.impronta_contenuto(azione, campi),
                                 str(ente)),
                    utente=utente, kid=kid, privata=privata,
                    adesso=adesso or datetime.now(UTC), durata_secondi=durata,
                    nonce=secrets.token_hex(16))


async def _pratica(db_url: str, pid: Any) -> dict[str, Any]:
    riga = dict((await sql(db_url, "SELECT * FROM public.pratiche WHERE id = $1::uuid",
                           str(pid)))[0])
    for campo in ("dati", "ultimo_errore"):
        if isinstance(riga[campo], str):
            riga[campo] = json.loads(riga[campo])
    return riga


async def _bloccata(db_url: str, pid: Any) -> list[str]:
    p = await _pratica(db_url, pid)
    assert p["lavoro"] is None, p
    assert p["ultimo_errore"]["codice"] == "LC-409-AUTORIZZAZIONI", p["ultimo_errore"]
    return list(p["ultimo_errore"]["difetti"])


async def _come_api(db_url: str, istruzione: str, *argomenti: Any) -> list[Any]:
    """Il login dell'API di ciclo-vita (non proprietario) che fa da sé."""
    dsn = (await url_ruolo_servizio(db_url)).replace("postgresql+asyncpg://", "postgresql://",
                                                     1)
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.ambito', 'conservatore', true)")
            return await conn.fetch(istruzione, *argomenti)
    finally:
        await conn.close()


async def _registra_come_api(db_url: str, pratica: dict[str, Any], azione: str,
                             conferma: dict[str, str]) -> str:
    crudi, firma, campi = cd.leggi(conferma)
    righe = await _come_api(
        db_url, "INSERT INTO public.conferme_decisione (pratica_id, tenant_id, azione, kid, "
                "nonce, utente_id, payload, firma) VALUES ($1, $2, $3, $4, $5, $6::uuid, $7, $8) "
                "RETURNING id", pratica["id"], pratica["tenant_id"], azione, campi["kid"],
        campi["nonce"], campi["utente"], crudi, firma)
    return str(righe[0]["id"])


async def _collega(db_url: str, pid: Any, conferme: dict[str, str]) -> None:
    await _come_api(db_url, "UPDATE public.pratiche SET dati = dati || jsonb_build_object("
                            "'conferme', $2::jsonb) WHERE id = $1::uuid",
                    str(pid), json.dumps(conferme))


# ── Annullamento ─────────────────────────────────────────────────────────

async def _ente(db_url: str, s3_ciclo: str) -> dict[str, Any]:
    tenant = await crea_ente(db_url)
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(db_url, tenant)
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    bucket_worm(s3_ciclo)
    return {"tenant": tenant, "admin": intestazioni(tenant, "ADMIN_TENANT", admin),
            "admin_id": str(admin), "pdv": str(pdv), "urn": urn, "classe": classe}


def _corpo_annullamento(e: dict[str, Any], motivo: str = "Documento versato per errore",
                        **sostituzioni: Any) -> dict[str, Any]:
    campi = {"pdv_id": e["pdv"], "motivo": motivo}
    conferma = _conferma(cd.ANNULLAMENTO_RICHIEDI, pratica=None, versione=0,
                         campi=sostituzioni.pop("campi", campi),
                         utente=sostituzioni.pop("utente", e["admin_id"]),
                         ente=e["tenant"], **sostituzioni)
    return {"pdv_id": e["pdv"], "motivo": motivo, "conferma": conferma}


async def test_annullamento_con_conferma_eseguito_e_nonce_consumato(db_url, s3_ciclo) -> None:
    e = await _ente(db_url, s3_ciclo)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                         json=_corpo_annullamento(e))
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        stato = (await c.get("/api/v1/ciclo-vita/conferme", headers=e["admin"])).json()
        assert stato["attiva"] is True and "ANNULLAMENTO_RICHIEDI" in stato["azioni_creazione"]
    p = await _pratica(db_url, pid)
    registrata = await sql(db_url, "SELECT azione, versione_pratica, utente_id::text AS u "
                                   "FROM public.conferme_decisione WHERE pratica_id = $1::uuid",
                           pid)
    assert [(r["azione"], r["versione_pratica"], r["u"]) for r in registrata] == [
        ("ANNULLAMENTO_RICHIEDI", 0, e["admin_id"])]
    assert set(p["dati"]["conferme"]) == {"ANNULLAMENTO_RICHIEDI"}
    await lavora(dipendenze(db_url))
    assert (await _pratica(db_url, pid))["stato"] == "ESEGUITA"
    fatti = await sql(db_url, "SELECT evento, azione FROM public.conferme_lavoratore "
                              "WHERE pratica_id = $1::uuid AND evento <> 'GIRO_CHIUSO'", pid)
    assert [(f["evento"], f["azione"]) for f in fatti] == [("VERIFICATA",
                                                             "ANNULLAMENTO_RICHIEDI")]


@pytest.mark.parametrize("caso", ["senza", "altro_motivo", "altro_utente", "scaduta",
                                  "altra_chiave", "chiave_revocata"])
async def test_annullamento_senza_conferma_valida_rifiutato(db_url, s3_ciclo, monkeypatch,
                                                           caso) -> None:
    from app.application import conferme
    from app.config import settings

    e = await _ente(db_url, s3_ciclo)
    corpo = _corpo_annullamento(e)
    if caso == "senza":
        corpo.pop("conferma")
    elif caso == "altro_motivo":
        corpo = {**_corpo_annullamento(e), "motivo": "Motivo diverso da quello confermato"}
    elif caso == "altro_utente":
        corpo = _corpo_annullamento(e, utente=str(uuid.uuid4()))
    elif caso == "scaduta":
        corpo = _corpo_annullamento(e, adesso=datetime.now(UTC) - timedelta(seconds=400))
    elif caso == "altra_chiave":
        corpo = _corpo_annullamento(e, privata=Ed25519PrivateKey.generate())
    else:
        monkeypatch.setattr(settings, "conferme_kid_revocati", KID)
        conferme._chiavi.cache_clear()
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                         json=corpo)
    assert r.status_code == 403, r.text
    assert r.json()["codice"] == "LC-403-CONFERMA"
    assert await sql(db_url, "SELECT 1 FROM public.pratiche WHERE pdv_id = $1::uuid",
                     e["pdv"]) == []
    assert await sql(db_url, "SELECT 1 FROM public.conferme_decisione") == []


async def test_conferma_gia_usata_rifiutata(db_url, s3_ciclo) -> None:
    """La stessa conferma non si registra due volte (nonce unico), nemmeno alla pratica
    giusta: la seconda richiesta (es. dopo una RESPINTA) ne vuole una nuova."""
    e = await _ente(db_url, s3_ciclo)
    corpo = _corpo_annullamento(e)
    async with client_app() as c:
        assert (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                             json=corpo)).status_code == 201
    # come dopo una RESPINTA: la pratica è finita, ne serve una nuova con la stessa conferma
    await sql(db_url, "UPDATE public.pratiche SET stato = 'RESPINTA', lavoro = NULL, "
                      "conclusa_il = now() WHERE pdv_id = $1::uuid", e["pdv"])
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                         json=corpo)
    assert r.status_code == 403 and r.json()["codice"] == "LC-403-CONFERMA", r.text


@pytest.mark.parametrize("manomissione", ["scollegata", "di_un_altra_pratica",
                                          "motivo_cambiato", "chiave_revocata",
                                          "chiave_ruotata_senza_la_vecchia"])
async def test_api_compromessa_dopo_la_richiesta_bloccata_dal_lavoratore(
        db_url, s3_ciclo, monkeypatch, manomissione) -> None:
    from app.application import conferme
    from app.config import settings

    e = await _ente(db_url, s3_ciclo)
    altra = await _ente(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                            json=_corpo_annullamento(e))).json()["id"]
        # una seconda pratica vera (di un altro ente) da cui prendere una conferma
        pid2 = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                             headers=altra["admin"],
                             json=_corpo_annullamento(altra))).json()["id"]
    await sql(db_url, "UPDATE public.pratiche SET lavoro = NULL WHERE id = $1::uuid", pid2)
    if manomissione == "scollegata":
        await _collega(db_url, pid, {})
    elif manomissione == "di_un_altra_pratica":
        await _collega(db_url, pid, (await _pratica(db_url, pid2))["dati"]["conferme"])
    elif manomissione == "motivo_cambiato":
        await _come_api(db_url, "UPDATE public.pratiche SET motivo = 'Annullare tutto' "
                                "WHERE id = $1::uuid", pid)
    elif manomissione == "chiave_revocata":
        monkeypatch.setattr(settings, "conferme_kid_revocati", KID)
        conferme._chiavi.cache_clear()
    else:
        monkeypatch.setattr(settings, "conferme_chiavi_pubbliche", json.dumps(
            {"gw-prova-2": cd.chiave_pubblica_b64(Ed25519PrivateKey.generate())}))
        conferme._chiavi.cache_clear()
    esiti = await lavora(dipendenze(db_url))
    assert "BLOCCATO" in esiti, esiti
    difetti = " ".join(await _bloccata(db_url, pid))
    atteso = {"scollegata": "CONFERMA_ASSENTE", "di_un_altra_pratica": "CONFERMA_ASSENTE",
              "motivo_cambiato": "CONFERMA_CONTENUTO_DIVERSO",
              "chiave_revocata": "CONFERMA_CHIAVE_REVOCATA",
              "chiave_ruotata_senza_la_vecchia": "CONFERMA_CHIAVE_IGNOTA"}[manomissione]
    assert atteso in difetti, difetti
    assert (await _pratica(db_url, pid))["stato"] == "RICHIESTA"
    assert await sql(db_url, "SELECT 1 FROM public.esiti_esecuzione WHERE pratica_id = "
                             "$1::uuid AND esito = 'ATTO_ARCHIVIATO'", pid) == []


@pytest.mark.parametrize("difetto", ["scaduta", "versione", "nonce_consumato"])
async def test_conferma_registrata_dall_api_compromessa_verificata_dal_lavoratore(
        db_url, s3_ciclo, difetto) -> None:
    """L'API compromessa registra da sé (con il suo login) una conferma vera ma scaduta, di
    un'altra versione, o il cui nonce il lavoratore ha già consumato per un'altra conferma."""
    e = await _ente(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                            json=_corpo_annullamento(e))).json()["id"]
    p = await _pratica(db_url, pid)
    campi = {"pdv_id": e["pdv"], "motivo": p["motivo"]}
    if difetto == "scaduta":
        conferma = _conferma(cd.ANNULLAMENTO_RICHIEDI, pratica=None, versione=0, campi=campi,
                             utente=e["admin_id"], ente=e["tenant"],
                             adesso=datetime.now(UTC) - timedelta(seconds=400))
    elif difetto == "versione":
        conferma = _conferma(cd.ANNULLAMENTO_RICHIEDI, pratica=None, versione=1, campi=campi,
                             utente=e["admin_id"], ente=e["tenant"])
    else:
        conferma = _conferma(cd.ANNULLAMENTO_RICHIEDI, pratica=None, versione=0, campi=campi,
                             utente=e["admin_id"], ente=e["tenant"])
    nuova = await _registra_come_api(db_url, p, cd.ANNULLAMENTO_RICHIEDI, conferma)
    await _collega(db_url, pid, {"ANNULLAMENTO_RICHIEDI": nuova})
    if difetto == "nonce_consumato":
        # il lavoratore ha già consumato questo nonce per la conferma originale
        originale = p["dati"]["conferme"]["ANNULLAMENTO_RICHIEDI"]
        await sql(db_url, "INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, "
                          "evento, conferma_id, nonce, azione) VALUES ($1::uuid, $2, "
                          "'VERIFICATA', $3::uuid, $4, 'ANNULLAMENTO_RICHIEDI')",
                  pid, e["tenant"], originale, cd.leggi(conferma)[2]["nonce"])
    await lavora(dipendenze(db_url))
    difetti = " ".join(await _bloccata(db_url, pid))
    atteso = {"scaduta": "CONFERMA_SCADUTA", "versione": "CONFERMA_VERSIONE_DIVERSA",
              "nonce_consumato": "CONFERMA_RIUSATA"}[difetto]
    assert atteso in difetti, difetti


# ── Scarto: approvazione, esecuzione, sospensione e ripresa ─────────────

async def _scarto(db_url: str, s3_ciclo: str) -> dict[str, Any]:
    from tests.firme_prova import autorizzazione

    tenant = await crea_ente(db_url, natura="PRIVATO")
    admin = await crea_utente(db_url, tenant, "ADMIN_TENANT")
    ente_c = await crea_ente(db_url)
    rsc = await crea_utente(db_url, ente_c, "RSC_CONSERVATORE")
    classe = await crea_classe(db_url, tenant)
    s3 = bucket_worm(s3_ciclo)
    pdv = await crea_pdv(db_url, tenant, classe, admin)
    aip, _ = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    oggetti_pda(s3, tenant, aip, fino_a=PASSATO)
    sc = {"tenant": tenant, "admin": intestazioni(tenant, "ADMIN_TENANT", admin),
          "rsc": intestazioni(ente_c, "RSC_CONSERVATORE", rsc), "rsc_id": str(rsc)}
    async with client_app() as c:
        corpo = {"ente": str(tenant), **CORPO_SCARTO}
        piano = (await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta",
                              headers=sc["admin"], json=corpo)).json()
        r = await c.post("/api/v1/ciclo-vita/pratiche/scarto/proposta", headers=sc["admin"],
                         json={**corpo, "impronta_piano": piano["impronta_piano"]})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert (await _tr(c, pid, sc["admin"], "BOZZA", "PROPOSTA")).status_code == 200
        aut = (await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/allegati",
                            headers=sc["admin"], data={"tipo": "AUTORIZZAZIONE_PRODUTTORE"},
                            files={"file": ("a.p7m", autorizzazione(pid),
                                            "application/pkcs7-mime")})).json()["id"]
        assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                          allegati=[aut])).status_code == 200
    sc["pid"] = pid
    return sc


async def _tr(c, pid, chi, da, a, *, allegati=(), conferma=None):  # type: ignore[no-untyped-def]
    corpo = {"stato_atteso": da, "nuovo_stato": a, "allegati": list(allegati),
             "motivo": "Passaggio di stato della prova"}
    if conferma is not None:
        corpo["conferma"] = conferma
    return await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/transizione", headers=chi,
                        json=corpo)


async def _conferma_scarto(db_url: str, sc: dict[str, Any], azione: str) -> dict[str, str]:
    p = await _pratica(db_url, sc["pid"])
    return _conferma(azione, pratica=str(p["id"]), versione=int(p["versione"]),
                     campi={"impronta_piano": p["impronta_piano"]}, utente=sc["rsc_id"],
                     ente=sc["tenant"])


async def test_scarto_approvato_ed_eseguito_con_le_conferme(db_url, s3_ciclo) -> None:
    sc = await _scarto(db_url, s3_ciclo)
    pid = sc["pid"]
    async with client_app() as c:
        r = await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE")
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-CONFERMA"
        # la conferma dell'esecuzione non vale per l'approvazione
        sbagliata = await _conferma_scarto(db_url, sc, cd.SCARTO_ESEGUI)
        r = await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE",
                      conferma=sbagliata)
        assert r.status_code == 403
        r = await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE",
                      conferma=await _conferma_scarto(db_url, sc, cd.SCARTO_APPROVA))
        assert r.status_code == 200, r.text
        r = await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")
        assert r.status_code == 403
        r = await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA",
                      conferma=await _conferma_scarto(db_url, sc, cd.SCARTO_ESEGUI))
        assert r.status_code == 202, r.text
    await lavora(dipendenze(db_url))
    assert (await _pratica(db_url, pid))["stato"] == "ESEGUITA"
    fatti = await sql(db_url, "SELECT azione FROM public.conferme_lavoratore WHERE pratica_id "
                              "= $1::uuid AND evento = 'VERIFICATA' ORDER BY id", pid)
    assert [f["azione"] for f in fatti] == ["SCARTO_APPROVA", "SCARTO_ESEGUI"]


async def test_scarto_messo_in_esecuzione_dall_api_compromessa_bloccato(db_url,
                                                                       s3_ciclo) -> None:
    """La prova di VD-C-120: l'API compromessa porta la pratica a ESEGUIBILE e chiede
    l'esecuzione con il suo login, senza conferme: il lavoratore non cancella nulla."""
    sc = await _scarto(db_url, s3_ciclo)
    pid = sc["pid"]
    await _come_api(db_url, "UPDATE public.pratiche SET stato = 'ESEGUIBILE', "
                            "lavoro = 'ESEGUI', prossimo_tentativo = now() WHERE id = $1::uuid",
                    pid)
    await _come_api(db_url, "INSERT INTO public.pratiche_stati (pratica_id, tenant_id, "
                            "da_stato, a_stato, utente_id, ruolo, metodo, motivo, allegati) "
                            "VALUES ($1::uuid, $2, 'AUTORIZZATA_PRODUTTORE', 'ESEGUIBILE', "
                            "$3::uuid, 'RSC_CONSERVATORE', 'jwt', 'fabbricata', '[]'::jsonb)",
                    pid, sc["tenant"], sc["rsc_id"])
    esiti = await lavora(dipendenze(db_url))
    assert "BLOCCATO" in esiti, esiti
    difetti = " ".join(await _bloccata(db_url, pid))
    assert "SCARTO_APPROVA: CONFERMA_ASSENTE" in difetti or \
        "CONFERMA_ASSENTE: nessuna conferma firmata per SCARTO_APPROVA" in difetti, difetti
    assert "SCARTO_ESEGUI" in difetti
    assert await sql(db_url, "SELECT 1 FROM public.esiti_esecuzione WHERE pratica_id = "
                             "$1::uuid AND esito = 'ELIMINATO'", pid) == []


async def test_ripresa_dopo_la_sospensione_richiede_la_conferma(db_url, s3_ciclo,
                                                                monkeypatch) -> None:
    from app.config import settings
    from app.infrastructure.registro import ErroreRegistro

    monkeypatch.setattr(settings, "lavoratore_tentativi_massimi", 2)
    sc = await _scarto(db_url, s3_ciclo)
    pid = sc["pid"]
    dip = dipendenze(db_url)
    dip.registro.errore = ErroreRegistro("registro irraggiungibile (prova)", ritentabile=True)
    async with client_app() as c:
        assert (await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE",
                          conferma=await _conferma_scarto(db_url, sc, cd.SCARTO_APPROVA))
                ).status_code == 200
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA",
                          conferma=await _conferma_scarto(db_url, sc, cd.SCARTO_ESEGUI))
                ).status_code == 202
        for _ in range(3):
            await lavora(dip, giri=1)
            await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() - "
                              "interval '1 second' WHERE id = $1::uuid", pid)
        p = await _pratica(db_url, pid)
        assert p["lavoro"] is None and p["ultimo_errore"]["codice"] == \
            "LC-409-TENTATIVI-ESAURITI"
        assert [r["evento"] for r in await sql(
            db_url, "SELECT evento FROM public.conferme_lavoratore WHERE pratica_id = $1::uuid "
                    "AND evento = 'SOSPESA'", pid)] == ["SOSPESA"]
        url = f"/api/v1/ciclo-vita/pratiche/{pid}/riprendi"
        motivo = "Registro delle impronte di nuovo raggiungibile dopo l'intervento"
        r = await c.post(url, headers=sc["rsc"], json={"versione": p["versione"],
                                                      "motivo": motivo})
        assert r.status_code == 403 and r.json()["codice"] == "LC-403-CONFERMA"
    # l'API compromessa rimette il lavoro senza la ripresa confermata
    await _come_api(db_url, "UPDATE public.pratiche SET lavoro = 'ESEGUI', tentativi = 0, "
                            "ultimo_errore = NULL, prossimo_tentativo = now() "
                            "WHERE id = $1::uuid", pid)
    dip.registro.errore = None
    await lavora(dip, giri=2)
    difetti = " ".join(await _bloccata(db_url, pid))
    assert "PRATICA_RIPRENDI" in difetti, difetti
    # il conservatore la riprende con la conferma: il lavoratore la porta a termine
    await sql(db_url, "UPDATE public.pratiche SET ultimo_errore = jsonb_build_object("
                      "'codice', 'LC-409-TENTATIVI-ESAURITI', 'lavoro_sospeso', 'ESEGUI', "
                      "'tentativi', 2) WHERE id = $1::uuid", pid)
    p = await _pratica(db_url, pid)
    conferma = _conferma(cd.PRATICA_RIPRENDI, pratica=pid, versione=int(p["versione"]),
                         campi={"lavoro": "ESEGUI"}, utente=sc["rsc_id"], ente=sc["tenant"])
    async with client_app() as c:
        r = await c.post(f"/api/v1/ciclo-vita/pratiche/{pid}/riprendi", headers=sc["rsc"],
                         json={"versione": p["versione"], "conferma": conferma,
                               "motivo": "Registro delle impronte di nuovo raggiungibile"})
        assert r.status_code == 200, r.text
    for _ in range(3):
        await lavora(dip, giri=2)
        await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() - "
                          "interval '1 second' WHERE id = $1::uuid", pid)
    assert (await _pratica(db_url, pid))["stato"] == "ESEGUITA"


async def test_azioni_decise_prima_della_migrazione_non_si_pretendono(db_url, s3_ciclo,
                                                                    monkeypatch) -> None:
    from app.config import settings

    e = await _ente(db_url, s3_ciclo)
    monkeypatch.setattr(settings, "conferma_decisioni", "DISATTIVO")
    async with client_app() as c:
        pid = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                            json={"pdv_id": e["pdv"], "motivo": "Richiesta prima di R17"})
               ).json()["id"]
    monkeypatch.setattr(settings, "conferma_decisioni", "ATTIVO")
    # senza la riga della transizione: bloccata
    await lavora(dipendenze(db_url))
    assert "CONFERMA_ASSENTE" in " ".join(await _bloccata(db_url, pid))
    # con la riga che la migrazione scrive per le pratiche allora aperte: eseguita
    await sql(db_url, "UPDATE public.pratiche SET lavoro = 'ANNULLA', ultimo_errore = NULL, "
                      "prossimo_tentativo = now() WHERE id = $1::uuid", pid)
    await sql(db_url, "INSERT INTO public.conferme_transizione_r17 (pratica_id, azioni) "
                      "VALUES ($1::uuid, ARRAY['ANNULLAMENTO_RICHIEDI'])", pid)
    await lavora(dipendenze(db_url))
    assert (await _pratica(db_url, pid))["stato"] == "ESEGUITA"


# ── Database ─────────────────────────────────────────────────────────────

async def test_database_colonne_dal_payload_istante_e_versione_dal_trigger(db_url,
                                                                         s3_ciclo) -> None:
    e = await _ente(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                            json=_corpo_annullamento(e))).json()["id"]
    p = await _pratica(db_url, pid)
    campi = {"pdv_id": e["pdv"], "motivo": p["motivo"]}
    buona = _conferma(cd.ANNULLAMENTO_APPROVA, pratica=pid, versione=1, campi=campi,
                      utente=e["admin_id"], ente=e["tenant"])
    crudi, firma, f = cd.leggi(buona)
    # colonne diverse dal payload firmato
    for azione, utente in (("SCARTO_APPROVA", f["utente"]), (f["azione"], str(uuid.uuid4()))):
        with pytest.raises(asyncpg.PostgresError, match="diverse dal payload"):
            await _come_api(db_url, "INSERT INTO public.conferme_decisione (pratica_id, "
                                    "tenant_id, azione, kid, nonce, utente_id, payload, firma) "
                                    "VALUES ($1, $2, $3, $4, $5, $6::uuid, $7, $8)",
                            p["id"], p["tenant_id"], azione, f["kid"], f["nonce"], utente,
                            crudi, firma)
    # l'API non sceglie istante né versione (diritti di colonna)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _come_api(db_url, "INSERT INTO public.conferme_decisione (pratica_id, tenant_id, "
                                "azione, kid, nonce, utente_id, payload, firma, registrata_il, "
                                "versione_pratica) VALUES ($1, $2, $3, $4, $5, $6::uuid, $7, $8, "
                                "now() - interval '1 day', 1)",
                        p["id"], p["tenant_id"], f["azione"], f["kid"], f["nonce"],
                        f["utente"], crudi, firma)
    nuova = await _registra_come_api(db_url, p, cd.ANNULLAMENTO_APPROVA, buona)
    riga = (await sql(db_url, "SELECT versione_pratica, registrata_il FROM "
                              "public.conferme_decisione WHERE id = $1::uuid", nuova))[0]
    assert riga["versione_pratica"] == p["versione"]
    assert abs((riga["registrata_il"] - datetime.now(UTC)).total_seconds()) < 60
    # nonce unico, append-only
    with pytest.raises(asyncpg.UniqueViolationError):
        await _registra_come_api(db_url, p, cd.ANNULLAMENTO_APPROVA, buona)
    for istruzione in ("UPDATE public.conferme_decisione SET versione_pratica = 9",
                       "DELETE FROM public.conferme_decisione"):
        with pytest.raises(asyncpg.PostgresError):
            await sql(db_url, istruzione)
    # i fatti del lavoratore: l'API non li legge né li scrive
    for istruzione in ("SELECT 1 FROM public.conferme_lavoratore",
                       "SELECT 1 FROM public.conferme_transizione_r17"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _come_api(db_url, istruzione)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _come_api(db_url, "INSERT INTO public.conferme_lavoratore (pratica_id, "
                                "tenant_id, evento) VALUES ($1, $2, 'SOSPESA')",
                        p["id"], p["tenant_id"])


async def test_chiave_revocata_dopo_il_consumo_non_ferma_il_lavoro_gia_autorizzato(
        db_url, s3_ciclo, monkeypatch) -> None:
    """Una conferma già verificata e consumata dal lavoratore per QUESTO lavoro resta valida
    se la chiave viene revocata dopo (giro successivo della stessa esecuzione); il contenuto
    si ricontrolla comunque."""
    from app.application import conferme
    from app.config import settings

    from libs.shared.db import get_async_session

    e = await _ente(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                            json=_corpo_annullamento(e))).json()["id"]
    from app.infrastructure.ambito import ambito_lavoratore

    pratica = await _pratica(db_url, pid)
    with ambito_lavoratore("prova R17"):
        await conferme.assicura(pratica, datetime.now(UTC))   # verifica e consumo
        monkeypatch.setattr(settings, "conferme_kid_revocati", KID)
        conferme._chiavi.cache_clear()
        async with get_async_session() as session:
            assert (await conferme.difetti_conferme(session, pratica))[0] == []
            # il contenuto cambiato si vede anche per una conferma già consumata
            cambiata = {**pratica, "motivo": "Annullare tutto"}
            difetti, _ = await conferme.difetti_conferme(session, cambiata)
    assert any("CONFERMA_CONTENUTO_DIVERSO" in d for d in difetti), difetti


async def test_chiave_api_non_conferma_e_api_non_registra_senza_persona(db_url,
                                                                         s3_ciclo) -> None:
    """Con la conferma attiva una chiave API (o un'identità senza persona) non registra una
    decisione sensibile, anche portando una conferma vera."""
    from app.application import conferme
    from app.domain.permessi import Identita

    from libs.shared.db import get_async_session
    from libs.shared.errori import ErroreApi

    e = await _ente(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                            json=_corpo_annullamento(e))).json()["id"]
    p = await _pratica(db_url, pid)
    campi = {"pdv_id": e["pdv"], "motivo": p["motivo"]}
    vera = _conferma(cd.ANNULLAMENTO_APPROVA, pratica=pid, versione=int(p["versione"]),
                     campi=campi, utente=e["admin_id"], ente=e["tenant"])
    for identita in (Identita(str(e["tenant"]), None, "API_SYSTEM", "api_key", "k1"),
                     Identita(str(e["tenant"]), e["admin_id"], "ADMIN_TENANT", "api_key", "k1")):
        async with get_async_session() as session:
            with pytest.raises(ErroreApi) as errore:
                await conferme.registra(session, identita, azione=cd.ANNULLAMENTO_APPROVA,
                                        pratica=p, conferma=vera, campi=campi,
                                        versione_attesa=int(p["versione"]),
                                        adesso=datetime.now(UTC))
        assert errore.value.codice == "LC-403-CONFERMA"


async def test_ripresa_confermata_prima_di_una_nuova_sospensione_non_vale(db_url,
                                                                         s3_ciclo) -> None:
    """La conferma di una ripresa precedente (già verificata e consumata) non vale per la
    ripresa dopo una NUOVA sospensione: deve essere registrata dopo la sospensione."""
    from app.application import conferme
    from app.infrastructure.ambito import ambito_lavoratore

    from libs.shared.db import get_async_session

    e = await _ente(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                            json=_corpo_annullamento(e))).json()["id"]
    p = await _pratica(db_url, pid)
    ripresa = _conferma(cd.PRATICA_RIPRENDI, pratica=pid, versione=int(p["versione"]),
                        campi={"lavoro": "ANNULLA"}, utente=e["admin_id"], ente=e["tenant"])
    rid = await _registra_come_api(db_url, p, cd.PRATICA_RIPRENDI, ripresa)
    await _come_api(db_url, "UPDATE public.pratiche SET dati = dati || jsonb_build_object("
                            "'ripresa', jsonb_build_object('lavoro', 'ANNULLA', 'da', $2::text), "
                            "'conferme', (dati -> 'conferme') || jsonb_build_object("
                            "'PRATICA_RIPRENDI', $3::text)) WHERE id = $1::uuid",
                    pid, e["admin_id"], rid)
    nonce = cd.leggi(ripresa)[2]["nonce"]
    for istruzione, argomenti in (
            ("INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento) VALUES "
             "($1::uuid, $2, 'SOSPESA')", (pid, e["tenant"])),
            ("INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento, conferma_id, "
             "nonce, azione) VALUES ($1::uuid, $2, 'VERIFICATA', $3::uuid, $4, "
             "'PRATICA_RIPRENDI')", (pid, e["tenant"], rid, nonce))):
        await sql(db_url, istruzione, *argomenti)
    p = await _pratica(db_url, pid)
    with ambito_lavoratore("prova R17"):
        async with get_async_session() as session:
            # ripresa verificata dopo la sospensione: nessuna ripresa pendente
            assert (await conferme.difetti_conferme(session, p))[0] == []
    await sql(db_url, "INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento) "
                      "VALUES ($1::uuid, $2, 'SOSPESA')", pid, e["tenant"])
    with ambito_lavoratore("prova R17"):
        async with get_async_session() as session:
            difetti, _ = await conferme.difetti_conferme(session, p)
    assert any("CONFERMA_RIPRESA_PRECEDENTE" in d for d in difetti), difetti


async def test_trigger_fissa_istante_e_versione_anche_per_chi_ha_i_diritti(db_url,
                                                                          s3_ciclo) -> None:
    """Seconda guardia, indipendente dai diritti di colonna: anche il proprietario che
    indica istante e versione li vede sostituiti dal trigger."""
    e = await _ente(db_url, s3_ciclo)
    async with client_app() as c:
        pid = (await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=e["admin"],
                            json=_corpo_annullamento(e))).json()["id"]
    p = await _pratica(db_url, pid)
    conferma = _conferma(cd.ANNULLAMENTO_APPROVA, pratica=pid, versione=int(p["versione"]),
                         campi={"pdv_id": e["pdv"], "motivo": p["motivo"]},
                         utente=e["admin_id"], ente=e["tenant"])
    crudi, firma, f = cd.leggi(conferma)
    riga = (await sql(db_url, "INSERT INTO public.conferme_decisione (pratica_id, tenant_id, "
                              "azione, kid, nonce, utente_id, payload, firma, registrata_il, "
                              "versione_pratica) VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, $7, "
                              "$8, now() - interval '1 day', 99) "
                              "RETURNING registrata_il, versione_pratica",
                      pid, e["tenant"], f["azione"], f["kid"], f["nonce"], f["utente"], crudi,
                      firma))[0]
    assert riga["versione_pratica"] == p["versione"]
    assert abs((riga["registrata_il"] - datetime.now(UTC)).total_seconds()) < 60


# ── Revisione di sicurezza R17: la conferma vale per UN giro di esecuzione ─────────

async def _scarto_eseguito(db_url: str, s3_ciclo: str) -> dict[str, Any]:
    sc = await _scarto(db_url, s3_ciclo)
    async with client_app() as c:
        assert (await _tr(c, sc["pid"], sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE",
                          conferma=await _conferma_scarto(db_url, sc, cd.SCARTO_APPROVA))
                ).status_code == 200
        assert (await _tr(c, sc["pid"], sc["rsc"], "ESEGUIBILE", "ESEGUITA",
                          conferma=await _conferma_scarto(db_url, sc, cd.SCARTO_ESEGUI))
                ).status_code == 202
    await lavora(dipendenze(db_url))
    assert (await _pratica(db_url, sc["pid"]))["stato"] == "ESEGUITA"
    eventi = [r["evento"] for r in await sql(
        db_url, "SELECT evento FROM public.conferme_lavoratore WHERE pratica_id = $1::uuid "
                "ORDER BY id", sc["pid"])]
    assert eventi[-1] == "GIRO_CHIUSO", eventi
    return sc


async def _nuovo_giro_dall_api(db_url: str, pid: str, richiesta_da: str | None
                               ) -> dict[str, Any]:
    """L'API compromessa chiede un nuovo giro senza conferma nuova (dati.conferme invariato,
    giro precedente in esecuzioni_precedenti)."""
    p = await _pratica(db_url, pid)
    dati = dict(p["dati"])
    dati["esecuzioni_precedenti"] = [dict(dati["esecuzione"])]
    dati["esecuzione"] = {"richiesta_da": richiesta_da, "giro": 2}
    await _come_api(db_url, "UPDATE public.pratiche SET stato = 'ESEGUITA_CON_ECCEZIONI', "
                            "lavoro = 'ESEGUI', dati = $2::jsonb WHERE id = $1::uuid",
                    pid, json.dumps(dati))
    return await _pratica(db_url, pid)


async def _difetti(p: dict[str, Any]) -> list[str]:
    from app.application import conferme
    from app.infrastructure.ambito import ambito_lavoratore

    from libs.shared.db import get_async_session

    with ambito_lavoratore("prova revisione R17"):
        async with get_async_session() as session:
            return (await conferme.difetti_conferme(session, p))[0]


@pytest.mark.parametrize("richiedente", ["sistema_finto", "chi_aveva_firmato"])
async def test_conferma_consumata_non_vale_per_un_nuovo_giro(db_url, s3_ciclo,
                                                             richiedente) -> None:
    """Riproduzione della revisione: prima il nuovo giro passava con la conferma del giro
    concluso (anche fingendo una ripresa del SISTEMA con richiesta_da nullo)."""
    sc = await _scarto_eseguito(db_url, s3_ciclo)
    p = await _nuovo_giro_dall_api(db_url, sc["pid"],
                                   None if richiedente == "sistema_finto" else sc["rsc_id"])
    difetti = " ".join(await _difetti(p))
    assert "CONFERMA_GIRO_PRECEDENTE" in difetti, difetti
    # il lavoratore non esegue nulla: pratica bloccata
    await lavora(dipendenze(db_url))
    assert "SCARTO_ESEGUI" in " ".join(await _bloccata(db_url, sc["pid"]))


async def test_ripresa_automatica_del_sistema_riusa_la_conferma_del_giro(db_url,
                                                                        s3_ciclo) -> None:
    """La ripresa automatica (lavoro periodico) si riconosce dal fatto RIPRESA_SISTEMA del
    solo lavoratore: con quello la conferma del giro precedente vale; una conferma nuova del
    conservatore vale sempre."""
    sc = await _scarto_eseguito(db_url, s3_ciclo)
    p = await _nuovo_giro_dall_api(db_url, sc["pid"], None)
    await sql(db_url, "INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento) "
                      "VALUES ($1::uuid, $2, 'RIPRESA_SISTEMA')", sc["pid"], sc["tenant"])
    assert await _difetti(p) == []
    # un nuovo giro chiesto da una persona con una conferma NUOVA: valido
    await sql(db_url, "INSERT INTO public.conferme_lavoratore (pratica_id, tenant_id, evento) "
                      "VALUES ($1::uuid, $2, 'GIRO_CHIUSO')", sc["pid"], sc["tenant"])
    nuova = _conferma(cd.SCARTO_ESEGUI, pratica=sc["pid"], versione=int(p["versione"]),
                      campi={"impronta_piano": p["impronta_piano"]}, utente=sc["rsc_id"],
                      ente=sc["tenant"])
    cid = await _registra_come_api(db_url, p, cd.SCARTO_ESEGUI, nuova)
    await _collega(db_url, sc["pid"], {**p["dati"]["conferme"], "SCARTO_ESEGUI": cid})
    p = await _pratica(db_url, sc["pid"])
    p["dati"]["esecuzione"] = {"richiesta_da": sc["rsc_id"], "giro": 3}
    assert await _difetti(p) == []


async def test_ripresa_del_lavoro_periodico_scrive_il_fatto_e_riusa_la_conferma(
        db_url, s3_ciclo) -> None:
    """Il lavoro periodico (D20) che riprende uno scarto incompleto scrive RIPRESA_SISTEMA:
    il giro successivo riusa la conferma consumata senza bloccarsi."""
    from app.application import periodico
    from app.infrastructure.ambito import ambito_lavoratore

    sc = await _scarto_eseguito(db_url, s3_ciclo)
    # come dopo un giro con eccezioni (un PdA rimasto sotto blocco, ora libero)
    await sql(db_url, "UPDATE public.pratiche SET stato = 'ESEGUITA_CON_ECCEZIONI', "
                      "lavoro = NULL, conclusa_il = NULL WHERE id = $1::uuid", sc["pid"])
    await sql(db_url, "UPDATE public.pratiche_oggetti SET esito = 'NON_COMPLETATO' "
                      "WHERE pratica_id = $1::uuid", sc["pid"])
    await sql(db_url, "UPDATE public.ciclo_vita_periodico SET ultimo_giro = NULL, "
                      "lease_scade_il = NULL, lease_detentore = NULL")
    dip = dipendenze(db_url)
    with ambito_lavoratore("prova revisione R17"):
        esito = await periodico.giro(dip.registro, "test-periodico", datetime.now(UTC))
    assert esito["riprese_scarto"]["riprese"] == [sc["pid"]], esito
    eventi = [r["evento"] for r in await sql(
        db_url, "SELECT evento FROM public.conferme_lavoratore WHERE pratica_id = $1::uuid "
                "ORDER BY id", sc["pid"])]
    assert eventi[-1] == "RIPRESA_SISTEMA", eventi
    p = await _pratica(db_url, sc["pid"])
    assert p["lavoro"] == "ESEGUI" and p["dati"]["esecuzione"]["richiesta_da"] is None
    assert await _difetti(p) == []


async def test_richiesta_senza_autore_al_primo_giro_rifiutata(db_url, s3_ciclo) -> None:
    """Senza il fatto RIPRESA_SISTEMA un'esecuzione «senza autore» non prende per buono
    il firmatario della conferma, anche al primo giro (nessun giro chiuso)."""
    sc = await _scarto(db_url, s3_ciclo)
    async with client_app() as c:
        assert (await _tr(c, sc["pid"], sc["rsc"], "AUTORIZZATA_PRODUTTORE", "ESEGUIBILE",
                          conferma=await _conferma_scarto(db_url, sc, cd.SCARTO_APPROVA))
                ).status_code == 200
        assert (await _tr(c, sc["pid"], sc["rsc"], "ESEGUIBILE", "ESEGUITA",
                          conferma=await _conferma_scarto(db_url, sc, cd.SCARTO_ESEGUI))
                ).status_code == 202
    p = await _pratica(db_url, sc["pid"])
    p["dati"]["esecuzione"] = {**p["dati"]["esecuzione"], "richiesta_da": None}
    difetti = " ".join(await _difetti(p))
    assert "SCARTO_ESEGUI" in difetti and "CONFERMA_GIRO_PRECEDENTE" not in difetti, difetti


async def test_esenzione_della_transizione_vale_solo_per_il_giro_in_corso(db_url,
                                                                          s3_ciclo) -> None:
    sc = await _scarto_eseguito(db_url, s3_ciclo)
    await sql(db_url, "INSERT INTO public.conferme_transizione_r17 (pratica_id, azioni) VALUES "
                      "($1::uuid, ARRAY['SCARTO_APPROVA', 'SCARTO_ESEGUI'])", sc["pid"])
    p = await _nuovo_giro_dall_api(db_url, sc["pid"], sc["rsc_id"])
    await _collega(db_url, sc["pid"], {})
    difetti = " ".join(await _difetti(p))
    assert "SCARTO_ESEGUI" in difetti and "SCARTO_APPROVA" not in difetti, difetti
