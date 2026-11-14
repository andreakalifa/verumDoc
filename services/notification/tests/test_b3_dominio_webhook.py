# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Regole pure dei webhook: URL e SSRF, firma, ritentativi — contratto C3 (agente R03 b3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.domain.webhook import (
    FALLITA,
    IN_CODA,
    STATI_CONSEGNA,
    UrlNonValido,
    calcola_firma,
    corpo_consegna,
    esito_tentativo,
    genera_segreto,
    indirizzo_ammesso,
    intestazione_firma,
    suffisso_segreto,
    valida_url,
    verifica_firma,
)

# Esempio pubblicato in docs/integrazione/webhook.md; la firma attesa è stata
# calcolata anche con `openssl dgst -sha256 -hmac` (oracolo esterno, REGOLE §23.5).
ESEMPIO_SEGRETO = "whsec_esempio-di-segreto-non-usare"
ESEMPIO_ISTANTE = 1792576800
ESEMPIO_EVENTO = {
    "id": 42, "tipo": "versamento.ricevuto", "creato_il": "2026-10-21T10:00:00+00:00",
    "risorsa": {"tipo": "versamento", "id": "3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"},
    "dati": {"pdv_id": "3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"},
}
ESEMPIO_CORPO = (
    b'{"creato_il":"2026-10-21T10:00:00+00:00","dati":{"pdv_id":"3f1c0b9e-2d4a-4c55-9a1e-'
    b'7b0c2f6d8e90"},"id":42,"risorsa":{"id":"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90",'
    b'"tipo":"versamento"},"tipo":"versamento.ricevuto"}'
)
ESEMPIO_FIRMA = "362a779b910d922709231ed641f88061f03db35fa8a4a1bc939f4afe65b228ad"


def test_firma_esempio_documentato_verificabile() -> None:
    assert corpo_consegna(ESEMPIO_EVENTO) == ESEMPIO_CORPO
    assert calcola_firma(ESEMPIO_SEGRETO, ESEMPIO_ISTANTE, ESEMPIO_CORPO) == ESEMPIO_FIRMA
    assert intestazione_firma(ESEMPIO_SEGRETO, ESEMPIO_ISTANTE, ESEMPIO_CORPO) == (
        f"t={ESEMPIO_ISTANTE},v1={ESEMPIO_FIRMA}")


def test_verifica_firma_corpo_alterato_segreto_errato_e_replay() -> None:
    h = intestazione_firma(ESEMPIO_SEGRETO, ESEMPIO_ISTANTE, ESEMPIO_CORPO)
    assert verifica_firma(ESEMPIO_SEGRETO, h, ESEMPIO_CORPO, adesso=ESEMPIO_ISTANTE + 10)
    assert not verifica_firma(ESEMPIO_SEGRETO, h, ESEMPIO_CORPO + b" ", adesso=ESEMPIO_ISTANTE)
    assert not verifica_firma("whsec_altro", h, ESEMPIO_CORPO, adesso=ESEMPIO_ISTANTE)
    # anti-replay: fuori dalla tolleranza di 5 minuti la stessa firma non vale
    assert not verifica_firma(ESEMPIO_SEGRETO, h, ESEMPIO_CORPO, adesso=ESEMPIO_ISTANTE + 301)
    assert not verifica_firma(ESEMPIO_SEGRETO, "v1=abc", ESEMPIO_CORPO, adesso=ESEMPIO_ISTANTE)


def test_genera_segreto_casuale_con_prefisso() -> None:
    a, b = genera_segreto(), genera_segreto()
    assert a != b and a.startswith("whsec_") and len(a) >= 6 + 43
    assert suffisso_segreto(a) == a[-4:]


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",  # metadati cloud
    "100.64.0.1", "0.0.0.0", "224.0.0.1",  # noqa: S104 — indirizzi da rifiutare
    "255.255.255.255", "::1", "fc00::1", "fe80::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:7f00:1::1", "2001:db8::1", "non-un-ip",
])
def test_indirizzo_ammesso_rifiuta_reti_non_globali(ip: str) -> None:
    assert indirizzo_ammesso(ip) is False


# R03 f2 (REV-14): elenco di ammessi; un IPv4 incapsulato in IPv6 (::ffff:8.8.8.8) è
# fuori da 2000::/3 e si rifiuta anche se pubblico (vedi tests/test_f2_ssrf.py)
@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
def test_indirizzo_ammesso_accetta_indirizzi_pubblici(ip: str) -> None:
    assert indirizzo_ammesso(ip) is True


def test_indirizzo_ammesso_privati_solo_con_impostazione_di_sviluppo() -> None:
    assert indirizzo_ammesso("127.0.0.1", consenti_privati=True)
    assert indirizzo_ammesso("10.0.0.1", consenti_privati=True)
    # mai l'indirizzo non specificato né il multicast
    assert not indirizzo_ammesso("0.0.0.0", consenti_privati=True)  # noqa: S104
    assert not indirizzo_ammesso("224.0.0.1", consenti_privati=True)


def test_valida_url_scompone_host_porta_percorso() -> None:
    d = valida_url("https://Hook.Cliente.it:8443/eventi?canale=a", lunghezza_massima=2048)
    assert (d.host, d.porta, d.percorso) == ("hook.cliente.it", 8443, "/eventi?canale=a")
    d = valida_url("https://hook.cliente.it", lunghezza_massima=2048)
    assert (d.porta, d.percorso) == (443, "/")


@pytest.mark.parametrize(("url", "codice"), [
    ("http://hook.cliente.it/x", "WH-001"),
    ("ftp://hook.cliente.it/x", "WH-001"),
    ("https://utente:pw@hook.cliente.it/x", "WH-001"),
    ("https://hook.cliente.it/x#frammento", "WH-001"),
    ("https:///solo-percorso", "WH-001"),
    ("https://hook.cliente.it/a b", "WH-001"),
    ("https://hook.cliente.it:99999/", "WH-001"),
    ("https://" + "a" * 2050 + ".it/", "WH-001"),
    ("https://127.0.0.1/x", "WH-002"),
    ("https://[::1]/x", "WH-002"),
    ("https://169.254.169.254/latest/meta-data", "WH-002"),
    ("https://localhost/x", "WH-002"),
    ("https://servizio-interno:8080/x", "WH-002"),
])
def test_valida_url_rifiuti(url: str, codice: str) -> None:
    with pytest.raises(UrlNonValido) as e:
        valida_url(url, lunghezza_massima=2048)
    assert e.value.codice == codice


def test_valida_url_loopback_ammesso_in_sviluppo() -> None:
    d = valida_url("https://127.0.0.1:9443/x", lunghezza_massima=2048, consenti_privati=True)
    assert (d.host, d.porta) == ("127.0.0.1", 9443)
    assert valida_url("https://localhost/x", lunghezza_massima=2048,
                      consenti_privati=True).host == "localhost"


def test_esito_tentativo_segue_la_lista_dei_ritardi_poi_fallita() -> None:
    adesso = datetime(2026, 10, 21, 12, tzinfo=UTC)
    ritardi = [60, 300, 1500]
    attese = []
    tentativi = 0
    while True:
        e = esito_tentativo(tentativi, ritentabile=True, adesso=adesso, ritardi_secondi=ritardi)
        tentativi = e.tentativi
        if e.stato == FALLITA:
            break
        assert e.stato == IN_CODA
        attese.append(e.prossimo_tentativo - adesso)
    assert attese == [timedelta(seconds=s) for s in ritardi]
    assert tentativi == len(ritardi) + 1


def test_esito_tentativo_non_ritentabile_fallita_subito() -> None:
    e = esito_tentativo(0, ritentabile=False, adesso=datetime.now(UTC), ritardi_secondi=[60])
    assert (e.stato, e.tentativi, e.prossimo_tentativo) == (FALLITA, 1, None)


def test_stati_consegna_allineati_al_router() -> None:
    """Il filtro `stato` dell'API accetta esattamente gli stati del dominio."""
    from app.api.webhook import StatoConsegna

    assert set(StatoConsegna.__args__) == set(STATI_CONSEGNA)
