# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 (b5): verifica senza file in memoria e con le regole fotografate nel PdV.

- ``regole_pdv``: limite per file e formati dalla fotografia (``pdv.regole_applicate``),
  predefiniti del servizio solo se la fotografia manca, errore se illeggibile;
- Step 4: formati della classe (dichiarato e rilevato dal gateway);
- Step 5: lettura in streaming, file oltre il limite NON scaricato, confronto con
  dimensione e impronte misurate dal gateway (VLD-502);
- Step 8: niente limite fisso di 100 MB.
"""
from __future__ import annotations

import hashlib

import pytest
from app.services.regole_pdv import MIB, RegoleNonLeggibili, regole_del_pdv
from app.validators.base import ErroreVerificaTransitorio, ValidationContext
from app.validators.steps import Step4FormatoMIME, Step5IntegritaHash, Step8DimensioneFile

from tests.lettore_memoria import LettoreMemoria, lettore_per_nomi, riferimento

PDF = b"%PDF-1.7 contenuto di prova per la verifica in streaming"


def _foto(**impostazioni) -> dict:
    return {"catalogo_regole": "1", "profilo_metadati": None, "impostazioni": {
        codice: {"valore": valore, "origine": "CLASSE"}
        for codice, valore in impostazioni.items()}}


def _regole(foto) -> object:
    return regole_del_pdv(foto, limite_mb_predefinito=5120,
                          formati_predefiniti=["application/pdf", "image/png"])


def _indice(nome="doc.pdf", contenuto=PDF, algoritmo="SHA-256", formato="application/pdf"):
    funzione = {"SHA-256": hashlib.sha256, "SHA-512": hashlib.sha512}[algoritmo]
    return {"classe_documentale": "fattura", "documenti": [{
        "id": "d1", "tipo": "PRINCIPALE", "nome_file": nome, "formato": formato,
        "hash": {"algoritmo": algoritmo, "impronta": funzione(contenuto).hexdigest()}}]}


# ── regole_pdv ────────────────────────────────────────────────────────

class TestRegoleDelPdv:
    def test_senza_fotografia_predefiniti_del_servizio(self):
        r = _regole(None)
        assert r.limite_byte_file == 5120 * MIB and not r.da_fotografia
        assert r.formati_ammessi == ("application/pdf", "image/png")

    def test_limite_e_formati_della_classe(self):
        r = _regole(_foto(CLASSE_DIMENSIONE_MAX_MB=250, FORMATI_AMMESSI=["Application/PDF"]))
        assert r.limite_byte_file == 250 * MIB and r.origine_limite == "CLASSE"
        assert r.formati_ammessi == ("application/pdf",) and r.da_fotografia

    def test_regola_dimensione_non_bloccante_nessun_limite(self):
        """RGL-104 in AVVISA: il versamento ha accettato il file, la verifica non lo rifiuta."""
        foto = _foto(CLASSE_DIMENSIONE_MAX_MB=1)
        foto["impostazioni"]["REGOLA.RGL-104.EFFETTO"] = {"valore": "AVVISA", "origine": "ENTE"}
        r = _regole(foto)
        assert r.limite_byte_file is None

    def test_formati_vuoti_o_regola_ignorata_nessuna_restrizione(self):
        assert _regole(_foto(FORMATI_AMMESSI=[])).formati_ammessi is None
        foto = _foto(FORMATI_AMMESSI=["application/pdf"])
        foto["impostazioni"]["REGOLA.RGL-105.EFFETTO"] = {"valore": "IGNORA", "origine": "CLASSE"}
        assert _regole(foto).formati_ammessi is None

    @pytest.mark.parametrize("foto", [
        "testo", {"impostazioni": "x"}, _foto(CLASSE_DIMENSIONE_MAX_MB="100"),
        _foto(CLASSE_DIMENSIONE_MAX_MB=0), _foto(FORMATI_AMMESSI="application/pdf"),
        {"impostazioni": {"CLASSE_DIMENSIONE_MAX_MB": 5}},
        {"impostazioni": {"REGOLA.RGL-104.EFFETTO": {"valore": "FORSE"}}},
    ])
    def test_fotografia_illeggibile_nessun_valore_indovinato(self, foto):
        with pytest.raises(RegoleNonLeggibili):
            _regole(foto)


# ── Step 4 ────────────────────────────────────────────────────────────

class TestStep4FormatiDellaClasse:
    async def test_formato_ammesso_dalla_classe_anche_se_fuori_elenco_servizio(self):
        """Prima: elenco fisso del servizio → un formato ammesso dalla classe era rifiutato."""
        indice = _indice(nome="a.odt", formato="application/vnd.oasis.opendocument.text")
        ctx = ValidationContext("p", "t", indice, regole=_regole(
            _foto(FORMATI_AMMESSI=["application/vnd.oasis.opendocument.text"])))
        assert (await Step4FormatoMIME().validate(ctx)).ok

    async def test_formato_rilevato_dal_gateway_controllato(self):
        indice = _indice()
        rif = riferimento("doc.pdf", formato_rilevato="application/x-msdownload")
        ctx = ValidationContext("p", "t", indice, riferimenti={"doc.pdf": rif},
                                regole=_regole(_foto(FORMATI_AMMESSI=["application/pdf"])))
        r = await Step4FormatoMIME().validate(ctx)
        assert not r.ok and r.codice_errore == "VLD-401"
        assert r.dettagli["errori"][0]["origine_formato"] == "rilevato"

    async def test_r09_atto_di_rettifica_ammesso_solo_dal_canale_ciclo_vita(self):
        """R09 (D24): l'atto XML di rettifica è un tipo di sistema (canale CICLO_VITA)."""
        indice = _indice(nome="atto.xml", formato="application/xml")
        indice["documenti"][0]["tipo"] = "PRINCIPALE"
        indice["documenti"][0]["parametri"] = {
            "identificativo_documento": "rettifica-p1", "rettifica_di": "urn:x",
            "pdv_rettificato": "p0", "pratica_rettifica": "p1"}
        regole = _regole(_foto(FORMATI_AMMESSI=["application/pdf"]))
        ctx = ValidationContext("p", "t", indice, regole=regole,
                                metadata={"canale": "CICLO_VITA"})
        assert (await Step4FormatoMIME().validate(ctx)).ok
        ctx = ValidationContext("p", "t", indice, regole=regole, metadata={"canale": "REST"})
        assert not (await Step4FormatoMIME().validate(ctx)).ok

    async def test_classe_senza_restrizioni(self):
        indice = _indice(formato="application/zip")
        ctx = ValidationContext("p", "t", indice, regole=_regole(_foto(FORMATI_AMMESSI=[])))
        assert (await Step4FormatoMIME().validate(ctx)).ok


# ── Step 5 e Step 8 ───────────────────────────────────────────────────

class TestStep5Streaming:
    async def test_file_oltre_limite_non_scaricato_poi_rifiutato_dallo_step_8(self):
        grande = b"x" * (2 * MIB)
        indice = _indice(contenuto=grande)
        riferimenti, lettore = lettore_per_nomi({"doc.pdf": grande})
        ctx = ValidationContext("p", "t", indice, riferimenti=riferimenti, lettore=lettore,
                                regole=_regole(_foto(CLASSE_DIMENSIONE_MAX_MB=1)))
        r5 = await Step5IntegritaHash().validate(ctx)
        assert r5.ok and r5.dettagli["non_letti_oltre_limite"] == ["doc.pdf"]
        assert lettore.letture == []  # nessun byte scaricato
        r8 = await Step8DimensioneFile().validate(ctx)
        assert not r8.ok and r8.codice_errore == "VLD-801"
        assert r8.dettagli["errori"][0]["size_bytes"] == 2 * MIB

    async def test_file_oltre_100mb_accettato_se_la_classe_lo_consente(self):
        """VLD-801 fisso a 100 MB eliminato: vale il limite della classe."""
        dimensione = 101 * MIB
        attesa = hashlib.sha256()
        for _ in range(dimensione // MIB):
            attesa.update(b"\0" * MIB)
        indice = {"classe_documentale": "f", "documenti": [{
            "id": "d1", "tipo": "PRINCIPALE", "nome_file": "big.pdf", "formato": "application/pdf",
            "hash": {"algoritmo": "SHA-256", "impronta": attesa.hexdigest()}}]}

        class LettoreZeri:
            """Contenuto di zeri generato a blocchi: nulla di intero in memoria."""
            async def dimensione(self, rif):
                return dimensione

            async def leggi(self, rif, *, algoritmi, limite_byte=None):
                from app.services.lettura_file import FileLetto, calcolatori
                calc, blocco = calcolatori(algoritmi), b"\0" * MIB
                for _ in range(dimensione // MIB):
                    for c in calc.values():
                        c.update(blocco)
                return FileLetto(dimensione, {n: c.hexdigest() for n, c in calc.items()})

        ctx = ValidationContext("p", "t", indice, riferimenti={"big.pdf": riferimento("big.pdf")},
                                lettore=LettoreZeri(),
                                regole=_regole(_foto(CLASSE_DIMENSIONE_MAX_MB=500)))
        assert (await Step5IntegritaHash().validate(ctx)).ok
        assert (await Step8DimensioneFile().validate(ctx)).ok

    async def test_algoritmo_dichiarato_e_sha256_calcolati(self):
        indice = _indice(algoritmo="SHA-512")
        riferimenti, lettore = lettore_per_nomi({"doc.pdf": PDF})
        ctx = ValidationContext("p", "t", indice, riferimenti=riferimenti, lettore=lettore)
        r = await Step5IntegritaHash().validate(ctx)
        assert r.ok
        calcolate = r.dettagli["impronte_ricalcolate"]["doc.pdf"]
        assert calcolate["sha256"] == hashlib.sha256(PDF).hexdigest()
        assert set(ctx.file_letti["doc.pdf"].impronte) == {"SHA-256", "SHA-512"}

    @pytest.mark.parametrize("campo,valore", [
        ("dimensione_versata", len(PDF) + 1),
        ("impronte_versamento", {"SHA-256": "00" * 32}),
    ])
    async def test_file_diverso_da_quello_versato_vld_502(self, campo, valore):
        """Il gateway ha misurato altro in transito: file alterato nello staging."""
        rif = riferimento("doc.pdf", **{campo: valore})
        ctx = ValidationContext("p", "t", _indice(), riferimenti={"doc.pdf": rif},
                                lettore=LettoreMemoria({("pdv", "doc.pdf"): PDF}))
        r = await Step5IntegritaHash().validate(ctx)
        assert not r.ok and r.codice_errore == "VLD-502"

    async def test_impronte_del_versamento_coerenti_ok(self):
        rif = riferimento("doc.pdf", dimensione_versata=len(PDF), impronte_versamento={
            "SHA-256": hashlib.sha256(PDF).hexdigest(), "SHA-512": hashlib.sha512(PDF).hexdigest()})
        ctx = ValidationContext("p", "t", _indice(), riferimenti={"doc.pdf": rif},
                                lettore=LettoreMemoria({("pdv", "doc.pdf"): PDF}))
        assert (await Step5IntegritaHash().validate(ctx)).ok

    async def test_storage_giu_errore_transitorio(self):
        riferimenti, _ = lettore_per_nomi({"doc.pdf": PDF})
        ctx = ValidationContext("p", "t", _indice(), riferimenti=riferimenti,
                                lettore=LettoreMemoria({}))
        with pytest.raises(ErroreVerificaTransitorio):
            await Step5IntegritaHash().validate(ctx)
