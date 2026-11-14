# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R04 integrazione — il consumer delle code di scarto si collega a MSK con IAM
(KAFKA_AUTENTICAZIONE=iam) e in chiaro in locale."""
from __future__ import annotations

from app.services import consumer_scarti

from libs.shared.kafka import connessione


async def test_consumer_scarti_sasl_ssl_con_iam_e_plaintext_in_locale(monkeypatch) -> None:
    connessione._fornitori.clear()  # noqa: SLF001
    monkeypatch.delenv("KAFKA_AUTENTICAZIONE", raising=False)
    locale = consumer_scarti.crea_consumer()
    assert locale._client._security_protocol == "PLAINTEXT"  # noqa: SLF001
    await locale.stop()
    monkeypatch.setenv("KAFKA_AUTENTICAZIONE", "iam")
    monkeypatch.setenv("KAFKA_REGIONE", "eu-south-1")
    msk = consumer_scarti.crea_consumer()
    assert msk._client._security_protocol == "SASL_SSL"  # noqa: SLF001
    assert msk._client._sasl_mechanism == "OAUTHBEARER"  # noqa: SLF001
    await msk.stop()
    connessione._fornitori.clear()  # noqa: SLF001
