# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prove reali di tools/dev/genera_segreti_dev.py in una cartella temporanea
(i veri file infra/*.env non vengono mai letti né toccati).
"""
from __future__ import annotations

import base64
import importlib.util
import re
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

_SPEC = importlib.util.spec_from_file_location(
    "genera_segreti_dev", Path(__file__).resolve().parent / "genera_segreti_dev.py")
assert _SPEC and _SPEC.loader
modulo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(modulo)


def _valori(file: Path) -> dict[str, str]:
    testo = file.read_text(encoding="utf-8")
    righe = [r for r in testo.splitlines() if r and not r.startswith("#")]
    return dict(r.split("=", 1) for r in righe)


def _carica_pem(valore: str):
    from cryptography.hazmat.primitives import serialization
    pem = valore.replace("\\n", "\n").encode()
    if "PRIVATE" in valore:
        return serialization.load_pem_private_key(pem, password=None)
    return serialization.load_pem_public_key(pem)


def test_crea_tutti_i_file_nel_formato_atteso(tmp_path: Path) -> None:
    modulo.genera(tmp_path)
    jwt = _valori(tmp_path / "infra/dev_jwt.env")
    assert set(jwt) == {"JWT_PRIVATE_KEY", "JWT_PUBLIC_KEY", "API_KEY_PEPPER"}
    assert re.fullmatch(r"[0-9a-f]{64}", jwt["API_KEY_PEPPER"])
    assert _carica_pem(jwt["JWT_PRIVATE_KEY"]).key_size == 2048
    assert _carica_pem(jwt["JWT_PRIVATE_KEY"]).public_key().public_numbers() == \
        _carica_pem(jwt["JWT_PUBLIC_KEY"]).public_numbers()

    priv = _valori(tmp_path / "infra/dev_sftp.env")["SFTP_TOKEN_CHIAVE_PRIVATA"]
    pub = _valori(tmp_path / "infra/dev_canali_pub.env")["SFTP_TOKEN_CHIAVE_PUBBLICA"]
    assert _carica_pem(priv).key_size == 3072
    assert _carica_pem(priv).public_key().public_numbers() == _carica_pem(pub).public_numbers()

    wh = _valori(tmp_path / "infra/dev_webhook.env")["WEBHOOK_CHIAVE_CIFRATURA"]
    assert len(base64.b64decode(wh)) == 32
    for nome in ("dev_jwt.env", "dev_sftp.env", "dev_canali_pub.env", "dev_webhook.env"):
        grezzo = (tmp_path / "infra" / nome).read_bytes()
        assert b"\r" not in grezzo and not grezzo.startswith(b"\xef\xbb\xbf")


def test_non_sovrascrive_e_aggiunge_solo_il_pepper(tmp_path: Path) -> None:
    (tmp_path / "infra").mkdir()
    vecchio = "JWT_PRIVATE_KEY=x\nJWT_PUBLIC_KEY=y"
    (tmp_path / "infra/dev_jwt.env").write_text(vecchio, encoding="utf-8")
    modulo.genera(tmp_path)
    valori = _valori(tmp_path / "infra/dev_jwt.env")
    assert valori["JWT_PRIVATE_KEY"] == "x" and valori["JWT_PUBLIC_KEY"] == "y"
    assert "API_KEY_PEPPER" in valori

    prima = {p.name: p.read_bytes() for p in (tmp_path / "infra").iterdir()}
    modulo.genera(tmp_path)
    assert {p.name: p.read_bytes() for p in (tmp_path / "infra").iterdir()} == prima


def test_forza_rigenera(tmp_path: Path) -> None:
    modulo.genera(tmp_path)
    prima = (tmp_path / "infra/dev_webhook.env").read_text()
    modulo.genera(tmp_path, forza=True)
    assert (tmp_path / "infra/dev_webhook.env").read_text() != prima


def test_nessun_valore_segreto_nei_messaggi(tmp_path: Path) -> None:
    esiti = "\n".join(modulo.genera(tmp_path))
    pepper = _valori(tmp_path / "infra/dev_jwt.env")["API_KEY_PEPPER"]
    assert pepper not in esiti and "BEGIN" not in esiti


def test_chiavi_della_catena_coerenti_e_interoperabili(tmp_path: Path) -> None:
    """R04 (K2/K3): chiave del registro e del testimone nel formato delle note
    firmate, ciascuna verificabile con la chiave pubblica data all'altro servizio;
    il testimone conosce solo l'impronta del token di servizio del registro."""
    import hashlib
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from libs.catena import nota

    modulo.genera(tmp_path)
    catena = _valori(tmp_path / "infra/dev_catena.env")
    testimone = _valori(tmp_path / "infra/dev_testimone.env")
    registro = nota.analizza_firmatario(catena["CATENA_CHIAVE_PRIVATA"],
                                        nome_atteso="verumdoc-registro")
    tes = nota.analizza_firmatario(testimone["TESTIMONE_CHIAVE_PRIVATA"],
                                   nome_atteso="verumdoc-testimone")
    assert registro.verificatore.testo == testimone["TESTIMONE_CHIAVI_REGISTRO"]
    assert tes.verificatore.testo == catena["TESTIMONE_VERIFICATORE"]
    assert hashlib.sha256(catena["TESTIMONE_SERVICE_TOKEN"].encode()).hexdigest() == \
        testimone["TESTIMONE_IMPRONTE_TOKEN_REGISTRO"]
    corpo = nota.formatta(nota.Checkpoint("o", 1, b"\x00" * 32))
    firmata = nota.componi(corpo, [registro.firma(corpo), tes.firma(corpo)])
    assert len(nota.verifica(firmata, [testimone["TESTIMONE_CHIAVI_REGISTRO"],
                                       catena["TESTIMONE_VERIFICATORE"]])) == 2
    esiti = "\n".join(modulo.genera(tmp_path, forza=True))
    assert catena["CATENA_CHIAVE_PRIVATA"].split("+", 4)[-1] not in esiti


def test_catena_e_testimone_rigenerati_insieme(tmp_path: Path) -> None:
    """Se manca uno dei due file si rigenerano entrambi (chiavi incrociate)."""
    modulo.genera(tmp_path)
    prima = _valori(tmp_path / "infra/dev_catena.env")
    (tmp_path / "infra/dev_testimone.env").unlink()
    modulo.genera(tmp_path)
    dopo = _valori(tmp_path / "infra/dev_catena.env")
    assert dopo["CATENA_CHIAVE_PRIVATA"] != prima["CATENA_CHIAVE_PRIVATA"]
    assert (tmp_path / "infra/dev_testimone.env").exists()


def test_coppia_del_canale_ciclo_vita(tmp_path: Path) -> None:
    """R05: chiave privata del token di canale CICLO_VITA per ciclo-vita, pubblica
    per il gateway in un file a parte (dev_canali_pub.env esistente non si tocca)."""
    modulo.genera(tmp_path)
    priv = _valori(tmp_path / "infra/dev_ciclo_vita.env")["CICLO_VITA_TOKEN_CHIAVE_PRIVATA"]
    pub = _valori(tmp_path / "infra/dev_ciclo_vita_pub.env")["CICLO_VITA_TOKEN_CHIAVE_PUBBLICA"]
    assert _carica_pem(priv).key_size == 3072
    assert _carica_pem(priv).public_key().public_numbers() == _carica_pem(pub).public_numbers()
    # già presenti: non si rigenerano
    prima = (tmp_path / "infra/dev_ciclo_vita.env").read_bytes()
    modulo.genera(tmp_path)
    assert (tmp_path / "infra/dev_ciclo_vita.env").read_bytes() == prima


def test_coppia_della_posta_x25519(tmp_path: Path) -> None:
    """R12a (VD-C-152): coppia X25519 della posta in base64url senza «=» (formato
    dello script .ps1), apribile da libs/shared/security/sigillo; non si
    sovrascrive se esiste, --forza la rigenera."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from libs.shared.security import sigillo

    esiti = "\n".join(modulo.genera(tmp_path))
    priv = _valori(tmp_path / "infra/dev_posta_priv.env")["POSTA_CHIAVE_PRIVATA"]
    pub = _valori(tmp_path / "infra/dev_posta_pub.env")["POSTA_CHIAVE_PUBBLICA"]
    for valore in (priv, pub):
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", valore)
        assert len(base64.urlsafe_b64decode(valore + "=")) == 32
    assert priv not in esiti
    apritore = sigillo.Apritore.da_impostazioni(priv)
    assert apritore is not None
    assert apritore.apri(sigillo.sigilla("s3greto", pub, "ctx"), "ctx") == "s3greto"
    for nome in ("dev_posta_priv.env", "dev_posta_pub.env"):
        grezzo = (tmp_path / "infra" / nome).read_bytes()
        assert b"\r" not in grezzo and not grezzo.startswith(b"\xef\xbb\xbf")
    prima = (tmp_path / "infra/dev_posta_priv.env").read_bytes()
    modulo.genera(tmp_path)
    assert (tmp_path / "infra/dev_posta_priv.env").read_bytes() == prima
    modulo.genera(tmp_path, forza=True)
    assert (tmp_path / "infra/dev_posta_priv.env").read_bytes() != prima


def test_coppia_delle_conferme_decisione(tmp_path: Path) -> None:
    """R17 (VD-P-164): privata e kid per il gateway, pubblica per ciclo-vita nel formato
    letto da libs/shared/conferme_decisione; una conferma firmata con la privata si
    verifica con la pubblica del kid; nessun valore nei messaggi; file esistenti non si
    toccano, --forza li rigenera."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from libs.shared import conferme_decisione as cd

    esiti = "\n".join(modulo.genera(tmp_path))
    gateway = _valori(tmp_path / "infra/dev_conferme.env")
    ciclo = _valori(tmp_path / "infra/dev_conferme_pub.env")
    assert set(gateway) == {"CONFERME_CHIAVE_PRIVATA", "CONFERME_KID"}
    assert set(ciclo) == {"CONFERME_CHIAVI_PUBBLICHE"}
    privata = cd.chiave_privata(gateway["CONFERME_CHIAVE_PRIVATA"])
    pubbliche = cd.chiavi_pubbliche(ciclo["CONFERME_CHIAVI_PUBBLICHE"])
    assert list(pubbliche) == [gateway["CONFERME_KID"]]
    assert base64.b64encode(pubbliche[gateway["CONFERME_KID"]].public_bytes_raw()).decode() \
        == cd.chiave_pubblica_b64(privata)
    firma = privata.sign(b"prova")
    pubbliche[gateway["CONFERME_KID"]].verify(firma, b"prova")
    assert gateway["CONFERME_CHIAVE_PRIVATA"] not in esiti
    for nome in ("dev_conferme.env", "dev_conferme_pub.env"):
        grezzo = (tmp_path / "infra" / nome).read_bytes()
        assert b"\r" not in grezzo and not grezzo.startswith(b"\xef\xbb\xbf")
    prima = (tmp_path / "infra/dev_conferme.env").read_bytes()
    modulo.genera(tmp_path)
    assert (tmp_path / "infra/dev_conferme.env").read_bytes() == prima
    (tmp_path / "infra/dev_conferme_pub.env").unlink()
    modulo.genera(tmp_path)
    assert (tmp_path / "infra/dev_conferme.env").read_bytes() != prima
    rigenerati = _valori(tmp_path / "infra/dev_conferme.env")
    assert list(cd.chiavi_pubbliche(_valori(tmp_path / "infra/dev_conferme_pub.env")
                                    ["CONFERME_CHIAVI_PUBBLICHE"])) == [rigenerati["CONFERME_KID"]]
