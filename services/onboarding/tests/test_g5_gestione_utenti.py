# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione di sicurezza A1/B6 (g5-sicurezza) — gestione utenti su PostgreSQL reale.

A1: un gestore d'ente (ADMIN_TENANT, RSC_CONSERVATORE) non può prendere il
controllo di un utente di ruolo pari o superiore riscrivendone il codice
fiscale (legame SPID/CIE), lo stato o il ruolo; il codice fiscale di un utente
esistente lo cambia solo il SUPER_ADMIN; nessuno assegna da qui ruoli globali;
ogni modifica finisce in audit_global.
B6: chi genera una chiave API ne risponde solo se è un utente del MEDESIMO ente.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from httpx import ASGITransport, AsyncClient

from libs.shared.security.revoca import svuota_cache_revoche
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = richiede_pg

CF_VITTIMA = "FTZPRV00A01Z999V"
CF_ATTACCANTE = "VRDLGU85M01H501M"   # stesso CF della prova di concetto del revisore
CF_NUOVO = "MRTMTT25D09F205Z"


async def _ente(url: str) -> uuid.UUID:
    tid = uuid.uuid4()
    await sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
                   "stato, email_referente) VALUES ($1, $2, 'Conservatore', $3, 'ATTIVO', "
                   "'a@b.it')", tid, tid.hex[:11], f"t_{tid.hex[:20]}")
    return tid


async def _utente(url: str, tid: uuid.UUID, ruolo: str, cf: str | None = None,
                  attivo: bool = True) -> uuid.UUID:
    uid = uuid.uuid4()
    await sql(url, "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo, "
                   "codice_fiscale) VALUES ($1, $2, $3, 'Nome', 'Cognome', "
                   "CAST($4 AS public.user_ruolo), $5, $6)",
              uid, tid, f"{uid.hex[:8]}@x.it", ruolo, attivo, cf)
    return uid


def _tok(tid: uuid.UUID, uid: uuid.UUID | str, ruolo: str) -> str:
    return create_access_token(tenant_id=str(tid), user_id=str(uid), ruolo=ruolo)


async def _chiama(metodo: str, url: str, tok: str, corpo: Any = None) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(metodo, url, json=corpo, headers={"Authorization": f"Bearer {tok}"})


async def _riga(url: str, uid: uuid.UUID) -> dict[str, Any]:
    righe = await sql(url, "SELECT ruolo::text AS ruolo, codice_fiscale, attivo, nome "
                           "FROM public.users WHERE id = $1", uid)
    return dict(righe[0])


async def _audit(url: str, evento: str) -> list[dict[str, Any]]:
    righe = await sql(
        url, "SELECT tenant_id, user_id, dettagli::text AS d FROM public.audit_global "
                           "WHERE evento = $1 ORDER BY created_at", evento)
    return [{"tenant_id": r["tenant_id"], "user_id": r["user_id"], **json.loads(r["d"])}
            for r in righe]


class TestPresaDiControllo:
    """La prova di concetto della revisione: ora 403/404 e il DB non cambia."""

    @pytest.mark.parametrize("gestore", ["ADMIN_TENANT", "RSC_CONSERVATORE"])
    async def test_gestore_non_riscrive_il_cf_del_super_admin(self, db_url, gestore) -> None:
        tid = await _ente(db_url)
        sa = await _utente(db_url, tid, "SUPER_ADMIN", CF_VITTIMA)
        adm = await _utente(db_url, tid, gestore)
        prima = await _riga(db_url, sa)

        r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{sa}",
                          _tok(tid, adm, gestore), {"codice_fiscale": CF_ATTACCANTE})
        assert r.status_code == 403, r.text
        assert await _riga(db_url, sa) == prima
        assert prima["codice_fiscale"] == CF_VITTIMA and prima["ruolo"] == "SUPER_ADMIN"
        assert await _audit(db_url, "utente.modificato") == []

    @pytest.mark.parametrize("bersaglio", ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT",
                                           "API_SYSTEM"])
    @pytest.mark.parametrize("corpo", [{"attivo": True}, {"attivo": False}, {"nome": "Mario"},
                                       {"ruolo": "VIEWER"}])
    async def test_gestore_non_tocca_ruoli_pari_o_superiori_404(self, db_url, bersaglio,
                                                                corpo) -> None:
        tid = await _ente(db_url)
        vittima = await _utente(db_url, tid, bersaglio, attivo=not corpo.get("attivo", False))
        adm = await _utente(db_url, tid, "ADMIN_TENANT")
        prima = await _riga(db_url, vittima)

        r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{vittima}",
                          _tok(tid, adm, "ADMIN_TENANT"), corpo)
        assert r.status_code == 404, r.text
        assert await _riga(db_url, vittima) == prima

    @pytest.mark.parametrize("bersaglio", ["SUPER_ADMIN", "RSC_CONSERVATORE", "ADMIN_TENANT"])
    async def test_gestore_non_disattiva_ruoli_pari_o_superiori_404(self, db_url,
                                                                    bersaglio) -> None:
        tid = await _ente(db_url)
        vittima = await _utente(db_url, tid, bersaglio)
        adm = await _utente(db_url, tid, "RSC_CONSERVATORE")
        r = await _chiama("DELETE", f"/api/v1/onboarding/tenant/{tid}/utenti/{vittima}",
                          _tok(tid, adm, "RSC_CONSERVATORE"))
        assert r.status_code == 404
        assert (await _riga(db_url, vittima))["attivo"] is True
        assert await _audit(db_url, "utente.disattivato") == []

    async def test_gestore_non_cambia_il_cf_neppure_a_un_operatore_403(self, db_url) -> None:
        tid = await _ente(db_url)
        op = await _utente(db_url, tid, "OPERATORE", CF_VITTIMA)
        adm = await _utente(db_url, tid, "ADMIN_TENANT")
        for corpo in ({"codice_fiscale": CF_NUOVO}, {"codice_fiscale": None}):
            r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{op}",
                              _tok(tid, adm, "ADMIN_TENANT"), corpo)
            assert r.status_code == 403
        assert (await _riga(db_url, op))["codice_fiscale"] == CF_VITTIMA

    @pytest.mark.parametrize("ruolo", ["ADMIN_TENANT", "SUPER_ADMIN", "RSC_CONSERVATORE",
                                       "API_SYSTEM"])
    async def test_gestore_non_assegna_ruoli_superiori_o_globali_403(self, db_url,
                                                                     ruolo) -> None:
        tid = await _ente(db_url)
        op = await _utente(db_url, tid, "OPERATORE")
        adm = await _utente(db_url, tid, "ADMIN_TENANT")
        r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{op}",
                          _tok(tid, adm, "ADMIN_TENANT"), {"ruolo": ruolo})
        assert r.status_code == 403
        assert (await _riga(db_url, op))["ruolo"] == "OPERATORE"
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/utenti",
                          _tok(tid, adm, "ADMIN_TENANT"),
                          {"email": "n@x.it", "nome": "Nuovo", "cognome": "Utente",
                           "ruolo": ruolo})
        assert r.status_code == 403
        assert len(await sql(db_url, "SELECT 1 FROM public.users WHERE email = 'n@x.it'")) == 0


class TestModificheAmmesse:
    async def test_gestore_modifica_operatore_con_audit(self, db_url) -> None:
        tid = await _ente(db_url)
        op = await _utente(db_url, tid, "OPERATORE", CF_VITTIMA)
        adm = await _utente(db_url, tid, "ADMIN_TENANT")
        r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{tid}/utenti/{op}",
                          _tok(tid, adm, "ADMIN_TENANT"), {"ruolo": "VIEWER", "nome": "Gianni"})
        assert r.status_code == 200, r.text
        riga = await _riga(db_url, op)
        assert (riga["ruolo"], riga["nome"]) == ("VIEWER", "Gianni")

        [voce] = await _audit(db_url, "utente.modificato")
        assert voce["tenant_id"] == tid and voce["user_id"] == adm
        assert voce["utente_id"] == str(op) and voce["ruolo_chiamante"] == "ADMIN_TENANT"
        assert voce["campi"] == ["nome", "ruolo"]
        assert voce["precedente"]["ruolo"] == "OPERATORE"
        assert voce["nuovo"]["ruolo"] == "VIEWER" and voce["nuovo"]["nome"] == "Gianni"
        # il codice fiscale nell'audit è solo mascherato
        assert CF_VITTIMA not in json.dumps(voce, default=str)
        assert voce["nuovo"]["codice_fiscale"].endswith(CF_VITTIMA[-4:])

    async def test_gestore_disattiva_viewer_con_audit(self, db_url) -> None:
        tid = await _ente(db_url)
        vw = await _utente(db_url, tid, "VIEWER")
        adm = await _utente(db_url, tid, "ADMIN_TENANT")
        r = await _chiama("DELETE", f"/api/v1/onboarding/tenant/{tid}/utenti/{vw}",
                          _tok(tid, adm, "ADMIN_TENANT"))
        assert r.status_code == 204
        assert (await _riga(db_url, vw))["attivo"] is False
        [voce] = await _audit(db_url, "utente.disattivato")
        assert voce["utente_id"] == str(vw) and voce["nuovo"]["attivo"] is False

    async def test_super_admin_cambia_cf_e_assegna_admin_tenant(self, db_url) -> None:
        conservatore, ente = await _ente(db_url), await _ente(db_url)
        sa = await _utente(db_url, conservatore, "SUPER_ADMIN")
        op = await _utente(db_url, ente, "OPERATORE", CF_VITTIMA)
        tok = _tok(conservatore, sa, "SUPER_ADMIN")
        r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{ente}/utenti/{op}", tok,
                          {"codice_fiscale": CF_NUOVO, "ruolo": "ADMIN_TENANT"})
        assert r.status_code == 200, r.text
        riga = await _riga(db_url, op)
        assert (riga["codice_fiscale"], riga["ruolo"]) == (CF_NUOVO, "ADMIN_TENANT")
        [voce] = await _audit(db_url, "utente.modificato")
        assert voce["tenant_id"] == ente and voce["campi"] == ["codice_fiscale", "ruolo"]
        assert voce["precedente"]["codice_fiscale"].endswith(CF_VITTIMA[-4:])
        assert voce["nuovo"]["codice_fiscale"].endswith(CF_NUOVO[-4:])

        # nemmeno il SUPER_ADMIN assegna da qui i ruoli globali
        for ruolo in ("SUPER_ADMIN", "RSC_CONSERVATORE", "API_SYSTEM"):
            r = await _chiama("PUT", f"/api/v1/onboarding/tenant/{ente}/utenti/{op}", tok,
                              {"ruolo": ruolo})
            assert r.status_code == 403
        assert (await _riga(db_url, op))["ruolo"] == "ADMIN_TENANT"

    async def test_gestore_crea_operatore_con_cf_e_audit(self, db_url) -> None:
        """Decisione: alla CREAZIONE di un OPERATORE/VIEWER il gestore indica il CF
        (serve per l'accesso SPID e non consegna utenze esistenti né privilegi)."""
        tid = await _ente(db_url)
        adm = await _utente(db_url, tid, "ADMIN_TENANT")
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/utenti",
                          _tok(tid, adm, "ADMIN_TENANT"),
                          {"email": "op@x.it", "nome": "Ope", "cognome": "Ratore",
                           "ruolo": "OPERATORE", "codice_fiscale": CF_VITTIMA})
        assert r.status_code == 201, r.text
        [voce] = await _audit(db_url, "utente.creato")
        assert voce["utente_id"] == r.json()["id"] and voce["user_id"] == adm
        assert voce["nuovo"]["ruolo"] == "OPERATORE"


class TestResponsabileChiave:
    async def test_super_admin_di_altro_ente_non_diventa_responsabile(self, db_url,
                                                                      monkeypatch) -> None:
        from app.routers import utenti

        monkeypatch.setattr(utenti, "_PEPPER", b"pepper-di-prova-" * 4)
        conservatore, ente = await _ente(db_url), await _ente(db_url)
        sa = await _utente(db_url, conservatore, "SUPER_ADMIN")
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{ente}/api-keys",
                          _tok(conservatore, sa, "SUPER_ADMIN"), {"descrizione": "x"})
        assert r.status_code == 422
        assert len(await sql(db_url, "SELECT 1 FROM public.api_keys WHERE tenant_id = $1",
                             ente)) == 0
        # con un responsabile dell'ente la chiave si genera
        op = await _utente(db_url, ente, "OPERATORE")
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{ente}/api-keys",
                          _tok(conservatore, sa, "SUPER_ADMIN"), {"user_id": str(op)})
        assert r.status_code == 201


class TestRevoca:
    """B5: token valido di un amministratore disattivato o di un ente sospeso."""

    @pytest.fixture(autouse=True)
    def _cache(self):
        svuota_cache_revoche()
        yield
        svuota_cache_revoche()

    async def test_amministratore_disattivato_401(self, db_url) -> None:
        tid = await _ente(db_url)
        adm = await _utente(db_url, tid, "ADMIN_TENANT", attivo=False)
        for metodo, url in (("GET", f"/api/v1/onboarding/tenant/{tid}/utenti"),
                            ("GET", "/api/v1/onboarding/classi-documentali"),
                            ("GET", f"/api/v1/onboarding/tenant/{tid}")):
            r = await _chiama(metodo, url, _tok(tid, adm, "ADMIN_TENANT"))
            assert r.status_code == 401, (url, r.text)
            assert r.json()["codice"] == "AUTH-003"

    async def test_ente_sospeso_401(self, db_url) -> None:
        tid = await _ente(db_url)
        adm = await _utente(db_url, tid, "ADMIN_TENANT")
        tok = _tok(tid, adm, "ADMIN_TENANT")
        assert (await _chiama("GET", f"/api/v1/onboarding/tenant/{tid}/utenti",
                              tok)).status_code == 200
        await sql(db_url, "UPDATE public.tenants SET stato = 'SOSPESO' WHERE id = $1", tid)
        svuota_cache_revoche()
        r = await _chiama("POST", f"/api/v1/onboarding/tenant/{tid}/utenti", tok,
                          {"email": "x@x.it", "nome": "Xx", "cognome": "Y", "ruolo": "VIEWER"})
        assert r.status_code == 401
        assert len(await sql(db_url, "SELECT 1 FROM public.users WHERE email = 'x@x.it'")) == 0
