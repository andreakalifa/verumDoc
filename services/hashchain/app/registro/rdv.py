# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Rapporto di versamento definitivo, formato 2 (contratto K5 di R04): costruzione
e verifica offline.

Servizio: hashchain. Riferimenti: LG AgID §4.7 (rapporto di versamento);
VD-C-096 (nessun namespace AgID inventato: AgID non pubblica uno schema del
RdV); REGOLE_SVILUPPO §8.9 (formati versionati: la versione 1,
``rdv_definitiva.py``, resta leggibile e non cambia), §9.4, §11.6.

Namespace proprio ``https://schemi.verumdoc.it/rdv/definitivo/2``. Contenuto:
esito, PdV, PdA (URN, impronta del manifest, radice dell'albero del PdA),
documenti con impronte e prova d'inclusione nel PdA, IPdA con la sua prova,
avvisi del versamento (``pdv.avvisi``), foglia del registro con la prova
d'inclusione riferita al checkpoint, checkpoint (nota firmata dal registro, con
la controfirma del testimone se c'è), stato del sigillo qualificato e della
marca del checkpoint (``NON_FIRMATO`` finché manca il fornitore, VD-C-068).

Il RdV stesso non porta una firma: la sua forza viene dalle prove, che legano
ogni impronta al checkpoint firmato (e, quando ci sono, controfirmato dal
testimone e sigillato). ``verifica_rdv`` rifà tutti i controlli senza
VerumDoc: la hashchain la esegue sul documento PRIMA di scriverlo nel WORM.

Funzioni pure (niente orologio, niente I/O).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from libs.catena import nota
from libs.catena.rfc6962 import da_esadecimale, hash_foglia, verifica_inclusione

NS = "https://schemi.verumdoc.it/rdv/definitivo/2"
FORMATO = "verumdoc-rdv-definitivo-2"
ALGORITMO_ALBERO = "RFC6962-SHA256"
ET.register_namespace("", NS)


def _q(nome: str) -> str:
    return f"{{{NS}}}{nome}"


def _sub(padre: ET.Element, nome: str, testo: str | None = None,
         **attributi: Any) -> ET.Element:
    el = ET.SubElement(padre, _q(nome), {k: str(v) for k, v in attributi.items()
                                          if v is not None})
    if testo is not None:
        el.text = testo
    return el


def _prova_pda(padre: ET.Element, prova: Mapping[str, Any] | None,
               foglia: str | None) -> None:
    if prova is None or foglia is None:
        return
    el = _sub(padre, "ProvaPdA", foglia_indice=prova["foglia_indice"],
              dimensione=prova["dimensione"], algoritmo=ALGORITMO_ALBERO)
    _sub(el, "Foglia", foglia)
    percorso = _sub(el, "Percorso")
    for h in prova["percorso"]:
        _sub(percorso, "Hash", h)


def costruisci_rdv(
    *,
    pdv_id: str,
    pda: Mapping[str, Any],
    documenti: Sequence[Mapping[str, Any]],
    ipda: Mapping[str, Any] | None,
    avvisi: Iterable[Mapping[str, Any]],
    registro: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    conservato_il: str,
    emesso_il: str,
    conservatore: str,
    rapporto_firme: Mapping[str, Any] | None = None,
) -> bytes:
    """
    XML del RdV definitivo v2 (UTF-8, con dichiarazione).

    ``pda``: aip_id, urn, impronta_manifest, radice_pda (None per i PdA in
    riversamento), ente. ``documenti``: identificativo, nome_file, sha256,
    sha512, prova_pda, foglia. ``ipda``: sha256, prova_pda, foglia (None in
    riversamento). ``registro``: foglia_indice, foglia, tipo, dimensione_albero,
    percorso (hex), blocco_storico. ``checkpoint``: nota, chiave_verificatore,
    dimensione, radice, emesso_il, stato_sigillo, motivo_sigillo, data_marca,
    stato_testimone, controfirmato_il, esito_testimone.
    ``rapporto_firme`` (R08, D01): chiave e SHA-256 del rapporto di verifica delle
    firme conservato nel PdA (elemento facoltativo ``VerificaFirme``; assente per
    i PdA senza rapporto).
    """
    radice = ET.Element(_q("RapportoVersamento"),
                        {"versione": "2", "tipo": "DEFINITIVO", "formato": FORMATO})
    _sub(radice, "Esito", "CONSERVATO")
    _sub(radice, "Conservatore", conservatore)
    _sub(radice, "DataConservazione", conservato_il)
    _sub(radice, "DataEmissione", emesso_il)
    el_pdv = _sub(radice, "PdV")
    _sub(el_pdv, "Identificativo", pdv_id)

    riversamento = not pda.get("radice_pda")
    el_pda = _sub(radice, "PdA", riversamento="true" if riversamento else "false")
    _sub(el_pda, "Identificativo", str(pda["aip_id"]))
    _sub(el_pda, "URN", str(pda["urn"]))
    _sub(el_pda, "Ente", str(pda["ente"]))
    _sub(el_pda, "ImprontaManifest", str(pda["impronta_manifest"]), algoritmo="SHA-256")
    if not riversamento:
        _sub(el_pda, "RadicePdA", str(pda["radice_pda"]), algoritmo=ALGORITMO_ALBERO)

    el_doc = _sub(radice, "Documenti")
    for d in documenti:
        el = _sub(el_doc, "Documento")
        if d.get("identificativo"):
            _sub(el, "Identificativo", str(d["identificativo"]))
        _sub(el, "NomeFile", str(d["nome_file"]))
        _sub(el, "Impronta", str(d["sha256"]), algoritmo="SHA-256")
        if d.get("sha512"):
            _sub(el, "Impronta", str(d["sha512"]), algoritmo="SHA-512")
        _prova_pda(el, d.get("prova_pda"), d.get("foglia"))
    if ipda is not None:
        el_ipda = _sub(radice, "IPdA")
        _sub(el_ipda, "Impronta", str(ipda["sha256"]), algoritmo="SHA-256")
        _prova_pda(el_ipda, ipda.get("prova_pda"), ipda.get("foglia"))

    if rapporto_firme is not None:
        el_firme = _sub(radice, "VerificaFirme")
        _sub(el_firme, "Rapporto", str(rapporto_firme["chiave"]).rsplit("/", 1)[-1])
        _sub(el_firme, "Impronta", str(rapporto_firme["sha256"]), algoritmo="SHA-256")

    el_avvisi = _sub(radice, "Avvisi")
    for a in avvisi:
        _sub(el_avvisi, "Avviso", str(a.get("messaggio") or ""),
             codice=a.get("codice"), effetto=a.get("effetto"), campo=a.get("campo"),
             documento=a.get("documento"))

    el_reg = _sub(radice, "Registro")
    _sub(el_reg, "Foglia", str(registro["foglia"]), indice=registro["foglia_indice"],
         tipo=registro["tipo"])
    prova = _sub(el_reg, "ProvaInclusione", foglia_indice=registro["foglia_indice"],
                 dimensione_albero=registro["dimensione_albero"], algoritmo=ALGORITMO_ALBERO)
    for h in registro["percorso"]:
        _sub(prova, "Hash", h)
    if registro.get("blocco_storico"):
        b = registro["blocco_storico"]
        _sub(el_reg, "BloccoStorico", numero=b["numero"], hash=b["hash"], formato=b["formato"])
    cp = _sub(el_reg, "Checkpoint", dimensione=checkpoint["dimensione"],
              radice=checkpoint["radice"], emesso_il=checkpoint["emesso_il"])
    _sub(cp, "Nota", str(checkpoint["nota"]))
    _sub(cp, "ChiaveRegistro", str(checkpoint["chiave_verificatore"]), tipo="servizio")
    testimone = _sub(cp, "Testimone", stato=checkpoint["stato_testimone"],
                     controfirmato_il=checkpoint.get("controfirmato_il"))
    if checkpoint.get("esito_testimone"):
        _sub(testimone, "Motivo", str(checkpoint["esito_testimone"]))
    sigillo = _sub(cp, "SigilloQualificato", stato=checkpoint["stato_sigillo"],
                   data_marca=checkpoint.get("data_marca"))
    if checkpoint.get("motivo_sigillo"):
        _sub(sigillo, "Motivo", str(checkpoint["motivo_sigillo"]))

    ET.indent(radice, space="  ")
    return bytes(ET.tostring(radice, encoding="utf-8", xml_declaration=True))


# ── Verifica offline ─────────────────────────────────────────────────

def _testo(el: ET.Element | None) -> str:
    return (el.text or "") if el is not None else ""


def _verifica_prova_pda(el: ET.Element, radice_pda: bytes, etichetta: str,
                        problemi: list[str]) -> dict[str, Any] | None:
    prova = el.find(_q("ProvaPdA"))
    if prova is None:
        problemi.append(f"{etichetta}: prova d'inclusione nel PdA assente")
        return None
    foglia = _testo(prova.find(_q("Foglia")))
    try:
        percorso = da_esadecimale([_testo(h) for h in prova.iter(_q("Hash"))])
        ok = verifica_inclusione(hash_foglia(foglia.encode("utf-8")),
                                 int(prova.get("foglia_indice", "-1")),
                                 int(prova.get("dimensione", "0")), percorso, radice_pda)
    except ValueError:
        ok = False
    if not ok:
        problemi.append(f"{etichetta}: prova d'inclusione nel PdA non valida")
        return None
    try:
        dati: dict[str, Any] = json.loads(foglia)
    except ValueError:
        problemi.append(f"{etichetta}: foglia non JSON")
        return None
    return dati


def riferimenti_rdv(xml: bytes) -> dict[str, Any]:
    """Dati di riferimento di un RdV v2 (anche già conservato nel WORM): checkpoint
    citato, indice della foglia, stato del testimone e del sigillo. Serve quando
    si riusa un RdV scritto da un tentativo precedente (rev4 rilievo 12): il
    riferimento nel database e l'evento devono citare lo STESSO checkpoint del
    documento conservato. ``ValueError`` se il documento non è leggibile."""
    try:
        radice = ET.fromstring(xml)  # noqa: S314 — vedi verifica_rdv
    except ET.ParseError as exc:
        raise ValueError(f"RdV non leggibile: {exc}") from None
    reg = radice.find(_q("Registro"))
    cp = reg.find(_q("Checkpoint")) if reg is not None else None
    foglia = reg.find(_q("Foglia")) if reg is not None else None
    if cp is None or foglia is None:
        raise ValueError("RdV senza registro o checkpoint")
    testimone = cp.find(_q("Testimone"))
    sigillo = cp.find(_q("SigilloQualificato"))
    return {
        "checkpoint_dimensione": int(cp.get("dimensione", "0")),
        "foglia_indice": int(foglia.get("indice", "-1")),
        "stato_testimone": testimone.get("stato") if testimone is not None else None,
        "stato_sigillo_checkpoint": sigillo.get("stato") if sigillo is not None else None,
        "emessa_il": _testo(radice.find(_q("DataEmissione"))) or None,
    }


def verifica_rdv(xml: bytes, verificatori_registro: Iterable[nota.Verificatore | str],
                 verificatori_testimone: Iterable[nota.Verificatore | str] = ()) -> list[str]:
    """
    Rifà tutti i controlli del RdV v2 e restituisce i problemi (vuoto = valido):
    firma del registro sul checkpoint (con le chiavi FIDATE indicate, mai con
    quella scritta nel documento), corrispondenza nota ↔ attributi, inclusione
    della foglia del registro, radice del PdA nella foglia, inclusione di ogni
    documento e dell'IPdA nel PdA con le impronte dichiarate; se indicati i
    verificatori del testimone e lo stato è CONTROFIRMATO, anche la controfirma.
    """
    problemi: list[str] = []
    try:
        # expat di Python ≥ 3.12 non risolve entità esterne e limita l'espansione
        radice = ET.fromstring(xml)  # noqa: S314
    except ET.ParseError as exc:
        return [f"XML non leggibile: {exc}"]
    if radice.tag != _q("RapportoVersamento") or radice.get("versione") != "2":
        return ["Non è un RdV definitivo versione 2"]
    reg = radice.find(_q("Registro"))
    cp = reg.find(_q("Checkpoint")) if reg is not None else None
    if reg is None or cp is None:
        return ["Registro o checkpoint assenti"]

    testo_nota = _testo(cp.find(_q("Nota")))
    try:
        firme = nota.verifica(testo_nota, list(verificatori_registro))
        corpo = nota.analizza(testo_nota)
    except nota.ErroreNota as exc:
        return [f"Checkpoint malformato: {exc}"]
    if not firme:
        problemi.append("Checkpoint senza firma valida del registro")
    if str(corpo.dimensione) != cp.get("dimensione") or corpo.radice.hex() != cp.get("radice"):
        problemi.append("Dimensione o radice del checkpoint diverse dalla nota firmata")
    testimone = cp.find(_q("Testimone"))
    verificatori_t = list(verificatori_testimone)
    if verificatori_t and testimone is not None and testimone.get("stato") == "CONTROFIRMATO" \
            and not nota.verifica(testo_nota, verificatori_t):
        problemi.append("Checkpoint dichiarato controfirmato senza controfirma valida")

    foglia_el, prova_el = reg.find(_q("Foglia")), reg.find(_q("ProvaInclusione"))
    if foglia_el is None or prova_el is None:
        return [*problemi, "Foglia o prova del registro assenti"]
    foglia = _testo(foglia_el)
    try:
        percorso = da_esadecimale([_testo(h) for h in prova_el.iter(_q("Hash"))])
        incluso = int(prova_el.get("dimensione_albero", "0")) == corpo.dimensione and \
            verifica_inclusione(hash_foglia(foglia.encode("utf-8")),
                                int(prova_el.get("foglia_indice", "-1")), corpo.dimensione,
                                percorso, corpo.radice)
    except ValueError:
        incluso = False
    if not incluso:
        problemi.append("Prova d'inclusione nel registro non valida per il checkpoint")
    try:
        dati_foglia = json.loads(foglia)
    except ValueError:
        return [*problemi, "Foglia del registro non JSON"]

    pda = radice.find(_q("PdA"))
    if pda is None:
        return [*problemi, "PdA assente"]
    urn = _testo(pda.find(_q("URN")))
    if dati_foglia.get("pda") != urn:
        problemi.append("URN del PdA diverso da quello della foglia del registro")
    if pda.get("riversamento") == "true":
        if dati_foglia.get("impronta_pda") != _testo(pda.find(_q("ImprontaManifest"))):
            problemi.append("Impronta del manifest diversa da quella della foglia di riversamento")
        return problemi

    radice_hex = _testo(pda.find(_q("RadicePdA")))
    if dati_foglia.get("radice_pda") != radice_hex:
        problemi.append("Radice del PdA diversa da quella della foglia del registro")
        return problemi
    radice_pda = bytes.fromhex(radice_hex)
    for i, doc in enumerate(radice.iter(_q("Documento"))):
        dati = _verifica_prova_pda(doc, radice_pda, f"Documento {i + 1}", problemi)
        if dati is None:
            continue
        sha = next((_testo(x) for x in doc.findall(_q("Impronta"))
                    if x.get("algoritmo") == "SHA-256"), "")
        if dati.get("sha256") != sha or dati.get("pda") != urn \
                or dati.get("file") != _testo(doc.find(_q("NomeFile"))):
            problemi.append(f"Documento {i + 1}: impronta, nome o PdA diversi dalla foglia")
    ipda = radice.find(_q("IPdA"))
    if ipda is None:
        problemi.append("IPdA assente")
    else:
        dati = _verifica_prova_pda(ipda, radice_pda, "IPdA", problemi)
        if dati is not None and dati.get("ipda") != _testo(ipda.find(_q("Impronta"))):
            problemi.append("IPdA: impronta diversa dalla foglia")
    return problemi
