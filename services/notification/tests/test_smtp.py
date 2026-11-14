# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Invio SMTP reale contro server simulati (aiosmtpd) — VD-C-024.
"""

from __future__ import annotations

import pytest
from app.config import Settings
from app.services.smtp import (
    INVIATA,
    PERMANENTE,
    TEMPORANEO,
    componi,
    config_da_settings,
    indirizzo_plausibile,
    invia_email,
    problemi_configurazione,
    sicurezza_effettiva,
)

from tests.conftest import config_smtp


def test_sicurezza_auto_porta_587_starttls_465_tls():
    """La 587 usa STARTTLS (non TLS implicito, era il bug), la 465 TLS implicito."""
    assert sicurezza_effettiva(587, "auto") == "starttls"
    assert sicurezza_effettiva(465, "auto") == "tls"
    assert sicurezza_effettiva(25, "auto") == "starttls"
    assert sicurezza_effettiva(2525, "nessuna") == "nessuna"


def test_configurazione_vuota_posta_non_configurata():
    cfg = Settings(env="development")
    assert config_da_settings(cfg) is None
    assert "SMTP_HOST non impostato" in problemi_configurazione(cfg)


def test_configurazione_587_tls_implicito_segnalata():
    cfg = Settings(
        smtp_host="smtp.x.it", smtp_port=587, smtp_sicurezza="tls", smtp_mittente="a@x.it"
    )
    assert any("587" in p for p in problemi_configurazione(cfg))


def test_configurazione_in_chiaro_vietata_in_produzione():
    cfg = Settings(
        env="production",
        smtp_host="smtp.x.it",
        smtp_port=25,
        smtp_sicurezza="nessuna",
        smtp_mittente="a@x.it",
    )
    assert config_da_settings(cfg) is None


@pytest.mark.parametrize(
    "indirizzo",
    ["tenant-1@verumdoc.local", "x@example.com", "", "a@b", "a@x.invalid", "due@@x.it", "a b@x.it"],
)
def test_indirizzi_segnaposto_non_plausibili(indirizzo):
    assert indirizzo_plausibile(indirizzo) is False


def test_indirizzo_reale_plausibile():
    assert indirizzo_plausibile("referente@comune.esempio.it") is True


@pytest.mark.asyncio
async def test_invio_starttls_587(smtp_starttls, certificato_tls):
    """STARTTLS: il messaggio arriva cifrato al server simulato."""
    cfg = config_smtp(smtp_starttls.porta, "starttls", certificato_tls["cert"])
    esito = await invia_email(cfg, componi(cfg, "referente@cliente.it", "Oggetto", "Testo"))
    assert esito.esito == INVIATA, esito.messaggio
    assert esito.message_id
    assert smtp_starttls.gestore.tls_usato == [True]
    assert smtp_starttls.gestore.ricevuti[0].rcpt_tos == ["referente@cliente.it"]


@pytest.mark.asyncio
async def test_invio_tls_implicito_465(smtp_tls_implicito, certificato_tls):
    cfg = config_smtp(smtp_tls_implicito.porta, "tls", certificato_tls["cert"])
    esito = await invia_email(
        cfg, componi(cfg, "referente@cliente.it", "Oggetto", "Testo", "<p>x</p>")
    )
    assert esito.esito == INVIATA, esito.messaggio
    assert len(smtp_tls_implicito.gestore.ricevuti) == 1


@pytest.mark.asyncio
async def test_certificato_non_attendibile_errore_permanente(smtp_starttls):
    """Senza la CA del server il certificato non si accetta: niente invio, errore permanente."""
    cfg = config_smtp(smtp_starttls.porta, "starttls")
    esito = await invia_email(cfg, componi(cfg, "referente@cliente.it", "O", "T"))
    assert esito.esito in (PERMANENTE, TEMPORANEO)
    assert esito.esito != INVIATA
    assert smtp_starttls.gestore.ricevuti == []


@pytest.mark.asyncio
async def test_starttls_richiesto_ma_server_senza_tls(smtp_in_chiaro):
    """Se il server non offre STARTTLS non si degrada in chiaro."""
    cfg = config_smtp(smtp_in_chiaro.porta, "starttls")
    esito = await invia_email(cfg, componi(cfg, "referente@cliente.it", "O", "T"))
    assert esito.esito == PERMANENTE
    assert smtp_in_chiaro.gestore.ricevuti == []


@pytest.mark.asyncio
async def test_destinatario_rifiutato_550_permanente(smtp_in_chiaro):
    cfg = config_smtp(smtp_in_chiaro.porta, "nessuna")
    esito = await invia_email(cfg, componi(cfg, "rifiuta@cliente.it", "O", "T"))
    assert esito.esito == PERMANENTE and esito.codice_smtp == 550
    assert not esito.ritentabile


@pytest.mark.asyncio
async def test_destinatario_451_temporaneo(smtp_in_chiaro):
    cfg = config_smtp(smtp_in_chiaro.porta, "nessuna")
    esito = await invia_email(cfg, componi(cfg, "occupato@cliente.it", "O", "T"))
    assert esito.esito == TEMPORANEO and esito.ritentabile


@pytest.mark.asyncio
async def test_server_irraggiungibile_temporaneo():
    from tests.conftest import _porta_libera

    cfg = config_smtp(_porta_libera(), "nessuna")
    esito = await invia_email(cfg, componi(cfg, "referente@cliente.it", "O", "T"))
    assert esito.esito == TEMPORANEO
