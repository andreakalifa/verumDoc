# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Rapporto di versamento definitivo (RdV) emesso dopo il sigillo del blocco.

Servizio: hashchain. Riferimento: VD-C-012, VD-C-020; LG AgID §4.7 (rapporto di
versamento), REGOLE_SVILUPPO §10.3 (impronte calcolate), §11.6 (esito salvato).

Formato proprietario VerumDoc (attributo ``formato="verumdoc-rdv-1"``): AgID
non pubblica uno schema XML del rapporto di versamento, quindi il documento
non dichiara un namespace AgID. La funzione è pura (niente orologio: le date
entrano come argomento), così a parità di dati produce lo stesso XML.
Contiene: identificativi di AIP e PdV, URN, impronte del pacchetto e di ogni
documento (ricalcolate dall'aip-builder sui byte), blocco della catena con
prova di inclusione di Merkle (RFC 6962) e stato della firma del blocco.
"""
from __future__ import annotations

from xml.dom.minidom import parseString
from xml.etree.ElementTree import Element, SubElement, tostring

FORMATO_RDV = "verumdoc-rdv-1"


def costruisci_rdv_definitiva(
    *,
    aip_id: str,
    urn: str,
    hash_sha256: str,
    numero_blocco: int,
    merkle_root: str,
    hash_blocco: str,
    data_conservazione: str,
    pdv_id: str | None = None,
    hash_blocco_precedente: str | None = None,
    documenti: list[dict] | None = None,
    prova_inclusione: list[tuple[str, str]] | None = None,
    stato_firma_blocco: str | None = None,
    motivo_firma_blocco: str | None = None,
    conservatore: str = "VerumDoc S.r.l.",
    emessa_il: str | None = None,
) -> str:
    """Restituisce l'XML del RdV definitivo (stringa UTF-8 indentata)."""
    radice = Element("RicevutaVersamento")
    radice.set("tipo", "DEFINITIVA")
    radice.set("formato", FORMATO_RDV)

    SubElement(radice, "IdentificativoAIP").text = aip_id
    if pdv_id:
        SubElement(radice, "IdentificativoPdV").text = pdv_id
    SubElement(radice, "URN").text = urn
    SubElement(radice, "HashAIP").text = hash_sha256
    SubElement(radice, "AlgoritmoHash").text = "SHA-256"

    if documenti:
        elenco = SubElement(radice, "Documenti")
        for doc in documenti:
            el = SubElement(elenco, "Documento")
            SubElement(el, "NomeFile").text = str(doc.get("nome_file", ""))
            SubElement(el, "ImprontaSHA256").text = str(doc.get("hash_sha256", ""))
            if doc.get("hash_sha512"):
                SubElement(el, "ImprontaSHA512").text = str(doc["hash_sha512"])

    SubElement(radice, "NumeroBloccoHashchain").text = str(numero_blocco)
    SubElement(radice, "MerkleRoot").text = merkle_root
    SubElement(radice, "HashBlocco").text = hash_blocco
    if hash_blocco_precedente:
        SubElement(radice, "HashBloccoPrecedente").text = hash_blocco_precedente
    if prova_inclusione is not None:
        prova = SubElement(radice, "ProvaInclusione")
        prova.set("algoritmo", "RFC6962-SHA256")
        for impronta, lato in prova_inclusione:
            passo = SubElement(prova, "Passo")
            passo.set("lato", lato)
            passo.text = impronta
    if stato_firma_blocco:
        firma = SubElement(radice, "FirmaBlocco")
        SubElement(firma, "Stato").text = stato_firma_blocco
        if motivo_firma_blocco:
            SubElement(firma, "Motivo").text = motivo_firma_blocco

    SubElement(radice, "DataConservazione").text = data_conservazione
    SubElement(radice, "DataEmissione").text = emessa_il or data_conservazione
    SubElement(radice, "ConservatoreId").text = conservatore
    SubElement(radice, "StatoConservazione").text = "CONSERVATO"

    grezzo = tostring(radice, encoding="unicode")
    return parseString(grezzo).toprettyxml(indent="  ")  # noqa: S318 — XML prodotto dal servizio stesso, non dall'esterno
