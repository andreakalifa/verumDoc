# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R13b (VD-C-078) — libreria dei canali PEC: configurazione senza segreti, segreti
legati agli host, verifica S/MIME delle ricevute e associazione al messaggio.

Le ricevute sono prodotte dal gestore di prova (``tools.simulatore_pec.gestore``):
certificati generati a runtime (REGOLE §31 R08 (4)), mai chiavi nel repository.
"""
from __future__ import annotations

import base64
import json

import pytest

from libs.shared.firma_qualificata.configurazione import ConfigurazioneFornitoreNonValida
from libs.shared.pec.configurazione import (
    controlla_deploy,
    leggi_segreti,
    valida_configurazione,
    vincoli_deploy,
)
from libs.shared.pec.ricevute import (
    ACCETTAZIONE,
    AVVENUTA_CONSEGNA,
    leggi_ricevuta,
    motivo_non_associata,
)
from libs.shared.pec.smime import RicevutaNonAutentica, radici_da_pem, separa_multipart_signed
from tools.simulatore_pec.gestore import Pki, messaggio_firmato, ricevuta

MITTENTE = "conservazione@pec.conservatore.it"
DEST = "protocollo@pec.ente.it"
MSGID = "<pec.abc.123@pec.conservatore.it>"
BASE = {"indirizzo": MITTENTE, "smtp_host": "smtps.pec.gestore.it",
        "imap_host": "imaps.pec.gestore.it"}


@pytest.fixture(scope="module")
def pki() -> Pki:
    return Pki()


def _consegna(pki: Pki | None, **kw) -> bytes:
    argomenti = {"msgid": MSGID, "mittente": MITTENTE, "destinatari": [DEST],
                 "oggetto": "Prova", "consegna": DEST} | kw
    return ricevuta(pki, "AVVENUTA_CONSEGNA", **argomenti)


# ── configurazione ───────────────────────────────────────────────────

def test_configurazione_normalizzata_e_predefiniti() -> None:
    conf = valida_configurazione("SMTP_IMAP", BASE, produzione=False)
    assert conf["smtp_porta"] == 465 and conf["smtp_sicurezza"] == "tls"
    assert conf["imap_porta"] == 993 and conf["imap_sicurezza"] == "tls"
    assert conf["cartella"] == "INBOX" and conf["tipo_ricevuta"] == "completa"


@pytest.mark.parametrize("modifica, motivo", [
    ({"password": "x"}, "segreti"),
    ({"smtp_pin": "1"}, "segreti"),
    ({"indirizzo": "non-un-indirizzo"}, "indirizzo"),
    ({"smtp_host": "https://smtps.pec.gestore.it"}, "smtp_host"),
    ({"smtp_sicurezza": "ssl"}, "smtp_sicurezza"),
    ({"tipo_ricevuta": "lunga"}, "tipo_ricevuta"),
    ({"cartella": "INBOX\r\nA1 LOGOUT"}, "cartella"),
])
def test_configurazione_rifiuti(modifica, motivo) -> None:
    with pytest.raises(ConfigurazioneFornitoreNonValida, match=motivo):
        valida_configurazione("SMTP_IMAP", BASE | modifica, produzione=False)


def test_produzione_tls_radici_e_niente_sviluppo(pki: Pki) -> None:
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="radici_pem"):
        valida_configurazione("SMTP_IMAP", BASE, produzione=True)
    con_radici = BASE | {"radici_pem": pki.radice_pem}
    assert valida_configurazione("SMTP_IMAP", con_radici, produzione=True)["radici_pem"]
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="cifrati"):
        valida_configurazione("SMTP_IMAP", con_radici | {"imap_sicurezza": "nessuna"},
                              produzione=True)
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="SVILUPPO"):
        valida_configurazione("SVILUPPO", con_radici, produzione=True)


def test_segreti_legati_agli_host() -> None:
    segreti_json = json.dumps({"pec-1": {"host": ["smtps.pec.gestore.it",
                                                  "imaps.pec.gestore.it"],
                                         "password": "s3greta", "utente": MITTENTE}})
    segreti = leggi_segreti(segreti_json, "pec-1")
    vincoli = vincoli_deploy(segreti_json, "pec-1")
    assert segreti == {"password": "s3greta", "utente": MITTENTE}  # host non è un segreto
    conf = valida_configurazione("SMTP_IMAP", BASE, produzione=False)
    assert controlla_deploy("SMTP_IMAP", conf, segreti, vincoli, produzione=True) == []
    # chi cambia l'host in console non porta fuori la password
    altrove = conf | {"imap_host": "imap.attaccante.example.com"}
    motivi = controlla_deploy("SMTP_IMAP", altrove, segreti, vincoli, produzione=True)
    assert any("imap.attaccante.example.com" in m for m in motivi)
    # voce senza host: mai costruita
    senza = vincoli_deploy(json.dumps({"pec-1": {"password": "x"}}), "pec-1")
    assert any("host" in m for m in controlla_deploy("SMTP_IMAP", conf, segreti, senza,
                                                     produzione=False))
    with pytest.raises(ValueError, match="PEC_SEGRETI_JSON"):
        leggi_segreti("{non json", "pec-1")


# ── ricevute autentiche ──────────────────────────────────────────────

def test_ricevuta_di_consegna_autentica_e_associata(pki: Pki) -> None:
    ric = leggi_ricevuta(_consegna(pki), radici_da_pem(pki.radice_pem))
    assert ric.tipo == AVVENUTA_CONSEGNA and ric.msgid == MSGID and ric.consegna == DEST
    assert ric.mittente == MITTENTE and ric.data is not None and ric.firma.certificato_sha256
    assert ric.tipo_ricevuta == "completa"
    assert motivo_non_associata(ric, message_id=MSGID, mittente=MITTENTE,
                                destinatario=DEST) is None


def test_accettazione_associata_per_destinatario(pki: Pki) -> None:
    grezzo = ricevuta(pki, "ACCETTAZIONE", msgid=MSGID, mittente=MITTENTE,
                      destinatari=[DEST], oggetto="Prova")
    ric = leggi_ricevuta(grezzo, radici_da_pem(pki.radice_pem))
    assert ric.tipo == ACCETTAZIONE
    assert motivo_non_associata(ric, message_id=MSGID, mittente=MITTENTE,
                                destinatario=DEST) is None
    assert motivo_non_associata(ric, message_id=MSGID, mittente=MITTENTE,
                                destinatario="altro@pec.ente.it") is not None


@pytest.mark.parametrize("kw, motivo", [
    ({"manomessa": True}, "impronta"),
    ({"x_ricevuta": "accettazione"}, "X-Ricevuta"),
    ({"x_riferimento": "<altro@pec.conservatore.it>"}, "X-Riferimento"),
])
def test_ricevute_manomesse_rifiutate(pki: Pki, kw, motivo) -> None:
    with pytest.raises(RicevutaNonAutentica, match=motivo):
        leggi_ricevuta(_consegna(pki, **kw), radici_da_pem(pki.radice_pem))


def test_ricevuta_di_un_gestore_fuori_dalle_radici_o_non_firmata(pki: Pki) -> None:
    estraneo = Pki(dominio="pec.estraneo.test")
    with pytest.raises(RicevutaNonAutentica, match="radici"):
        leggi_ricevuta(_consegna(estraneo), radici_da_pem(pki.radice_pem))
    with pytest.raises(RicevutaNonAutentica, match="non firmata"):
        leggi_ricevuta(_consegna(None), radici_da_pem(pki.radice_pem))
    with pytest.raises(RicevutaNonAutentica, match="radice"):
        leggi_ricevuta(_consegna(pki), [])


def test_certificato_del_gestore_scaduto(pki: Pki) -> None:
    scaduto = Pki(scaduto=True)
    with pytest.raises(RicevutaNonAutentica, match="validità"):
        leggi_ricevuta(_consegna(scaduto), radici_da_pem(scaduto.radice_pem))


def test_daticert_fuori_dalla_parte_firmata_non_conta(pki: Pki) -> None:
    """Un attaccante con una ricevuta autentica per un ALTRO messaggio aggiunge un
    secondo daticert.xml nell'area non firmata: si legge solo quello firmato."""
    autentica = _consegna(pki, msgid="<altro.1@pec.conservatore.it>")
    falso = ricevuta(None, "AVVENUTA_CONSEGNA", msgid=MSGID, mittente=MITTENTE,
                     destinatari=[DEST], oggetto="x", consegna=DEST)
    # il falso diventa preambolo del multipart firmato: fuori dalle due parti
    testa, _, corpo = autentica.partition(b"\r\n\r\n")
    manipolato = testa + b"\r\n\r\n" + falso.split(b"\r\n\r\n", 1)[1] + b"\r\n" + corpo
    ric = leggi_ricevuta(manipolato, radici_da_pem(pki.radice_pem))
    assert ric.msgid == "<altro.1@pec.conservatore.it>"
    assert motivo_non_associata(ric, message_id=MSGID, mittente=MITTENTE,
                                destinatario=DEST) is not None


@pytest.mark.parametrize("campo, valore", [
    ("message_id", "<pec.diverso@pec.conservatore.it>"),
    ("mittente", "altra.casella@pec.conservatore.it"),
    ("destinatario", "altro@pec.ente.it"),
])
def test_ricevuta_autentica_di_un_altro_messaggio_non_associata(pki: Pki, campo,
                                                               valore) -> None:
    ric = leggi_ricevuta(_consegna(pki), radici_da_pem(pki.radice_pem))
    attesi = {"message_id": MSGID, "mittente": MITTENTE, "destinatario": DEST} | {campo: valore}
    assert motivo_non_associata(ric, **attesi) is not None


def test_multipart_signed_con_tre_parti_rifiutato(pki: Pki) -> None:
    grezzo = _consegna(pki)
    confine = grezzo.split(b'boundary="', 1)[1].split(b'"', 1)[0]
    extra = b"\r\n--" + confine + b"\r\nContent-Type: text/plain\r\n\r\naltro"
    chiusura = b"\r\n--" + confine + b"--"
    tre = grezzo.replace(chiusura, extra + chiusura)
    with pytest.raises(RicevutaNonAutentica, match="parti"):
        separa_multipart_signed(tre)


def test_firma_cms_con_contenuto_incapsulato_rifiutata(pki: Pki) -> None:
    """Una firma «attached» non lega i byte della prima parte: rifiutata."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs7

    grezzo = _consegna(pki)
    parti = separa_multipart_signed(grezzo)
    incapsulata = (pkcs7.PKCS7SignatureBuilder().set_data(parti.contenuto)
                   .add_signer(pki.certificato, pki.chiave, hashes.SHA256())
                   .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary]))
    vecchia = base64.encodebytes(parti.firma_der).replace(b"\n", b"\r\n")
    nuova = base64.encodebytes(incapsulata).replace(b"\n", b"\r\n")
    assert vecchia in grezzo
    with pytest.raises(RicevutaNonAutentica, match="incapsulato"):
        leggi_ricevuta(grezzo.replace(vecchia, nuova), radici_da_pem(pki.radice_pem))


def test_messaggio_firmato_senza_daticert(pki: Pki) -> None:
    grezzo = messaggio_firmato(pki, intestazioni={"X-Ricevuta": "accettazione"},
                               testo="niente", xml=b"<altro/>")
    grezzo = grezzo.replace(b'filename="daticert.xml"', b'filename="altro.xml"')
    with pytest.raises(RicevutaNonAutentica):
        leggi_ricevuta(grezzo, radici_da_pem(pki.radice_pem))


def test_firma_cms_non_corrispondente_alla_chiave_rifiutata(pki: Pki) -> None:
    """Attributi firmati intatti (impronta giusta) ma firma prodotta da un'altra chiave."""
    from asn1crypto import cms
    from cryptography.hazmat.primitives import hashes as _h
    from cryptography.hazmat.primitives.asymmetric import ec as _ec

    from libs.shared.pec.smime import verifica_firma
    contenuto = b"contenuto firmato\r\n"
    info = cms.ContentInfo.load(pki.firma_staccata(contenuto))
    si = info["content"]["signer_infos"][0]
    attributi = si["signed_attrs"].dump(force=True)
    attributi = b"\x31" + attributi[1:]
    altra = _ec.generate_private_key(_ec.SECP256R1())
    si["signature"] = altra.sign(attributi, _ec.ECDSA(_h.SHA256()))
    firma = info.dump(force=True)
    radici = radici_da_pem(pki.radice_pem)
    verifica_firma(contenuto, pki.firma_staccata(contenuto), radici)  # quella vera passa
    with pytest.raises(RicevutaNonAutentica, match="firma"):
        verifica_firma(contenuto, firma, radici)


# ── revisione R13b, rilievo 1: busta di anomalia e intestazioni obbligatorie ────────
def _busta_anomalia(pki: Pki, *, x_trasporto: str | None = "errore",
                    x_ricevuta: str | None = None) -> bytes:
    """Busta firmata DAL GESTORE che allega (message/rfc822) il messaggio di un terzo
    con un proprio daticert.xml «avvenuta-consegna» falso (riproduzione del rilievo)."""
    import secrets as _s

    from tools.simulatore_pec.gestore import daticert
    falso = daticert("AVVENUTA_CONSEGNA", msgid=MSGID, mittente=MITTENTE,
                     destinatari=[DEST], oggetto="x", gestore="pec.ente.it", consegna=DEST)
    b = "att-" + _s.token_hex(4)
    originale = ("From: attaccante@example.invalid\r\nTo: " + MITTENTE + "\r\nSubject: ciao\r\n"
                 "MIME-Version: 1.0\r\n"
                 f"Content-Type: multipart/mixed; boundary=\"{b}\"\r\n\r\n--{b}\r\n"
                 "Content-Type: text/plain\r\n\r\nciao\r\n"
                 f"--{b}\r\nContent-Type: application/xml; name=\"daticert.xml\"\r\n"
                 "Content-Disposition: attachment; filename=\"daticert.xml\"\r\n"
                 "Content-Transfer-Encoding: base64\r\n\r\n"
                 + base64.encodebytes(falso).decode().replace("\n", "\r\n") + f"--{b}--\r\n")
    c = "env-" + _s.token_hex(4)
    interna = (f"Content-Type: multipart/mixed; boundary=\"{c}\"\r\n\r\n--{c}\r\n"
               "Content-Type: text/plain\r\n\r\nAnomalia nel messaggio\r\n"
               f"--{c}\r\nContent-Type: message/rfc822\r\n"
               "Content-Disposition: attachment; filename=\"postacert.eml\"\r\n\r\n"
               + originale + f"\r\n--{c}--\r\n").encode()
    firma = base64.encodebytes(pki.firma_staccata(interna)).decode().replace("\n", "\r\n")
    s = "sig-" + _s.token_hex(4)
    testa = (f"From: posta-certificata@{pki.dominio}\r\nTo: {MITTENTE}\r\n"
             "Subject: ANOMALIA MESSAGGIO: ciao\r\n")
    if x_trasporto is not None:
        testa += f"X-Trasporto: {x_trasporto}\r\n"
    if x_ricevuta is not None:
        testa += f"X-Ricevuta: {x_ricevuta}\r\nX-Riferimento-Message-ID: {MSGID}\r\n"
    return ((testa + "MIME-Version: 1.0\r\nContent-Type: multipart/signed; "
             "protocol=\"application/pkcs7-signature\"; micalg=sha-256; "
             f"boundary=\"{s}\"\r\n\r\n--{s}\r\n").encode() + interna
            + (f"\r\n--{s}\r\nContent-Type: application/pkcs7-signature; name=smime.p7s\r\n"
               "Content-Transfer-Encoding: base64\r\n\r\n" + firma + f"\r\n--{s}--\r\n").encode())


@pytest.mark.parametrize("x_trasporto, x_ricevuta, motivo", [
    ("errore", None, "anomalia"),
    # anche senza X-Trasporto o con X-Ricevuta coerente: il daticert annidato non conta
    (None, None, "parte diretta"),
    (None, "avvenuta-consegna", "parte diretta"),
])
def test_busta_di_anomalia_con_daticert_annidato_rifiutata(pki: Pki, x_trasporto,
                                                          x_ricevuta, motivo) -> None:
    with pytest.raises(RicevutaNonAutentica, match=motivo):
        leggi_ricevuta(_busta_anomalia(pki, x_trasporto=x_trasporto, x_ricevuta=x_ricevuta),
                       radici_da_pem(pki.radice_pem))


@pytest.mark.parametrize("togli", [b"X-Ricevuta", b"X-Riferimento-Message-ID"])
def test_ricevuta_senza_intestazioni_obbligatorie_rifiutata(pki: Pki, togli) -> None:
    grezzo = _consegna(pki).replace(togli + b":", b"X-Altro-" + togli[2:] + b":", 1)
    with pytest.raises(RicevutaNonAutentica, match="assente"):
        leggi_ricevuta(grezzo, radici_da_pem(pki.radice_pem))


def test_ricevuta_valida_marcata_come_anomalia_rifiutata(pki: Pki) -> None:
    grezzo = _consegna(pki).replace(b"X-Ricevuta:", b"X-Trasporto: errore\r\nX-Ricevuta:", 1)
    with pytest.raises(RicevutaNonAutentica, match="anomalia"):
        leggi_ricevuta(grezzo, radici_da_pem(pki.radice_pem))


def test_ca_dei_server_solo_dal_secret(pki: Pki) -> None:
    """Revisione rilievo 3: la console non indica la CA dei server SMTP/IMAP."""
    from libs.shared.pec.configurazione import controlla_deploy
    with pytest.raises(ConfigurazioneFornitoreNonValida, match="ca_pem"):
        valida_configurazione("SMTP_IMAP", BASE | {"ca_pem": pki.radice_pem}, produzione=False)
    assert controlla_deploy("SMTP_IMAP", BASE, {"ca_pem": "non pem"}, {"host": ()},
                            produzione=False)
    # la sola CA nel Secret non è una credenziale: nessun obbligo di «host»
    assert controlla_deploy("SMTP_IMAP", BASE, {"ca_pem": pki.radice_pem}, {"host": ()},
                            produzione=False) == []
