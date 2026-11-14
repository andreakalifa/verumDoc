# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/normative.py — valori normativi ancora scritti nel codice dei
servizi, portati nel registro (R05 Q1; REGOLE_SVILUPPO §8.1, §9.9).

Inventario fatto con grep su ``services/*/app`` e ``libs/shared`` il 23/10/2026
(rapporto r05-regole). Ogni voce qui ha come predefinito ESATTAMENTE il valore
oggi usato dal servizio indicato in ``descrizione``. Le voci hanno
``applicata_da = None``: il servizio non le legge ancora dal registro, quindi la
console le MOSTRA (codice, valore, riferimento normativo, dove sta nel codice)
ma non le lascia modificare (§9.9: un parametro che nessuno applica non si
espone come modificabile). Collegarle è una richiesta al coordinatore per i
servizi fuori dal perimetro di questo rilascio; quando un servizio le legge si
imposta ``applicata_da`` e l'ambito.

Il validatore preventivo (``validatore.py``) usa i predefiniti di queste voci
per i controlli di forma che replica dal gateway: stesso valore, una sola fonte.
"""

from __future__ import annotations

from libs.verumdoc_rules.impostazioni import (
    Impostazione,
    RegistroImpostazioni,
    TipoImpostazione,
)

ALGORITMI_IMPRONTA_AMMESSI = "ALGORITMI_IMPRONTA_AMMESSI"
ALGORITMI_IMPRONTA_PDA = "ALGORITMI_IMPRONTA_PDA"
CONSERVAZIONE_ANNI_PREDEFINITI = "CONSERVAZIONE_ANNI_PREDEFINITI"
OGGETTO_LUNGHEZZA_MINIMA = "OGGETTO_LUNGHEZZA_MINIMA"
CHIAVE_ANNO_MINIMO = "CHIAVE_ANNO_MINIMO"
MODALITA_FORMAZIONE_AMMESSE = "MODALITA_FORMAZIONE_AMMESSE"
TIPOLOGIE_SINCRO = "TIPOLOGIE_SINCRO"
TIPI_DOCUMENTO_INDICE = "TIPI_DOCUMENTO_INDICE"

_LG = "Linee guida AgID sulla formazione, gestione e conservazione dei documenti informatici (2021)"

REGISTRO_NORMATIVO = RegistroImpostazioni((
    Impostazione(
        ALGORITMI_IMPRONTA_AMMESSI, TipoImpostazione.CODICI,
        predefinito=("SHA-256", "SHA-512", "SHA3-256"),
        descrizione=("Algoritmi d'impronta accettati nell'indice del versamento. Nel codice: "
                     "libs/shared/impronte.py ALGORITMI_IMPRONTA (gateway, validation, "
                     "aip-builder); è anche l'elenco di ciò che il codice sa calcolare"),
        norm_ref=f"{_LG}, Allegato 6 (impronta SHA-256 come minimo)",
        locked=True, applicata_da=None, gruppo="normative"),
    Impostazione(
        ALGORITMI_IMPRONTA_PDA, TipoImpostazione.CODICI,
        predefinito=("SHA-256", "SHA-512"),
        descrizione=("Impronte calcolate sui file del PdA. Nel codice: "
                     "services/aip-builder/app/services/file_pda.py ALGORITMI_PDA"),
        norm_ref=f"{_LG}, Allegato 6", locked=True, applicata_da=None, gruppo="normative"),
    Impostazione(
        CONSERVAZIONE_ANNI_PREDEFINITI, TipoImpostazione.INTERO, predefinito=10,
        descrizione=("Anni di conservazione proposti per una classe nuova. Nel codice: "
                     "services/onboarding/app/routers/classi.py (ClasseCreate) e "
                     "libs/shared/models (ClasseDocumentale.anni_conservazione, server_default)"),
        minimo=1, massimo=100, applicata_da=None, gruppo="normative"),
    Impostazione(
        OGGETTO_LUNGHEZZA_MINIMA, TipoImpostazione.INTERO, predefinito=5,
        descrizione=("Lunghezza minima del metadato «oggetto». Nel codice: "
                     "services/gateway/app/models/versamento.py (min_length=5) e "
                     "services/validation/app/validators/steps.py (passo 6)"),
        minimo=1, massimo=1000, applicata_da=None, gruppo="normative"),
    Impostazione(
        CHIAVE_ANNO_MINIMO, TipoImpostazione.INTERO, predefinito=1900,
        descrizione=("Anno minimo plausibile della chiave dell'unità documentaria. Nel codice: "
                     "services/validation/app/validators/steps.py (passo 9)"),
        minimo=1000, massimo=2100, applicata_da=None, gruppo="normative"),
    Impostazione(
        MODALITA_FORMAZIONE_AMMESSE, TipoImpostazione.CODICI, predefinito=("a", "b", "c", "d"),
        descrizione=("Lettere della modalità di formazione. Nel codice: libs/shared/models "
                     "ModalitaFormazione e tipo PostgreSQL public.modalita_formazione: un "
                     "valore nuovo richiede una migrazione (ALTER TYPE), non solo il registro"),
        norm_ref=f"{_LG}, §2.1.1 e Allegato 5 (ModalitaDiFormazione)",
        locked=True, applicata_da=None, gruppo="normative"),
    Impostazione(
        TIPOLOGIE_SINCRO, TipoImpostazione.CODICI, predefinito=("DI", "DAI", "AGG"),
        descrizione=("Tipologie dell'indice del PdA. Nel codice: libs/shared/models "
                     "TipoSInCROXML e tipo PostgreSQL public.tipo_sincro_xml (migrazione per "
                     "un valore nuovo)"),
        norm_ref="UNI 11386:2020 (SInCRO)", locked=True, applicata_da=None, gruppo="normative"),
    Impostazione(
        TIPI_DOCUMENTO_INDICE, TipoImpostazione.CODICI,
        predefinito=("PRINCIPALE", "ALLEGATO", "ANNESSO", "ANNOTAZIONE"),
        descrizione=("Tipi dei documenti dell'indice. Nel codice: "
                     "services/gateway/app/models/versamento.py (DocumentoIndice.tipo)"),
        norm_ref="UNI 11386:2020 (SInCRO); Allegato 5 (allegati)",
        locked=True, applicata_da=None, gruppo="normative"),
))

__all__ = [
    "ALGORITMI_IMPRONTA_AMMESSI",
    "ALGORITMI_IMPRONTA_PDA",
    "CHIAVE_ANNO_MINIMO",
    "CONSERVAZIONE_ANNI_PREDEFINITI",
    "MODALITA_FORMAZIONE_AMMESSE",
    "OGGETTO_LUNGHEZZA_MINIMA",
    "REGISTRO_NORMATIVO",
    "TIPI_DOCUMENTO_INDICE",
    "TIPOLOGIE_SINCRO",
]
