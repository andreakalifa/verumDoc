# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Test di libs/shared/security/codice_fiscale.py e chiavi_api.py.

Oracoli esterni (REGOLE §23.5): FTZPRV00A01Z999V e MRTMTT25D09F205Z sono gli
esempi pubblici classici di codice fiscale valido (Agenzia delle Entrate /
letteratura sul D.M. 23/12/1976).
"""
from __future__ import annotations

import pytest

from libs.shared.security.chiavi_api import (
    ChiaveApiMalformata,
    ErroreConfigurazioneChiaviApi,
    carica_pepper,
    genera_chiave_api,
    identificativo_da_chiave,
    impronta_chiave,
    normalizza_identificativo,
    verifica_chiave,
)
from libs.shared.security.codice_fiscale import (
    CodiceFiscaleNonValido,
    maschera_codice_fiscale,
    valida_codice_fiscale,
)

PEPPER = b"p" * 64


class TestCodiceFiscale:
    @pytest.mark.parametrize("cf", ["FTZPRV00A01Z999V", "MRTMTT25D09F205Z"])
    def test_esempi_pubblici_validi(self, cf: str) -> None:
        assert valida_codice_fiscale(cf) == cf

    def test_normalizza_prefisso_spid_spazi_minuscole(self) -> None:
        assert valida_codice_fiscale(" tinit-ftzprv00a01z999v ") == "FTZPRV00A01Z999V"

    def test_omocodia_accettata(self) -> None:
        # 1 → M in ultima cifra del comune: il carattere di controllo cambia
        assert valida_codice_fiscale("RSSMRA80A01H50MM") == "RSSMRA80A01H50MM"

    @pytest.mark.parametrize("cf", [
        "RSSMRA80A01H501X",   # controllo sbagliato
        "RSSMRA80A01H501",    # 15 caratteri
        "12345678901",        # partita IVA, non persona fisica
        "RSSMRA80Z01H501U",   # mese inesistente
        "RSSMRA80A99H501U",   # giorno impossibile
        "",
    ])
    def test_non_validi(self, cf: str) -> None:
        with pytest.raises(CodiceFiscaleNonValido) as exc:
            valida_codice_fiscale(cf)
        assert cf not in str(exc.value) or cf == ""

    def test_maschera(self) -> None:
        assert maschera_codice_fiscale("FTZPRV00A01Z999V") == "************999V"


class TestChiaviApi:
    def test_generata_si_verifica_e_impronta_non_contiene_la_chiave(self) -> None:
        g = genera_chiave_api(PEPPER)
        assert g.chiave.startswith(f"vd_{g.identificativo}_")
        assert len(g.identificativo) == 10
        assert len(g.impronta) <= 128  # colonna api_keys.key_hash
        assert g.chiave not in g.impronta and g.chiave not in repr(g)
        assert identificativo_da_chiave(g.chiave) == g.identificativo
        assert verifica_chiave(g.chiave, g.impronta, PEPPER)

    def test_pepper_diverso_o_chiave_alterata_non_verifica(self) -> None:
        g = genera_chiave_api(PEPPER)
        assert not verifica_chiave(g.chiave, g.impronta, b"q" * 64)
        altra = g.chiave[:-1] + ("A" if g.chiave[-1] != "A" else "B")
        assert not verifica_chiave(altra, g.impronta, PEPPER)

    def test_vecchio_formato_sha256_non_verifica(self) -> None:
        import hashlib
        g = genera_chiave_api(PEPPER)
        assert not verifica_chiave(g.chiave, hashlib.sha256(g.chiave.encode()).hexdigest(), PEPPER)

    def test_identificativo_spostato_su_altra_chiave_non_verifica(self) -> None:
        a, b = genera_chiave_api(PEPPER), genera_chiave_api(PEPPER)
        ibrida = f"vd_{b.identificativo}_{a.chiave.split('_', 2)[2]}"
        assert not verifica_chiave(ibrida, a.impronta, PEPPER)
        assert not verifica_chiave(ibrida, b.impronta, PEPPER)

    @pytest.mark.parametrize("valore", ["", "vd_abc_def", "sk_test", "vd_ABCDEFGHIJ_" + "x" * 43])
    def test_malformate(self, valore: str) -> None:
        with pytest.raises(ChiaveApiMalformata):
            identificativo_da_chiave(valore)

    def test_normalizza_identificativo(self) -> None:
        assert normalizza_identificativo("vd_abcdefghij") == "abcdefghij"
        assert normalizza_identificativo("abcdefghij") == "abcdefghij"
        with pytest.raises(ChiaveApiMalformata):
            normalizza_identificativo("vd_abc")

    def test_pepper_obbligatorio_fuori_da_sviluppo(self) -> None:
        for env in ("production", "staging", "", "qualsiasi"):
            with pytest.raises(ErroreConfigurazioneChiaviApi):
                carica_pepper(env=env, valore="", servizio="t")
        assert carica_pepper(env="development", valore="", servizio="t") is None
        assert carica_pepper(env="test", valore=None, servizio="t") is None
        with pytest.raises(ErroreConfigurazioneChiaviApi):
            carica_pepper(env="production", valore="corto", servizio="t")
        assert carica_pepper(env="production", valore="x" * 64, servizio="t") == b"x" * 64

    def test_impronta_senza_pepper_errore_esplicito(self) -> None:
        with pytest.raises(ErroreConfigurazioneChiaviApi):
            impronta_chiave("vd_x", b"")
