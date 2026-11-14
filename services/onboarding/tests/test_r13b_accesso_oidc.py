# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R13b (VD-C-069) — profilo OIDC dell'aggregatore SPID/CIE in console: campi facoltativi
validati, predefiniti come prima, federazione con chiavi del Trust Anchor in JWKS."""
from __future__ import annotations

import pytest
from app.routers.accesso import valida_configurazione

from libs.shared.errori import ErroreApi

BASE = {"aggregatore_url": "https://agg.esempio.it", "issuer": "https://agg.esempio.it",
        "jwks_url": "https://agg.esempio.it/jwks", "client_id": "vd", "livello_minimo": 2}


def test_predefiniti_come_prima() -> None:
    conf = valida_configurazione("SPID_CIE", BASE)
    assert conf["metodo_autenticazione"] == "client_secret_post"
    assert conf["request_object"] is False and conf["attributi_da_userinfo"] is False


def test_profilo_spid_oidc_completo() -> None:
    conf = valida_configurazione("SPID_CIE", BASE | {
        "metodo_autenticazione": "private_key_jwt", "request_object": True,
        "attributi_da_userinfo": True, "prompt": "consent login",
        "token_url": "https://agg.esempio.it/oidc/token", "jwks_url": "",
        "federazione": {"trust_anchor": "https://ta.esempio.it",
                        "entita_op": "https://agg.esempio.it",
                        "chiavi_trust_anchor": {"keys": [{"kty": "RSA", "kid": "t1",
                                                          "n": "x", "e": "AQAB"}]}}})
    assert conf["federazione"]["trust_anchor"] == "https://ta.esempio.it"
    assert conf["jwks_url"] == "" and conf["token_url"].endswith("/oidc/token")


@pytest.mark.parametrize("modifica, motivo", [
    ({"metodo_autenticazione": "none"}, "Autenticazione del cliente"),
    ({"prompt": "select_account"}, "prompt"),
    ({"jwks_url": "", "federazione": {"trust_anchor": "https://ta.esempio.it",
                                      "entita_op": "https://agg.esempio.it",
                                      "chiavi_trust_anchor": {"keys": [{"kty": "RSA"}]}}},
     "Trust Anchor"),
    ({"client_secret": "x"}, "segreti"),
])
def test_rifiuti(modifica, motivo) -> None:
    with pytest.raises(ErroreApi) as errore:
        valida_configurazione("SPID_CIE", BASE | modifica)
    assert motivo in str(errore.value.dettagli)
