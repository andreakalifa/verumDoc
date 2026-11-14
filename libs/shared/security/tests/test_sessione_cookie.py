# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 (VD-C-079) — controlli puri della sessione in cookie: doppio invio del
token anti-CSRF e origine. Il middleware sull'applicazione e il flusso SPID
vero sono in services/gateway/tests/test_r05_sessione_cookie.py.
"""
from __future__ import annotations

import pytest

from libs.shared.security.sessione_cookie import (
    COOKIE_CSRF,
    motivo_rifiuto_csrf,
    origine_della_richiesta,
    origini_ammesse_da_ambiente,
)


def _scope(metodo: str = "POST", schema: str = "https") -> dict:
    return {"type": "http", "method": metodo, "scheme": schema}


def _h(csrf: str | None = None, **valori: str) -> dict[bytes, bytes]:
    intestazioni = {k.replace("_", "-").encode(): v.encode() for k, v in valori.items()}
    if csrf is not None:
        intestazioni[b"x-csrf-token"] = csrf.encode()
    return intestazioni


BISCOTTI = {COOKIE_CSRF: "abc"}


@pytest.mark.parametrize("metodo", ["GET", "HEAD", "OPTIONS"])
def test_metodi_sicuri_senza_controlli(metodo):
    assert motivo_rifiuto_csrf(_scope(metodo), _h(host="p.it"), {}, ()) is None


def test_post_ammesso_solo_con_token_e_origine_giusti():
    h = _h(host="p.it", origin="https://p.it", csrf="abc")
    assert motivo_rifiuto_csrf(_scope(), h, BISCOTTI, ()) is None
    assert motivo_rifiuto_csrf(_scope(), _h(host="p.it", origin="https://p.it"), BISCOTTI, ())
    assert motivo_rifiuto_csrf(_scope(), _h(host="p.it", origin="https://p.it",
                                            csrf="abd"), BISCOTTI, ())
    assert motivo_rifiuto_csrf(_scope(), h, {}, ())  # cookie anti-CSRF assente
    assert motivo_rifiuto_csrf(_scope(), _h(host="p.it", origin="https://x.it",
                                            csrf="abc"), BISCOTTI, ())
    assert motivo_rifiuto_csrf(_scope(), _h(host="p.it", origin="null",
                                            csrf="abc"), BISCOTTI, ())
    assert motivo_rifiuto_csrf(_scope(), _h(host="p.it", csrf="abc"), BISCOTTI, ())
    # schema diverso: http://p.it non è https://p.it
    assert motivo_rifiuto_csrf(_scope(), _h(host="p.it", origin="http://p.it",
                                            csrf="abc"), BISCOTTI, ())


def test_origine_configurata_e_proxy(monkeypatch):
    h = _h(host="api.p.it", origin="https://console.p.it", csrf="abc")
    assert motivo_rifiuto_csrf(_scope(), h, BISCOTTI, ())
    assert motivo_rifiuto_csrf(_scope(), h, BISCOTTI, ("https://console.p.it",)) is None
    monkeypatch.setenv("SESSIONE_ORIGINI_AMMESSE", " https://Console.P.it , ")
    assert origini_ammesse_da_ambiente() == ("https://console.p.it",)
    assert origine_della_richiesta(_scope(schema="http"),
                                   _h(host="p.it", x_forwarded_proto="https")) == "https://p.it"
