# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 b4 — parti pure dei moduli condivisi d'accesso: forma delle chiavi API dalle
intestazioni, token di canale (contratto C2), quote per piano (VD-C-099).
Le parti con il database sono provate su PostgreSQL vero nei servizi
(gateway tests/test_b4_accesso_canali_quote.py, distribution tests/test_b4_*).
"""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from libs.shared.errori import ErroreApi
from libs.shared.security.chiavi_api import genera_chiave_api
from libs.shared.security.emetti_token_servizio import main as emetti
from libs.shared.security.jwt_rs256 import ErroreTokenNonValido, firma_token
from libs.shared.security.quote import (
    ImpostazioniQuote,
    QuotaSuperata,
    chiave_pdv_giorno,
    consuma_richiesta,
    controlla_quota_pdv,
    limite_pdv,
    limite_richieste,
    registra_pdv_versato,
    secondi_alla_mezzanotte,
)
from libs.shared.security.token_canale import (
    PUBBLICO_TOKEN_CANALE,
    ServizioCanale,
    firma_token_canale,
    servizi_da_configurazione,
    verifica_token_canale,
)
from libs.shared.security.verifica_chiavi_api import (
    ChiaveApiRifiutata,
    ChiaviApiNonDisponibili,
    IdentitaChiaveApi,
    VerificaChiaveNonEseguibile,
    chiave_da_intestazioni,
    errore_api_chiave,
    verifica_chiave_api,
)


def _coppia() -> tuple[str, str]:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
    pub = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


PRIV, PUB = _coppia()
ALTRA_PRIV, ALTRA_PUB = _coppia()
T, U = str(uuid.uuid4()), str(uuid.uuid4())
SFTP = ServizioCanale("sftp-ingest", frozenset({"SFTP"}), PUB)


# ── Chiave API dalle intestazioni ─────────────────────────────────────

def test_chiave_da_intestazioni_tre_forme_stessa_chiave():
    chiave = genera_chiave_api(b"p" * 40).chiave
    ident = chiave.split("_")[1]
    segreto = chiave.split("_", 2)[2]
    assert chiave_da_intestazioni(chiave, None, None) == chiave
    assert chiave_da_intestazioni(None, f"vd_{ident}", chiave) == chiave
    assert chiave_da_intestazioni(None, ident, segreto) == chiave
    assert chiave_da_intestazioni(None, None, None) is None


@pytest.mark.parametrize("intestazioni", [
    ("sk_test", None, None),
    (None, None, "solo-segreto-" + "a" * 40),
    (None, "vd_aaaaaaaaaa", None),
])
def test_chiave_da_intestazioni_forma_errata_auth_001(intestazioni):
    if intestazioni == (None, "vd_aaaaaaaaaa", None):
        assert chiave_da_intestazioni(*intestazioni) is None  # nessun segreto: nessuna chiave
        return
    with pytest.raises(ChiaveApiRifiutata) as errore:
        chiave_da_intestazioni(*intestazioni)
    assert errore.value.codice == "AUTH-001"


def test_chiave_da_intestazioni_identificativo_di_altra_chiave_rifiutato():
    a, b = genera_chiave_api(b"p" * 40).chiave, genera_chiave_api(b"p" * 40).chiave
    with pytest.raises(ChiaveApiRifiutata):
        chiave_da_intestazioni(None, b.split("_")[1], a)


async def test_verifica_chiave_api_senza_pepper_non_disponibile():
    with pytest.raises(ChiaviApiNonDisponibili):
        await verifica_chiave_api("vd_abcdefghij_" + "a" * 43, None)


def test_errore_api_chiave_codici_del_catalogo():
    e = errore_api_chiave(ChiaveApiRifiutata("AUTH-002", "scaduta"))
    assert isinstance(e, ErroreApi) and e.codice == "AUTH-002" and e.status == 401
    assert errore_api_chiave(ChiaveApiRifiutata("AUTH-001", "x")).codice == "AUTH-001"
    assert errore_api_chiave(ChiaviApiNonDisponibili()).codice == "AUTH-030"
    assert errore_api_chiave(VerificaChiaveNonEseguibile()).codice == "AUTH-503"


def test_identita_chiave_payload_come_jwt():
    p = IdentitaChiaveApi("k1", T, U, ("read",)).payload()
    assert p["ruolo"] == "API_SYSTEM" and p["tenant_id"] == T and p["user_id"] == U
    assert p["scopes"] == ["read"] and p["auth_method"] == "api_key"


# ── Token di canale ───────────────────────────────────────────────────

def _token(**kw) -> str:
    base = {"tenant_id": T, "user_id": U, "ruolo": "OPERATORE", "canale": "SFTP",
                "chiave_privata_pem": PRIV, "emittente": "verumdoc.it"}
    base.update(kw)
    return firma_token_canale("sftp-ingest", **base)


def test_token_canale_valido():
    p = verifica_token_canale(_token(), servizi=[SFTP], emittente="verumdoc.it")
    assert p["servizio"] == "sftp-ingest" and p["canale"] == "SFTP"
    assert p["tenant_id"] == T and p["user_id"] == U and p["aud"] == PUBBLICO_TOKEN_CANALE
    assert p["exp"] - p["iat"] == 300


def test_token_canale_firmato_da_altra_chiave_rifiutato():
    with pytest.raises(ErroreTokenNonValido) as e:
        verifica_token_canale(_token(chiave_privata_pem=ALTRA_PRIV), servizi=[SFTP],
                              emittente="verumdoc.it")
    assert e.value.codice == "AUTH-001"


def test_token_canale_servizio_diverso_da_quello_della_chiave_rifiutato():
    """Un servizio ammesso non può firmare token a nome di un altro."""
    altro = ServizioCanale("altro", frozenset({"SFTP"}), ALTRA_PUB)
    token = firma_token_canale("sftp-ingest", tenant_id=T, user_id=U, ruolo="OPERATORE",
                               canale="SFTP", chiave_privata_pem=ALTRA_PRIV,
                               emittente="verumdoc.it")
    with pytest.raises(ErroreTokenNonValido):
        verifica_token_canale(token, servizi=[SFTP, altro], emittente="verumdoc.it")


def test_token_canale_canale_non_ammesso_per_il_servizio():
    solo_altro = ServizioCanale("sftp-ingest", frozenset({"ALTRO"}), PUB)
    with pytest.raises(ErroreTokenNonValido):
        verifica_token_canale(_token(), servizi=[solo_altro], emittente="verumdoc.it")


def test_token_utente_non_vale_come_token_canale():
    """Un token utente (senza servizio/canale) non passa come token di canale,
    nemmeno se firmato con la chiave del servizio."""
    utente = firma_token({"tenant_id": T, "user_id": U, "ruolo": "OPERATORE"},
                         chiave_privata_pem=PRIV, emittente="verumdoc.it",
                         pubblico="verumdoc-api", durata_secondi=60)
    with pytest.raises(ErroreTokenNonValido):
        verifica_token_canale(utente, servizi=[SFTP], emittente="verumdoc.it")


def test_token_canale_con_aud_dei_token_utente_rifiutato():
    """R03 i1: il vecchio formato di sftp-ingest (aud verumdoc-api, il pubblico dei
    token utente) non vale più, nemmeno con la chiave dedicata del servizio;
    ``tipo_token`` resta facoltativo (un token senza, con aud giusto, passa)."""
    token = firma_token({"sub": "servizio:sftp-ingest", "servizio": "sftp-ingest",
                         "tenant_id": T, "user_id": U, "ruolo": "OPERATORE", "canale": "SFTP",
                         "scopes": ["versamento:ud", "read"]},
                        chiave_privata_pem=PRIV, emittente="verumdoc.it",
                        pubblico="verumdoc-api", durata_secondi=60)
    with pytest.raises(ErroreTokenNonValido):
        verifica_token_canale(token, servizi=[SFTP], emittente="verumdoc.it")
    senza_tipo = firma_token({"sub": "servizio:sftp-ingest", "servizio": "sftp-ingest",
                              "tenant_id": T, "user_id": U, "ruolo": "OPERATORE",
                              "canale": "SFTP"}, chiave_privata_pem=PRIV,
                             emittente="verumdoc.it", pubblico="verumdoc-canale",
                             durata_secondi=60)
    assert verifica_token_canale(senza_tipo, servizi=[SFTP],
                                 emittente="verumdoc.it")["canale"] == "SFTP"
    altro_pubblico = firma_token({"sub": "servizio:sftp-ingest", "servizio": "sftp-ingest",
                                  "tenant_id": T, "user_id": U, "ruolo": "OPERATORE",
                                  "canale": "SFTP"}, chiave_privata_pem=PRIV,
                                 emittente="verumdoc.it", pubblico="verumdoc-signature",
                                 durata_secondi=60)
    with pytest.raises(ErroreTokenNonValido):
        verifica_token_canale(altro_pubblico, servizi=[SFTP], emittente="verumdoc.it")


def test_token_canale_scaduto_auth_002():
    vecchio = _token(durata_secondi=60, adesso=int(time.time()) - 600)
    with pytest.raises(ErroreTokenNonValido) as e:
        verifica_token_canale(vecchio, servizi=[SFTP], emittente="verumdoc.it")
    assert e.value.codice == "AUTH-002"


@pytest.mark.parametrize("kw", [{"canale": "PDND"}, {"canale": "REST"}, {"ruolo": ""},
                                {"tenant_id": "non-uuid"}, {"durata_secondi": 7200},
                                {"durata_secondi": 0}])
def test_firma_token_canale_dati_non_validi(kw):
    with pytest.raises(ValueError):
        _token(**kw)


def test_servizi_da_configurazione_senza_chiave_esclusi():
    assert servizi_da_configurazione({"sftp-ingest": ["SFTP"]}, {}, chiave_ripiego_pem=None) == []
    con = servizi_da_configurazione({"sftp-ingest": ["SFTP"]}, {}, chiave_ripiego_pem=PUB)
    assert con[0].chiave_pubblica_pem == PUB


def test_emetti_token_servizio_modo_canale(monkeypatch, capsys):
    monkeypatch.setenv("JWT_PRIVATE_KEY", PRIV)
    monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
    esito = emetti(["--servizio", "sftp-ingest", "--canale", "SFTP", "--ente", T,
                    "--utente", U, "--ruolo", "OPERATORE", "--secondi", "120"])
    assert esito == 0
    token = capsys.readouterr().out.strip()
    p = verifica_token_canale(token, servizi=[SFTP], emittente="verumdoc.it")
    assert p["exp"] - p["iat"] == 120
    assert emetti(["--servizio", "sftp-ingest", "--canale", "PDND", "--ente", T,
                   "--utente", U, "--ruolo", "OPERATORE"]) == 2


# ── Quote ─────────────────────────────────────────────────────────────

class _Redis:
    """Redis in memoria minimo (ZSET in pipeline, GET/INCR/EXPIRE)."""

    def __init__(self) -> None:
        self.z: dict[str, dict[str, float]] = {}
        self.v: dict[str, int] = {}

    def pipeline(self):
        r, comandi = self, []

        class P:
            def __getattr__(self, nome):
                return lambda *a, **k: comandi.append((nome, a, k))

            async def execute(self):
                out = []
                for nome, a, _k in comandi:
                    z = r.z.setdefault(a[0], {})
                    if nome == "zremrangebyscore":
                        for m in [m for m, s in z.items() if a[1] <= s <= a[2]]:
                            del z[m]
                        out.append(0)
                    elif nome == "zcard":
                        out.append(len(z))
                    elif nome == "zrange":
                        out.append(sorted(z.items(), key=lambda x: x[1])[:1])
                    elif nome == "zadd":
                        z.update(a[1])
                        out.append(1)
                    else:
                        out.append(True)
                return out
        return P()

    async def get(self, k):
        return self.v.get(k)

    async def incr(self, k):
        self.v[k] = self.v.get(k, 0) + 1
        return self.v[k]

    async def expire(self, k, s):
        return True


def test_limiti_da_impostazioni_e_piano_sconosciuto():
    imp = ImpostazioniQuote(richieste_minuto={"starter": 10, "Gold": 99}, pdv_giorno={"starter": 5})
    assert limite_richieste("gold", imp) == 99
    assert limite_richieste("ignoto", imp) == 10
    assert limite_pdv("ignoto", imp) == 5


def test_limiti_predefiniti_r03():
    """R03 i3: predefiniti alzati per l'obiettivo di 1M documenti/giorno (prima
    60/300/1000 richieste e 100/500/2000 PdV, i valori di R02)."""
    imp = ImpostazioniQuote()
    piani = ("starter", "business", "enterprise")
    assert [limite_richieste(p, imp) for p in piani] == [120, 600, 6000]
    assert [limite_pdv(p, imp) for p in piani] == [1000, 20000, 200000]


async def test_consuma_richiesta_finestra_scorrevole_e_retry_after():
    r = _Redis()
    await consuma_richiesta(r, "k", limite=2, piano="x", finestra=60, adesso=1000.0)
    await consuma_richiesta(r, "k", limite=2, piano="x", finestra=60, adesso=1010.0)
    with pytest.raises(QuotaSuperata) as e:
        await consuma_richiesta(r, "k", limite=2, piano="x", finestra=60, adesso=1020.0)
    assert e.value.codice == "QTA-001" and e.value.retry_after == 40
    # la più vecchia esce dalla finestra: di nuovo ammessa
    await consuma_richiesta(r, "k", limite=3, piano="x", finestra=60, adesso=1075.0)


async def test_quota_pdv_conta_e_blocca_fino_a_mezzanotte():
    r = _Redis()
    ora = datetime(2026, 10, 21, 21, 30, tzinfo=UTC)  # 23:30 a Roma
    for _ in range(2):
        await controlla_quota_pdv(r, T, limite=2, piano="starter", adesso=ora)
        await registra_pdv_versato(r, T, adesso=ora)
    with pytest.raises(QuotaSuperata) as e:
        await controlla_quota_pdv(r, T, limite=2, piano="starter", adesso=ora)
    assert e.value.codice == "QTA-002" and e.value.retry_after == 1800
    err = e.value.errore_api()
    assert isinstance(err, ErroreApi) and err.intestazioni()["Retry-After"] == "1800"
    # il giorno dopo (ora italiana) il contatore è un altro
    domani = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)
    assert chiave_pdv_giorno(T, domani, "Europe/Rome") != chiave_pdv_giorno(T, ora, "Europe/Rome")
    await controlla_quota_pdv(r, T, limite=2, piano="starter", adesso=domani)


def test_secondi_alla_mezzanotte_ora_legale():
    # 25/10/2026 è il giorno del cambio d'ora: 25 ore
    assert secondi_alla_mezzanotte(datetime(2026, 10, 24, 22, 0, tzinfo=UTC), "Europe/Rome") \
        == 25 * 3600


async def test_quote_redis_none_nessun_controllo():
    await consuma_richiesta(None, "k", limite=0, piano="x")
    await controlla_quota_pdv(None, T, limite=0, piano="x")
    assert await registra_pdv_versato(None, T) is None
