# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 2 di R05 — rilievo S2: il processo API di ciclo-vita (esposto
all'ingress, fa upload e parsing di ZIP e XML) NON riceve né pretende le
credenziali verso gli altri servizi (token del registro, chiave dei token di
canale, token di distribution): le ha solo il lavoratore dedicato.

Prima della correzione ``problemi_avvio`` le pretendeva per l'API in
produzione (e il Secret unico ``ciclo-vita-secrets`` le dava al pod API).
"""
from __future__ import annotations

from pydantic import SecretStr

CREDENZIALI = ("HASHCHAIN_TOKEN_REGISTRO", "CICLO_VITA_TOKEN_CHIAVE_PRIVATA",
               "DISTRIBUTION_TOKEN_SERVIZIO")


def _produzione(monkeypatch, *, lavoratore: bool, credenziali: bool) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "lavoratore_attivo", lavoratore)
    valore = SecretStr("presente" if credenziali else "")
    for campo in ("hashchain_token_registro", "ciclo_vita_token_chiave_privata",
                  "distribution_token_servizio"):
        monkeypatch.setattr(settings, campo, valore)


def test_api_in_produzione_parte_senza_credenziali_del_lavoratore(monkeypatch) -> None:
    from app.main import problemi_processo_api

    _produzione(monkeypatch, lavoratore=False, credenziali=False)
    problemi = " ".join(problemi_processo_api())
    for chiave in CREDENZIALI:
        assert chiave not in problemi, problemi


def test_api_in_produzione_con_credenziali_del_lavoratore_non_parte(monkeypatch) -> None:
    """Un Secret sbagliato (credenziali del lavoratore nel pod API) ferma l'avvio."""
    from app.main import problemi_processo_api

    _produzione(monkeypatch, lavoratore=False, credenziali=True)
    problemi = " ".join(problemi_processo_api())
    assert "credenziali del lavoratore nel processo API" in problemi
    for chiave in CREDENZIALI:
        assert chiave in problemi


def test_lavoratore_in_produzione_pretende_le_credenziali(monkeypatch) -> None:
    from app.main import problemi_avvio

    _produzione(monkeypatch, lavoratore=True, credenziali=False)
    problemi = " ".join(problemi_avvio(lavoratore=True))
    for chiave in CREDENZIALI:
        assert chiave in problemi


async def test_info_dell_api_non_espone_dipendenze_del_lavoratore(monkeypatch) -> None:
    from app.config import settings

    from tests.supporto import client_app

    monkeypatch.setattr(settings, "lavoratore_attivo", False)
    async with client_app() as c:
        corpo = (await c.get("/api/v1/ciclo-vita/info")).json()
    assert corpo["lavoratore_nel_processo"] is False
    assert corpo["dipendenze"] == "nel lavoratore ciclo-vita-lavoratore"
