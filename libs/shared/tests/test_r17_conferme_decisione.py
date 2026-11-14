# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R17 (VD-P-164) — conferma firmata dal gateway delle decisioni sensibili: forma canonica,
firma e verifica pura (``libs.shared.conferme_decisione``).

Ogni controllo della verifica ha un test che lo fa scattare da solo (prova di mutazione):
firma, chiave ignota o revocata, azione, pratica, ente, utente, contenuto, versione,
scadenza, istante futuro, durata, riuso del nonce, forma non canonica.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from libs.shared import conferme_decisione as cd

ADESSO = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
PRATICA = "11111111-1111-4111-8111-111111111111"
ENTE = "22222222-2222-4222-8222-222222222222"
UTENTE = "33333333-3333-4333-8333-333333333333"
NONCE = "0123456789abcdef0123456789abcdef"

#: Vettori condivisi con la console (frontend/src/console/conferme.ts, e2e r17.spec.ts):
#: stessa forma canonica, stessa impronta (attese in ``vettori_conferme.json``)
VETTORI = [
    (cd.SCARTO_ESEGUI, {"impronta_piano": "ab" * 32},
     None),
    (cd.ANNULLAMENTO_RICHIEDI, {"pdv_id": PRATICA, "motivo": "Versamento duplicato: è già "
                                                           "presente il PdV corretto"}, None),
    (cd.RETTIFICA_RICHIEDI, {"riferimento": f"pdv:{PRATICA}",
                             "metadati_nuovi": {"oggetto": "Determina n. 12 — rettificata",
                                                "anno": 2026, "tag": ["a", "b"]},
                             "motivo": "Oggetto errato"}, None),
]


def _chiave() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def _firmata(azione: str = cd.SCARTO_ESEGUI, *, pratica: str | None = PRATICA,
             versione: int = 3, impronta: str | None = None, utente: str = UTENTE,
             ente: str = ENTE, kid: str = "gw-1", adesso: datetime = ADESSO,
             durata: int = 300, nonce: str = NONCE, privata: Ed25519PrivateKey | None = None
             ) -> dict[str, str]:
    impronta = impronta or cd.impronta_contenuto(azione, {"impronta_piano": "ab" * 32})
    return cd.firma(cd.Richiesta(azione, pratica, versione, impronta, ente), utente=utente,
                    kid=kid, privata=privata or _chiave(), adesso=adesso,
                    durata_secondi=durata, nonce=nonce)


def _reg(firmata: dict[str, str], *, registrata: datetime | None = None, versione: int = 3,
         ident: str = "c1") -> cd.Registrazione:
    crudi, firma, _ = cd.leggi(firmata)
    return cd.Registrazione(id=ident, payload=crudi, firma=firma,
                            registrata_il=registrata or ADESSO + timedelta(seconds=5),
                            versione_pratica=versione)


def _atteso(**sostituzioni) -> cd.Atteso:
    base = {"azione": cd.SCARTO_ESEGUI, "pratica": PRATICA,
            "impronta": cd.impronta_contenuto(cd.SCARTO_ESEGUI, {"impronta_piano": "ab" * 32}),
            "utente": UTENTE, "ente": ENTE}
    return cd.Atteso(**{**base, **sostituzioni})


def _chiavi() -> dict:
    return {"gw-1": _chiave().public_key()}


def _difetti(reg: cd.Registrazione, atteso: cd.Atteso | None = None, *, revocati=frozenset(),
             usata_da: str | None = None, chiavi: dict | None = None) -> list[str]:
    return cd.difetti(reg, atteso or _atteso(), chiavi=_chiavi() if chiavi is None else chiavi,
                      revocati=revocati, usata_da=usata_da)


def _codici(difetti: list[str]) -> set[str]:
    return {d.split(":", 1)[0] for d in difetti}


def test_forma_canonica_e_impronte_stabili() -> None:
    assert cd.json_canonico({"b": 1, "a": "è"}) == '{"a":"è","b":1}'.encode()
    impronta = cd.impronta_contenuto(cd.SCARTO_ESEGUI, {"impronta_piano": "ab" * 32})
    assert impronta == cd.impronta_contenuto(cd.SCARTO_ESEGUI, {"impronta_piano": "ab" * 32})
    assert impronta != cd.impronta_contenuto(cd.SCARTO_APPROVA, {"impronta_piano": "ab" * 32})
    with pytest.raises(ValueError):
        cd.impronta_contenuto("SCARTO_QUALSIASI", {})
    with pytest.raises(ValueError):
        cd.impronta_contenuto(cd.SCARTO_ESEGUI, {"azione": "x"})


def test_vettori_condivisi_con_la_console() -> None:
    """I valori sono scritti anche nel test e2e (r17.spec.ts): se la forma canonica cambia,
    cambiano entrambi."""
    attesi = {a: cd.impronta_contenuto(a, c) for a, c, _ in VETTORI}
    assert attesi == json.loads((__import__("pathlib").Path(__file__).parent
                                 / "vettori_conferme.json").read_text(encoding="utf-8"))


def test_conferma_valida() -> None:
    assert _difetti(_reg(_firmata())) == []


def test_firma_alterata_o_di_un_altra_chiave() -> None:
    reg = _reg(_firmata())
    alterata = cd.Registrazione(id=reg.id, payload=reg.payload,
                                firma=bytes([reg.firma[0] ^ 1]) + reg.firma[1:],
                                registrata_il=reg.registrata_il, versione_pratica=3)
    assert _codici(_difetti(alterata)) == {"CONFERMA_FIRMA_NON_VALIDA"}
    altra = _reg(_firmata(privata=Ed25519PrivateKey.generate()))
    assert _codici(_difetti(altra)) == {"CONFERMA_FIRMA_NON_VALIDA"}


def test_chiave_ignota_e_revocata() -> None:
    assert _codici(_difetti(_reg(_firmata(kid="gw-2")))) == {"CONFERMA_CHIAVE_IGNOTA"}
    assert _codici(_difetti(_reg(_firmata()), revocati=frozenset({"gw-1"}))) == {
        "CONFERMA_CHIAVE_REVOCATA"}


@pytest.mark.parametrize("campo,valore,codice", [
    ("azione", cd.SCARTO_APPROVA, "CONFERMA_AZIONE_DIVERSA"),
    ("pratica", "44444444-4444-4444-8444-444444444444", "CONFERMA_PRATICA_DIVERSA"),
    ("ente", "55555555-5555-4555-8555-555555555555", "CONFERMA_ENTE_DIVERSO"),
    ("utente", "66666666-6666-4666-8666-666666666666", "CONFERMA_UTENTE_DIVERSO"),
    ("utente", None, "CONFERMA_UTENTE_DIVERSO"),
    ("impronta", "cd" * 32, "CONFERMA_CONTENUTO_DIVERSO"),
])
def test_ogni_campo_deve_corrispondere(campo: str, valore, codice: str) -> None:
    assert _codici(_difetti(_reg(_firmata()), _atteso(**{campo: valore}))) == {codice}


def test_versione_diversa_da_quella_registrata() -> None:
    assert _codici(_difetti(_reg(_firmata(versione=3), versione=4))) == {
        "CONFERMA_VERSIONE_DIVERSA"}


def test_scaduta_futura_e_troppo_lunga() -> None:
    tardi = _reg(_firmata(), registrata=ADESSO + timedelta(seconds=301))
    assert _codici(_difetti(tardi)) == {"CONFERMA_SCADUTA"}
    assert _difetti(_reg(_firmata(), registrata=ADESSO + timedelta(seconds=300))) == []
    presto = _reg(_firmata(), registrata=ADESSO - timedelta(seconds=61))
    assert _codici(_difetti(presto)) == {"CONFERMA_ISTANTE_FUTURO"}
    assert _difetti(_reg(_firmata(), registrata=ADESSO - timedelta(seconds=60))) == []
    # una scadenza oltre il massimo non si firma (limitata) né si accetta se costruita
    lunga = _firmata(durata=10_000)
    assert json.loads(cd.leggi(lunga)[0])["scadenza"] == "2026-11-12T09:45:00Z"
    contenuto = json.loads(cd.leggi(_firmata())[0])
    contenuto["scadenza"] = "2026-11-12T10:30:00Z"
    crudi = cd.json_canonico(contenuto)
    forgiata = cd.Registrazione(id="c1", payload=crudi, firma=_chiave().sign(crudi),
                                registrata_il=ADESSO, versione_pratica=3)
    assert _codici(_difetti(forgiata)) == {"CONFERMA_DURATA"}


def test_nonce_gia_consumato_da_un_altra_conferma() -> None:
    reg = _reg(_firmata())
    assert _codici(_difetti(reg, usata_da="altra")) == {"CONFERMA_RIUSATA"}
    assert _difetti(reg, usata_da="c1") == []   # la stessa conferma riverificata


def test_forma_non_canonica_o_campi_in_piu() -> None:
    contenuto = json.loads(cd.leggi(_firmata())[0])
    spaziato = json.dumps(contenuto).encode()
    with pytest.raises(cd.ConfermaNonValida):
        cd.leggi({"payload": cd.b64url(spaziato), "firma": cd.b64url(_chiave().sign(spaziato))})
    contenuto["extra"] = 1
    crudi = cd.json_canonico(contenuto)
    with pytest.raises(cd.ConfermaNonValida):
        cd.leggi({"payload": cd.b64url(crudi), "firma": cd.b64url(_chiave().sign(crudi))})
    reg = cd.Registrazione(id="c1", payload=crudi, firma=_chiave().sign(crudi),
                           registrata_il=ADESSO, versione_pratica=3)
    assert _codici(_difetti(reg)) == {"CONFERMA_ILLEGGIBILE"}


def test_richieste_ben_formate() -> None:
    impronta = "ab" * 32
    assert cd.controlla_richiesta(cd.Richiesta(cd.SCARTO_ESEGUI, PRATICA, 3, impronta, ENTE)) \
        is None
    assert cd.controlla_richiesta(cd.Richiesta(cd.ANNULLAMENTO_RICHIEDI, None, 0, impronta,
                                               ENTE)) is None
    assert cd.controlla_richiesta(cd.Richiesta(cd.ANNULLAMENTO_RICHIEDI, PRATICA, 0, impronta,
                                               ENTE))
    assert cd.controlla_richiesta(cd.Richiesta(cd.SCARTO_ESEGUI, None, 3, impronta, ENTE))
    assert cd.controlla_richiesta(cd.Richiesta(cd.SCARTO_ESEGUI, PRATICA, 0, impronta, ENTE))
    assert cd.controlla_richiesta(cd.Richiesta(cd.SCARTO_ESEGUI, PRATICA, 3, "x", ENTE))
    assert cd.controlla_richiesta(cd.Richiesta("ALTRO", PRATICA, 3, impronta, ENTE))


def test_chiavi_dalla_configurazione() -> None:
    privata = _chiave()
    pubblica = cd.chiave_pubblica_b64(privata)
    chiavi = cd.chiavi_pubbliche(json.dumps({"gw-1": pubblica}))
    assert set(chiavi) == {"gw-1"}
    assert cd.chiavi_pubbliche("") == {}
    for errata in ('{"gw 1": "x"}', '{"gw-1": "AAAA"}', '["gw-1"]'):
        with pytest.raises(ValueError):
            cd.chiavi_pubbliche(errata)
    import base64
    assert cd.chiave_privata(base64.b64encode(bytes(range(32))).decode()).public_key() \
        .public_bytes_raw() == privata.public_key().public_bytes_raw()
    with pytest.raises(ValueError):
        cd.chiave_privata("")
    assert cd.kid_revocati("gw-0, gw-1") == {"gw-0", "gw-1"}
    assert cd.kid_revocati('["gw-0"]') == {"gw-0"}


def test_riferimento_della_rettifica() -> None:
    pratica = {"pdv_id": PRATICA, "dati": {"aip_id": ENTE}}
    assert cd.riferimento_coerente(f"pdv:{PRATICA}", pratica)
    assert cd.riferimento_coerente(f"aip:{ENTE}", pratica)
    assert not cd.riferimento_coerente(f"aip:{PRATICA}", pratica)
    assert not cd.riferimento_coerente(None, pratica)
    assert cd.riferimento_rettifica("a1", None) == "aip:a1"
    assert cd.riferimento_rettifica(None, "p1") == "pdv:p1"
