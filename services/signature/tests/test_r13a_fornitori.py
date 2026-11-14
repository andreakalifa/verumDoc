# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R13a — adattatori e registro dei fornitori di firma e marca.

PKCS#11 con SoftHSM2 (se installato), CSC v2 contro il simulatore (SAD
riusabile con SCAL 1, OTP, elenco delle credenziali, firma della persona),
ripiego fra fornitori e fra TSA, controllo d'avvio in produzione, gestore del
registro, segreti mai nei log, client HTTP (ritentativi, pinning), API della
console (permessi mai delegabili, limite delle prove).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.config import Settings
from app.services.csc_client import ClienteCSC, FornitoreFirmaCSC
from app.services.fornitori import ConfigurazioneNonDisponibile, ErroreFornitore
from app.services.registro_fornitori import (
    GestoreConfigurazione,
    costruisci_da_registro,
    problemi_avvio,
)
from app.services.tsa_client import ClienteTSA, FornitoreMarcaRFC3161, MarcaConRipiego

from libs.shared.firma_qualificata.configurazione import (
    ConfigurazioneFornitoreNonValida,
    FornitoreRegistrato,
    valida_configurazione,
)
from tools.simulatore_firma.app import Simulatore, configurazione_console, crea_app

from .test_r13a_simulatore_e2e import servizio_da

pytestmark = pytest.mark.asyncio
ADESSO = datetime.now(UTC)


def _csc(sim: Simulatore, **kw) -> FornitoreFirmaCSC:
    conf = configurazione_console(sim)["firma"]["configurazione"]
    cliente = ClienteCSC(conf["url_base"], conf["url_token"], sim.client_id, sim.client_secret,
                         trasporto=httpx.ASGITransport(app=crea_app(sim)))
    return FornitoreFirmaCSC(cliente, kw.pop("credential_id", sim.credenziale),
                             pin=kw.pop("pin", sim.pin), **kw)


# ── CSC v2 ───────────────────────────────────────────────────────────

async def test_csc_credenziale_da_elenco_e_firma():
    sim = Simulatore()
    f = _csc(sim, credential_id="")
    firma = await f.firma_impronta(hashlib.sha256(b"x").digest(), "sha256")
    assert firma and "credentials/list" in sim.chiamate


async def test_csc_piu_credenziali_senza_identificativo_errore():
    sim = Simulatore(credenziali_extra=["altra"])
    with pytest.raises(ErroreFornitore, match="indicare credential_id"):
        await _csc(sim, credential_id="").materiale()


async def test_csc_scal1_un_sad_per_piu_firme():
    """Sigillo automatico: con SCAL 1 e firme_per_autorizzazione > 1 si autorizza una
    volta per N firme (firma massiva senza interazione)."""
    sim = Simulatore(scal="1")
    f = _csc(sim, firme_per_autorizzazione=5)
    for i in range(4):
        await f.firma_impronta(hashlib.sha256(bytes([i])).digest(), "sha256")
    assert sim.chiamate.count("credentials/authorize") == 1
    assert sim.chiamate.count("signatures/signHash") == 4


async def test_csc_scal2_autorizza_ogni_firma():
    sim = Simulatore(scal="2")
    f = _csc(sim, firme_per_autorizzazione=5)
    for i in range(3):
        await f.firma_impronta(hashlib.sha256(bytes([i])).digest(), "sha256")
    assert sim.chiamate.count("credentials/authorize") == 3


async def test_csc_otp_online_senza_otp_nel_segreto_non_automatico():
    sim = Simulatore(otp_presenza="true", otp_tipo="online")
    with pytest.raises(ErroreFornitore, match="OTP"):
        await _csc(sim).firma_impronta(hashlib.sha256(b"x").digest(), "sha256")
    assert "credentials/authorize" not in sim.chiamate


async def test_csc_firma_persona_con_otp():
    sim = Simulatore(otp_presenza="true", otp_tipo="online")
    f = _csc(sim)
    await f.invia_otp()
    assert "credentials/sendOTP" in sim.chiamate
    with pytest.raises(ErroreFornitore):
        await f.firma_impronta_con_otp(hashlib.sha256(b"x").digest(), "sha256", otp="000000",
                                       pin=sim.pin)
    assert await f.firma_impronta_con_otp(hashlib.sha256(b"x").digest(), "sha256",
                                          otp=sim.otp, pin=sim.pin)


async def test_csc_pin_errato_rifiutato():
    sim = Simulatore()
    with pytest.raises(ErroreFornitore):
        await _csc(sim, pin="0000").firma_impronta(hashlib.sha256(b"x").digest(), "sha256")


async def test_csc_ecdsa_r_s_convertito():
    sim = Simulatore(chiave="ec")
    servizio = servizio_da(sim)
    esito = await servizio.firma(b"x", formato="cades", livello="B-T", adesso=ADESSO)
    assert esito.verificata


async def test_csc_segreti_mai_nel_repr():
    sim = Simulatore()
    f = _csc(sim, otp="99887766")
    assert sim.client_secret not in repr(f._cliente) and sim.pin not in repr(f)
    assert "99887766" not in repr(f)


# ── ritentativi, ripiego ─────────────────────────────────────────────

async def test_ritentativi_idempotenti_su_503():
    sim = Simulatore(guasti={"credentials/info": [503, 503]})
    f = _csc(sim)
    f._cliente._http._attesa_base = 0.0
    await f.materiale()
    assert sim.chiamate.count("credentials/info") == 3


async def test_autorizzazione_con_otp_non_si_ritenta():
    sim = Simulatore(otp_presenza="true", guasti={"credentials/authorize": [503]})
    f = _csc(sim, otp=sim.otp)
    f._cliente._http._attesa_base = 0.0
    with pytest.raises(ErroreFornitore):
        await f.firma_impronta(hashlib.sha256(b"x").digest(), "sha256")
    assert sim.chiamate.count("credentials/authorize") == 1


async def test_ripiego_sul_secondo_fornitore_di_firma():
    guasto, buono = Simulatore(guasti={"signatures/signHash": [503] * 10}), Simulatore()
    s1, s2 = servizio_da(guasto), servizio_da(buono)
    f1, f2 = s1.fornitori_firma[0], s2.fornitori_firma[0]
    f1._cliente._http._attesa_base = 0.0
    f2.codice = "secondo"
    s1.fornitori_firma = [f1, f2]
    s1.fornitore_firma = f1
    esito = await s1.firma(b"x", formato="cades", livello="B-T", adesso=ADESSO)
    assert esito.codice_fornitore == "secondo"


async def test_ripiego_tsa_se_la_risposta_non_supera_la_verifica():
    cattiva, buona = Simulatore(guasti={"tsa": ["impronta_errata"]}), Simulatore()

    def tsa(sim: Simulatore, codice: str) -> FornitoreMarcaRFC3161:
        from app.services.registro_fornitori import radici_da_pem

        radici = list(radici_da_pem(sim.pki.ca_pem))
        return FornitoreMarcaRFC3161(ClienteTSA(
            "http://simulatore.locale/tsa", radici=radici,
            trasporto=httpx.ASGITransport(app=crea_app(sim))), radici, codice=codice)

    marca = MarcaConRipiego([tsa(cattiva, "prima"), tsa(buona, "seconda")])
    token = await marca.marca(hashlib.sha256(b"x").digest(), "sha256")
    assert token and marca.ultimo.codice == "seconda"
    with pytest.raises(ErroreFornitore, match="nessuna TSA"):
        await MarcaConRipiego([tsa(Simulatore(guasti={"tsa": ["impronta_errata"]}), "u")]
                              ).marca(hashlib.sha256(b"x").digest(), "sha256")


async def test_tsa_istante_fuori_tolleranza_rifiutato():
    sim = Simulatore()
    cliente = ClienteTSA("http://simulatore.locale/tsa",
                         trasporto=httpx.ASGITransport(app=crea_app(sim)),
                         tolleranza_secondi=30,
                         orologio=lambda: datetime.now(UTC) + timedelta(hours=1))
    with pytest.raises(ErroreFornitore, match="tolleranza"):
        await cliente.async_timestamp(hashlib.sha256(b"x").digest(), "sha256")


async def test_tsa_certificato_diverso_da_quello_configurato():
    sim = Simulatore()
    cliente = ClienteTSA("http://simulatore.locale/tsa",
                         trasporto=httpx.ASGITransport(app=crea_app(sim)),
                         certificato_sha256="00" * 32)
    with pytest.raises(ErroreFornitore, match="certificato della TSA"):
        await cliente.async_timestamp(hashlib.sha256(b"x").digest(), "sha256")


# ── pinning ──────────────────────────────────────────────────────────

def test_pinning_fail_closed():
    from app.services.http_sicuro import controlla_pin, spki_sha256

    sim = Simulatore()
    der = sim.pki.der(sim.pki.ca)
    controlla_pin(der, [spki_sha256(der)], "csc")
    with pytest.raises(ErroreFornitore, match="pinning"):
        controlla_pin(der, ["00" * 32], "csc")
    with pytest.raises(ErroreFornitore, match="pinning"):
        controlla_pin(None, ["00" * 32], "csc")
    controlla_pin(None, [], "csc")  # senza pin: nessun controllo aggiuntivo


async def test_pinning_attivo_su_trasporto_senza_tls_rifiuta_la_risposta():
    sim = Simulatore()
    conf = configurazione_console(sim)["firma"]["configurazione"]
    cliente = ClienteCSC(conf["url_base"], conf["url_token"], sim.client_id, sim.client_secret,
                         trasporto=httpx.ASGITransport(app=crea_app(sim)),
                         pin_spki_sha256=("00" * 32,))
    with pytest.raises(ErroreFornitore, match="pinning"):
        await FornitoreFirmaCSC(cliente, sim.credenziale, pin=sim.pin).materiale()


# ── configurazione e produzione ──────────────────────────────────────

def test_configurazione_rifiuta_segreti_e_sviluppo_in_produzione():
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="segreti"):
        valida_configurazione("FIRMA", "CSC", {"url_base": "https://a", "url_token": "https://b",
                                               "client_id": "x", "client_secret": "y"},
                              produzione=False)
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="SVILUPPO"):
        valida_configurazione("FIRMA", "SVILUPPO", {}, produzione=True)
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="https"):
        valida_configurazione("FIRMA", "CSC", {"url_base": "http://a", "url_token": "https://b",
                                               "client_id": "x", "radici_pem": ""},
                              produzione=True)
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="radici"):
        valida_configurazione("MARCA", "RFC3161", {"url": "https://tsa"}, produzione=True)
    assert valida_configurazione("FIRMA", "SVILUPPO", {}, produzione=False) == {}


def test_fornitore_di_sviluppo_vietato_in_produzione():
    """Tre difese: la console non lo salva, il servizio non parte, il registro non
    lo costruisce."""
    sviluppo = FornitoreRegistrato("dev", "FIRMA", "SVILUPPO", "dev", 1, {})
    cfg = Settings(env="production", database_url="postgresql+asyncpg://x@h/d")
    assert any("SVILUPPO" in p for p in problemi_avvio(cfg, [sviluppo]))
    esito = costruisci_da_registro([sviluppo], cfg, adesso=ADESSO)
    assert esito.servizio.fornitori_firma == [] and "dev" in esito.problemi


def test_produzione_senza_database_non_parte():
    assert any("DATABASE_URL" in p for p in problemi_avvio(Settings(env="production"), []))
    assert problemi_avvio(Settings(env="test"), None) == []


def test_segreti_mancanti_o_json_non_valido():
    sim = Simulatore()
    conf = configurazione_console(sim)["firma"]["configurazione"]
    f = FornitoreRegistrato("csc-a", "FIRMA", "CSC", "a", 1, conf)
    esito = costruisci_da_registro([f], Settings(env="test"), adesso=ADESSO)
    assert "client_secret" in " ".join(esito.problemi["csc-a"])
    esito = costruisci_da_registro([f], Settings(env="test", firma_segreti_json="{non json"),
                                   adesso=ADESSO)
    assert "JSON non valido" in " ".join(esito.problemi["csc-a"])
    assert "{non json" not in json.dumps(esito.problemi)


def test_pkcs11_libreria_non_ammessa():
    f = FornitoreRegistrato("hsm", "FIRMA", "PKCS11", "hsm", 1,
                            {"libreria": "/opt/qualunque.so", "token_label": "t",
                             "chiave_label": "k"})
    esito = costruisci_da_registro([f], Settings(env="test",
                                                 firma_segreti_json='{"hsm": {"pin": "1"}}'),
                                   adesso=ADESSO)
    assert "ammesse" in " ".join(esito.problemi["hsm"])


# ── gestore del registro ─────────────────────────────────────────────

async def test_gestore_ricostruisce_tiene_l_ultima_e_fallisce_chiuso():
    righe: list[FornitoreRegistrato] = []
    stato = {"errore": False}

    async def lettore():
        if stato["errore"]:
            raise OSError("db giù")
        return list(righe)

    cfg = Settings(env="test", database_url="postgresql+asyncpg://x@h/d",
                   firma_configurazione_cache_secondi=0.0)
    g = GestoreConfigurazione(cfg, lettore=lettore)
    s1 = await g.servizio()
    assert s1.configurazione["origine"] == "ambiente"
    righe.append(FornitoreRegistrato("dev", "FIRMA", "SVILUPPO", "dev", 1, {}))
    s2 = await g.servizio()
    assert s2 is not s1 and s2.fornitori_firma[0].codice == "dev"
    stato["errore"] = True
    assert await g.servizio() is s2  # ultima configurazione buona
    nuovo = GestoreConfigurazione(cfg, lettore=lettore)
    with pytest.raises(ConfigurazioneNonDisponibile):
        await nuovo.servizio()


# ── PKCS#11 con SoftHSM2 ─────────────────────────────────────────────

SOFTHSM_LIB = "/usr/lib/softhsm/libsofthsm2.so"


def _uso(*, ca: bool):
    from cryptography import x509 as cx

    return cx.KeyUsage(digital_signature=not ca, content_commitment=not ca,
                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                       key_cert_sign=ca, crl_sign=ca, encipher_only=False, decipher_only=False)


@pytest.fixture
def softhsm(tmp_path, monkeypatch):
    if not (shutil.which("softhsm2-util") and os.path.exists(SOFTHSM_LIB)):
        pytest.skip("SoftHSM2 non installato (apt install softhsm2)")
    pytest.importorskip("pkcs11")
    (tmp_path / "token").mkdir()
    conf = tmp_path / "softhsm2.conf"
    conf.write_text(f"directories.tokendir = {tmp_path / 'token'}\nobjectstore.backend = file\n")
    monkeypatch.setenv("SOFTHSM2_CONF", str(conf))
    subprocess.run(["softhsm2-util", "--init-token", "--free", "--label", "vd-prova",  # noqa: S607
                    "--pin", "1234", "--so-pin", "5678"], check=True, capture_output=True)
    import pkcs11
    from asn1crypto import keys as akeys
    from cryptography import x509 as cx
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from pkcs11.util.rsa import encode_rsa_public_key

    token = pkcs11.lib(SOFTHSM_LIB).get_token(token_label="vd-prova")  # noqa: S106
    with token.open(rw=True, user_pin="1234") as sessione:
        pubblica, _ = sessione.generate_keypair(pkcs11.KeyType.RSA, 2048, label="sigillo",
                                                store=True)
        der_pub = encode_rsa_public_key(pubblica)
    chiave_pub = serialization.load_der_public_key(akeys.PublicKeyInfo({
        "algorithm": {"algorithm": "rsa"}, "public_key": akeys.RSAPublicKey.load(der_pub)
    }).dump())
    ca_k = rsa.generate_private_key(65537, 2048)
    nome = cx.Name([cx.NameAttribute(NameOID.COMMON_NAME, "CA HSM di prova")])
    adesso = datetime.now(UTC)
    ca = (cx.CertificateBuilder().subject_name(nome).issuer_name(nome)
          .public_key(ca_k.public_key()).serial_number(1)
          .not_valid_before(adesso - timedelta(days=1)).not_valid_after(adesso + timedelta(days=30))
          .add_extension(cx.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(_uso(ca=True), critical=True)
          .sign(ca_k, hashes.SHA256()))
    cert = (cx.CertificateBuilder()
            .subject_name(cx.Name([cx.NameAttribute(NameOID.COMMON_NAME, "Sigillo HSM")]))
            .issuer_name(nome).public_key(chiave_pub).serial_number(2)
            .not_valid_before(adesso - timedelta(days=1))
            .not_valid_after(adesso + timedelta(days=30))
            .add_extension(cx.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_uso(ca=False), critical=True)
            .sign(ca_k, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    return {"certificato_pem": cert.public_bytes(pem).decode(),
            "radici_pem": ca.public_bytes(pem).decode()}


async def test_pkcs11_softhsm_firma_cades_verificata(softhsm):
    f = FornitoreRegistrato("hsm", "FIRMA", "PKCS11", "HSM", 1, {
        "libreria": SOFTHSM_LIB, "token_label": "vd-prova", "chiave_label": "sigillo",
        **softhsm})
    cfg = Settings(env="test", firma_segreti_json='{"hsm": {"pin": "1234"}}',
                   pkcs11_librerie_ammesse=SOFTHSM_LIB)
    esito = costruisci_da_registro([f], cfg, adesso=ADESSO)
    assert esito.problemi == {}
    firma = await esito.servizio.firma(b"<IPdA/>", formato="cades", livello="B-B",
                                       adesso=ADESSO)
    assert firma.verificata and firma.codice_fornitore == "hsm"
    # PIN errato: errore del fornitore, nessuna firma
    cfg_errato = Settings(env="test", firma_segreti_json='{"hsm": {"pin": "0000"}}',
                          pkcs11_librerie_ammesse=SOFTHSM_LIB)
    servizio = costruisci_da_registro([f], cfg_errato, adesso=ADESSO).servizio
    with pytest.raises(ErroreFornitore):
        await servizio.firma(b"x", formato="cades", livello="B-B", adesso=ADESSO)
    # revisione R13a (4): certificato del token diverso da quello atteso
    from app.services.fornitori import FirmaProdottaNonValida

    f_atteso = FornitoreRegistrato("hsm", "FIRMA", "PKCS11", "HSM", 1, {
        **f.configurazione, "certificati_attesi_sha256": ["00" * 32]})
    altro = costruisci_da_registro([f_atteso], cfg, adesso=ADESSO).servizio
    with pytest.raises(FirmaProdottaNonValida, match="non è fra quelli attesi"):
        await altro.fornitori_firma[0].materiale()


# ── API della console ────────────────────────────────────────────────

def _token(ruolo: str, **extra) -> str:
    from app.auth.jwt_handler import create_access_token

    return create_access_token(tenant_id="t-001", user_id="u-001", ruolo=ruolo, **extra)


@pytest.fixture
def client_con_simulatore(monkeypatch):
    from app.main import app
    from app.routers import signature as rotte
    from fastapi.testclient import TestClient

    sim = Simulatore()
    servizio = servizio_da(sim)

    class GestoreFinto:
        ultima_lettura = None
        ultimo_errore = None

        async def esito(self):
            from app.services.registro_fornitori import EsitoCostruzione

            return EsitoCostruzione(servizio, {}, [
                FornitoreRegistrato("simulatore-csc", "FIRMA", "CSC", "S", 1, {}),
                FornitoreRegistrato("simulatore-tsa", "MARCA", "RFC3161", "T", 1, {})])

        async def servizio(self):
            return servizio

    rotte.imposta_gestore(GestoreFinto())  # type: ignore[arg-type]
    rotte.reimposta_servizio(servizio)
    from app.services import limiti

    limiti.azzera_locali()  # R16: contatore del processo senza REDIS_URL (test)
    yield TestClient(app)
    rotte.reimposta_servizio(None)
    limiti.azzera_locali()


PERSONALIZZATO = {"rp": "r1", "permessi": ["firma_fornitori:gestisci"]}


@pytest.mark.parametrize("ruolo,extra", [("ADMIN_TENANT", {}), ("OPERATORE", {}),
                                          ("OPERATORE", PERSONALIZZATO),
                                          # permesso mai delegabile: mai con un ruolo
                                          # personalizzato, nemmeno su un ruolo del conservatore
                                          ("RSC_CONSERVATORE", PERSONALIZZATO)])
def test_fornitori_solo_conservatore(client_con_simulatore, ruolo, extra):
    try:
        token = _token(ruolo, **extra)
    except TypeError:
        pytest.skip("create_access_token senza claim aggiuntivi")
    r = client_con_simulatore.get("/api/v1/signature/fornitori",
                                  headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and r.json()["codice"] == "FIRMA-403-FORNITORI"


def test_fornitori_token_di_servizio_rifiutato(client_con_simulatore, token_servizio):
    r = client_con_simulatore.get("/api/v1/signature/fornitori",
                                  headers={"Authorization": f"Bearer {token_servizio}"})
    assert r.status_code in (401, 403)


def test_stato_e_prova_dei_fornitori(client_con_simulatore, monkeypatch):
    from app.config import settings

    intestazioni = {"Authorization": f"Bearer {_token('RSC_CONSERVATORE')}"}
    r = client_con_simulatore.get("/api/v1/signature/fornitori", headers=intestazioni)
    assert r.status_code == 200, r.text
    voci = {v["codice"]: v for v in r.json()["fornitori"]}
    cert = voci["simulatore-csc"]["certificato"]
    assert cert["raggiungibile"] and cert["giorni_alla_scadenza"] > 300
    assert cert["dichiara_qualificato"]
    p = client_con_simulatore.post("/api/v1/signature/fornitori/simulatore-csc/prova",
                                   headers=intestazioni)
    assert p.status_code == 200, p.text
    assert p.json()["verificata"] and p.json()["livello"] == "B-T"
    m = client_con_simulatore.post("/api/v1/signature/fornitori/simulatore-tsa/prova",
                                   headers=intestazioni)
    assert m.status_code == 200 and m.json()["verificata"]
    assert client_con_simulatore.post("/api/v1/signature/fornitori/ignoto/prova",
                                      headers=intestazioni).status_code == 404
    monkeypatch.setattr(settings, "firma_prove_per_ora", 3)
    r = client_con_simulatore.post("/api/v1/signature/fornitori/simulatore-csc/prova",
                                   headers=intestazioni)
    assert r.status_code == 429 and r.json()["codice"] == "FIRMA-429-PROVA"


async def test_avvio_in_produzione_rifiutato_con_fornitore_di_sviluppo(monkeypatch):
    """Controllo d'avvio: in produzione il servizio non parte con un fornitore di
    SVILUPPO attivo nel registro, né senza database, né con il registro illeggibile."""
    from app import main
    from app.config import settings

    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "database_url", "")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await main.controlla_registro()

    async def init_finto(*a, **k):
        return None

    async def registro_con_sviluppo():
        return [FornitoreRegistrato("dev", "FIRMA", "SVILUPPO", "dev", 1, {})]

    async def registro_illeggibile():
        raise OSError("db giù")

    import libs.shared.db as db

    monkeypatch.setattr(db, "init_db", init_finto)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://vd_firma@h/d")
    monkeypatch.setattr(main, "leggi_registro", registro_con_sviluppo)
    with pytest.raises(RuntimeError, match="SVILUPPO"):
        await main.controlla_registro()
    monkeypatch.setattr(main, "leggi_registro", registro_illeggibile)
    with pytest.raises(RuntimeError, match="non leggibile"):
        await main.controlla_registro()
    monkeypatch.setattr(settings, "env", "test")
    await main.controlla_registro()  # fuori produzione: solo log


async def test_segreti_mai_nei_log(capsys):
    """Né il segreto del cliente né PIN e OTP compaiono nei log, anche negli errori."""
    sim = Simulatore(otp_presenza="true", otp="77665544", pin="98761234",
                     client_secret="segreto-da-non-loggare")  # noqa: S106
    servizio = servizio_da(sim)
    f = servizio.fornitori_firma[0]
    f._otp = sim.otp
    await servizio.firma(b"x", formato="cades", livello="B-T", adesso=ADESSO)
    f._pin = "00000000"  # PIN errato: il rifiuto si logga senza il valore
    with pytest.raises(ErroreFornitore):
        await servizio.firma(b"y", formato="cades", livello="B-T", adesso=ADESSO)
    uscita = capsys.readouterr()
    testo = uscita.out + uscita.err
    for segreto in ("segreto-da-non-loggare", "98761234", "77665544", "00000000"):
        assert segreto not in testo
