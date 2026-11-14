# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Costruttore dell'IPdA (indice del PdA) di VerumDoc in tre strutture:
  - DI  : Documento Informatico (struttura base)
  - DAI : Documento Amministrativo Informatico (esteso con Allegato 5 e registro)
  - AGG : Aggregazione Documentale (fascicolo gerarchico)

ATTENZIONE (VD-C-071, decisione D03): la struttura, gli elementi e il namespace
qui sotto sono PROVVISORI di VerumDoc (R14, VD-C-096: namespace proprio del conservatore
``https://schemi.verumdoc.it/ipda/provvisorio/1``, mai un URI a nome di UNI o AgID).
NON sono verificati contro lo schema
ufficiale UNI 11386:2020, che il committente non ha ancora acquistato: l'IPdA
non si dichiara conforme. Il validatore pluggable ``libs/sincro/validatore.py``
lo verifica quando l'XSD ufficiale è caricato in ``libs/sincro/schemas/``
(README, «Schema UNI 11386»); l'adeguamento del costruttore segue l'XSD.

Sprint 2A FIX-012: rimosso RiferimentoStorage (S3 key interno) dall'XML distribuibile.

R11 (D07): i metadati dei profili riscontrati sull'Allegato 5 (Soggetti con i
ruoli, Tracciature, Riservato, Verifica, …) si emettono in
``MetadatiDocumento/MetadatiAllegato5`` con i NOMI dei metadati dell'Allegato 5
(dati dei profili, ``campi_allegato5``), strutture comprese, invece che come
testo JSON fra i metadati aggiuntivi. Il contenitore resta provvisorio.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from xml.dom.minidom import parseString
from xml.etree.ElementTree import Element, SubElement, tostring

from libs.shared.models import ModalitaFormazione
from libs.verumdoc_rules.profili import CampoProfilo, TipoCampo
from libs.verumdoc_rules.tipologie import campi_allegato5

# R14 (VD-C-096): namespace PROPRIO del conservatore, dichiarato come tale. Prima era
# ``urn:uni:uninfo:sincro:2020`` (con ``schemaLocation`` verso un XSD inesistente e un
# ``urn:agid:…`` per il versatore): URI inventati a nome di UNI e AgID, che facevano
# sembrare l'indice conforme a uno schema ufficiale che non è stato verificato. Finché
# l'XSD UNI 11386:2020 non è acquistato e il costruttore adeguato (VD-C-071) l'indice usa
# questo namespace, senza ``schemaLocation``, con un commento che lo dichiara provvisorio.
# Gli IPdA già conservati restano come sono (WORM); i lettori (ciclo-vita, verificatore)
# confrontano i nomi locali e leggono entrambi.
SINCRO_NS = "https://schemi.verumdoc.it/ipda/provvisorio/1"
SINCRO_VER = "2.0"
#: identificativo del versatore: l'id dell'ente in VerumDoc (schema del conservatore)
VERSATORE_SCHEMA = f"{SINCRO_NS}#id-ente"
#: namespace usati fino a R13b (solo per riconoscere gli IPdA storici)
NAMESPACE_STORICI = ("urn:uni:uninfo:sincro:2020",)
#: Revisione R14 (2): versione del FORMATO dell'IPdA, fissata nel record di costruzione del
#: PdA al primo tentativo (``costruzione_pda.formato_ipda``) e riusata a ogni ricostruzione:
#: un PdA iniziato con il codice di prima (IPdA già nel WORM con il namespace storico) si
#: ricostruisce con gli STESSI byte, invece di fallire con AIP-STORAGE-001.
FORMATO_ATTUALE = "verumdoc-ipda-provvisorio-1"
#: formato fino a R13b: un record di costruzione senza ``formato_ipda`` è di prima
FORMATO_STORICO = "sincro-2020-provvisorio-r13"
FORMATI_IPDA = (FORMATO_ATTUALE, FORMATO_STORICO)
_STORICO_XSI = "http://www.w3.org/2001/XMLSchema-instance"
_STORICO_SCHEMA_LOC = "urn:uni:uninfo:sincro:2020 sincro_v2.0.xsd"
_STORICO_VERSATORE = "urn:agid:sincro:versatore"


def formato_della_costruzione(costruzione: dict | None) -> str:
    """Il formato dell'IPdA di un record di costruzione (assente = di prima di R14)."""
    formato = (costruzione or {}).get("formato_ipda") or FORMATO_STORICO
    if formato not in FORMATI_IPDA:
        raise ValueError(f"formato dell'IPdA sconosciuto: {formato!r}")
    return str(formato)


def _come_storico(root: Element) -> None:
    """Gli attributi e il versatore dell'IPdA di R13b, nello stesso ordine (byte uguali)."""
    versione, tipo = root.get("versione"), root.get("tipo")
    root.attrib.clear()
    root.set("xmlns", NAMESPACE_STORICI[0])
    root.set("xmlns:xsi", _STORICO_XSI)
    root.set("xsi:schemaLocation", _STORICO_SCHEMA_LOC)
    root.set("versione", str(versione))
    root.set("tipo", str(tipo))
    versatore = root.find("Intestazione/VersatoreSchema")
    if versatore is not None:
        versatore.text = _STORICO_VERSATORE
DICHIARAZIONE = (" Indice del PdA nel formato PROVVISORIO di VerumDoc (namespace del "
                 "conservatore " + SINCRO_NS + "): NON è lo schema UNI 11386:2020 e non è "
                 "dichiarato conforme (VD-C-071, VD-C-096). ")


def _sub(parent: Element, tag: str, text: str | None = None) -> Element:
    el = SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _intestazione(root: Element, aip_id: str, pdv_id: str,
                  tenant_id: str, now: datetime, tipo: str) -> None:
    """Sezione Intestazione comune a tutte le tipologie."""
    i = _sub(root, "Intestazione")
    _sub(i, "IdentificativoAIP",   aip_id)
    _sub(i, "IdentificativoPdV",   pdv_id)
    _sub(i, "TipologiaOggetto",    tipo)    # DI / DAI / AGG
    _sub(i, "ConservatoreAIP",     "VerumDoc S.r.l.")
    _sub(i, "DataVersamento",      now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    _sub(i, "VersatoreId",         tenant_id)
    _sub(i, "VersatoreSchema",     VERSATORE_SCHEMA)
    _sub(i, "VersioneSInCRO",      SINCRO_VER)


def _chiave_documentale(parent: Element, chiave: dict | None) -> None:
    if not chiave:
        return
    ch = _sub(parent, "ChiaveDocumentale")
    if chiave.get("anno"):
        _sub(ch, "Anno",     str(chiave["anno"]))
    if chiave.get("numero"):
        _sub(ch, "Numero",   str(chiave["numero"]))
    if chiave.get("registro"):
        _sub(ch, "Registro", str(chiave["registro"]))


class MetadatiIPdANonValidi(ValueError):
    """I metadati del versamento non bastano a comporre l'IPdA: nessun valore
    viene inventato (VD-C-037). Il chiamante lo tratta come errore permanente."""


def _modalita_formazione(meta: dict) -> str:
    """Lettera a/b/c/d dal metadato versato; errore se manca o non è ammessa."""
    valore = meta.get("modalita_formazione")
    if isinstance(valore, ModalitaFormazione):
        return valore.value
    try:
        return ModalitaFormazione(valore).value
    except ValueError:
        raise MetadatiIPdANonValidi(
            f"modalita_formazione mancante o non ammessa: {valore!r} "
            f"(valori ammessi: {', '.join(m.value for m in ModalitaFormazione)})"
        ) from None


def _allegato5(parent: Element, meta: dict, nome_file: str | None = None) -> None:
    """Inserisce i campi Allegato 5 AGID nel nodo MetadatiDocumento."""
    if not meta:
        return
    modalita = _modalita_formazione(meta)
    m = _sub(parent, "MetadatiDocumento")
    _sub(m, "IdentificativoDocumento",  meta.get("identificativo_documento", ""))
    _sub(m, "ModalitaFormazione",       modalita)
    _sub(m, "TipologiaDocumentale",     meta.get("tipologia_documentale", ""))
    _sub(m, "DataDocumento",            str(meta.get("data_documento", "")))
    # Soggetto produttore
    sp = _sub(m, "SoggettoProduttore")
    _sub(sp, "Identificativo",          meta.get("soggetto_produttore_id", ""))
    _sub(sp, "Denominazione",           meta.get("soggetto_produttore_nome", ""))
    _sub(m, "Oggetto",                  meta.get("oggetto", ""))
    _sub(m, "Lingua",                   meta.get("lingua", "it"))
    # Campi opzionali
    if meta.get("classificazione"):
        _sub(m, "Classificazione",      meta["classificazione"])
    if meta.get("fascicolo_id"):
        _sub(m, "FascicoloId",          meta["fascicolo_id"])
    if meta.get("numero_registrazione"):
        _sub(m, "NumeroRegistrazione",  meta["numero_registrazione"])
    if meta.get("data_registrazione"):
        _sub(m, "DataRegistrazione",    str(meta["data_registrazione"]))
    if meta.get("tipo_firma"):
        _sub(m, "TipoFirma",            meta["tipo_firma"])
    if meta.get("codice_ipa"):
        _sub(m, "CodiceIPA",            meta["codice_ipa"])
    if meta.get("aoo"):
        _sub(m, "AOO",                  meta["aoo"])
    # Destinatari
    for dest in meta.get("soggetto_destinatario", []):
        _sub(m, "SoggettoDestinatario", str(dest))
    emessi = _metadati_allegato5(m, meta, nome_file)
    _metadati_aggiuntivi(m, meta, emessi)


# ── R11 (D07): metadati dei profili con i nomi dell'Allegato 5 ─────────────

def _nomi_xml(percorso: str) -> list[str]:
    """Nome del metadato dell'Allegato 5 → elementi (``A.B`` = B dentro A); di più
    alternative (``X / Y``) vale la prima; solo lettere e cifre."""
    parti = [re.sub(r"[^A-Za-z0-9]", "", p) for p in percorso.split(" / ")[0].split(".")]
    return [p for p in parti if p]


def _figlio(parent: Element, nome: str) -> Element:
    esistente = parent.find(nome)
    return esistente if esistente is not None else SubElement(parent, nome)


def _valore_xml(el: Element, campo: CampoProfilo | None, valore: Any) -> None:
    if campo is not None and campo.tipo is TipoCampo.ELENCO_STRUTTURE and isinstance(valore, list):
        for n, elemento in enumerate(valore, start=1):
            figlio = SubElement(el, "Elemento")
            figlio.set("n", str(n))
            _oggetto_xml(figlio, campo.sottocampi, elemento)
        return
    if campo is not None and campo.tipo is TipoCampo.STRUTTURA and isinstance(valore, dict):
        _oggetto_xml(el, campo.sottocampi, valore)
        return
    if isinstance(valore, list) and all(not isinstance(v, dict | list) for v in valore):
        for v in valore:
            _sub(el, "Valore", _testo_metadato(v))
        return
    el.text = _testo_metadato(valore)


def _oggetto_xml(el: Element, sottocampi: tuple[CampoProfilo, ...], oggetto: Any) -> None:
    """Sottocampi con il loro nome dell'Allegato 5; chiavi non previste conservate come
    ``Metadato nome=…`` (nessun dato versato si perde, VD-C-094)."""
    if not isinstance(oggetto, dict):
        el.text = _testo_metadato(oggetto)
        return
    per_nome = {c.nome: c for c in sottocampi}
    for chiave in sorted(oggetto):
        valore = oggetto[chiave]
        if valore is None:
            continue
        campo = per_nome.get(chiave)
        nomi = _nomi_xml(campo.allegato5) if campo is not None and campo.allegato5 else []
        if nomi:
            figlio = el
            for nome in nomi:
                figlio = SubElement(figlio, nome)
        else:
            figlio = SubElement(el, "Metadato")
            figlio.set("nome", str(chiave))
        _valore_xml(figlio, campo, valore)


def _metadati_allegato5(parent: Element, meta: dict, nome_file: str | None) -> frozenset[str]:
    """Emette ``MetadatiAllegato5`` con i metadati dei profili R11 presenti (senza un
    elemento proprio in ``MetadatiDocumento``); restituisce i nomi emessi. Se c'è
    almeno uno di questi metadati e manca ``nome_documento`` (campo calcolato), il
    NomeDelDocumento è il nome del file versato."""
    mappa = campi_allegato5()
    chiavi = sorted(k for k in meta if k in mappa and k not in _CAMPI_CON_ELEMENTO
                    and meta[k] is not None and meta[k] != "")
    if not chiavi:
        return frozenset()
    valori = dict(meta)
    if "nome_documento" in mappa and not valori.get("nome_documento") and nome_file:
        valori["nome_documento"] = nome_file
        chiavi = sorted({*chiavi, "nome_documento"})
    sezione = _sub(parent, "MetadatiAllegato5")
    sezione.set("fonte", "Linee guida AgID 2021, Allegato 5 (nomi dei metadati)")
    for chiave in sorted(chiavi, key=lambda k: (mappa[k].allegato5 or "", k)):
        campo = mappa[chiave]
        *gruppi, foglia = _nomi_xml(campo.allegato5 or chiave) or [chiave]
        el = sezione
        for nome in gruppi:          # gruppi condivisi (es. DatiDiRegistrazione, Verifica)
            el = _figlio(el, nome)
        _valore_xml(SubElement(el, foglia), campo, valori[chiave])
    return frozenset(chiavi)


# R03 (VD-C-094): nessun metadato versato si perde. Ciò che non ha un elemento
# proprio qui sopra (metadati del profilo della classe, chiavi non previste,
# contenuto di ``metadati_extra``, versione del documento) si emette in
# ``MetadatiAggiuntivi/Metadato`` con il nome e l'origine. Ordine totale per
# origine e nome (REGOLE §12.8): a parità di metadati, stesso IPdA.
#: Metadati con un elemento proprio in ``MetadatiDocumento``.
_CAMPI_CON_ELEMENTO = frozenset({
    "identificativo_documento", "modalita_formazione", "tipologia_documentale",
    "data_documento", "soggetto_produttore_id", "soggetto_produttore_nome", "oggetto",
    "lingua", "classificazione", "fascicolo_id", "numero_registrazione",
    "data_registrazione", "tipo_firma", "codice_ipa", "aoo", "soggetto_destinatario",
})
#: Contenitore dei metadati liberi dichiarati come tali dal versante.
_CONTENITORE_LIBERO = "metadati_extra"


def _testo_metadato(valore: object) -> str:
    if isinstance(valore, bool):
        return "true" if valore else "false"
    if isinstance(valore, dict | list):
        return json.dumps(valore, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return str(valore)


def _metadati_aggiuntivi(parent: Element, meta: dict,
                         emessi: frozenset[str] = frozenset()) -> None:
    """Metadati senza elemento proprio: ``<Metadato nome="…" origine="…">valore</Metadato>``."""
    voci: list[tuple[str, str, str]] = []
    for nome, valore in meta.items():
        if nome in _CAMPI_CON_ELEMENTO or nome in emessi or nome == _CONTENITORE_LIBERO \
                or valore is None:
            continue
        voci.append(("parametri", str(nome), _testo_metadato(valore)))
    liberi = meta.get(_CONTENITORE_LIBERO)
    if isinstance(liberi, dict):
        voci += [(_CONTENITORE_LIBERO, str(n), _testo_metadato(v))
                 for n, v in liberi.items() if v is not None]
    if not voci:
        return
    contenitore = _sub(parent, "MetadatiAggiuntivi")
    for origine, nome, testo in sorted(voci):
        el = _sub(contenitore, "Metadato", testo)
        el.set("nome", nome)
        el.set("origine", origine)


def _nome_principale(documenti: list[dict]) -> str | None:
    principale = next((d for d in documenti if d.get("tipo", "PRINCIPALE") == "PRINCIPALE"),
                      documenti[0] if documenti else None)
    return str(principale.get("nome_file") or "") or None if principale else None


def _file_group(parent: Element, documenti: list[dict]) -> None:
    """FileGroup SInCRO con tutti i file dell'AIP."""
    fg = _sub(parent, "FileGroup")
    for i, doc in enumerate(documenti):
        f = _sub(fg, "File")
        _sub(f, "NomeFile",          doc.get("nome_file", ""))
        _sub(f, "TipoFile",          doc.get("tipo", "PRINCIPALE"))
        _sub(f, "FormatoMIME",       doc.get("formato", "application/octet-stream"))
        _sub(f, "DimensioneBytes",   str(doc.get("dimensione_bytes", 0)))
        _sub(f, "Ordine",            str(i + 1))
        # Hash
        h = _sub(f, "Hash")
        _sub(h, "Impronta",          doc.get("hash_sha256", ""))
        _sub(h, "Algoritmo",         "SHA-256")
        if doc.get("hash_sha512"):
            h2 = _sub(f, "Hash")
            _sub(h2, "Impronta",     doc["hash_sha512"])
            _sub(h2, "Algoritmo",    "SHA-512")
        # Sprint 2A FIX-012: RiferimentoStorage (S3 key) rimosso.
        # Il SInCRO XML è un documento esterno distribuibile — non deve
        # contenere riferimenti a infrastruttura interna (bucket/key S3).


# ─── Tipologia DI — Documento Informatico ────────────────────────────

def _build_di(
    aip_id: str, pdv_id: str, tenant_id: str,
    classe_documentale: str, documenti: list[dict],
    chiave: dict | None, now: datetime,
    allegato5: dict | None = None,
) -> Element:
    """
    Documento Informatico (DI) — struttura SInCRO base.
    Per documenti tecnici: immagini, file tecnici, allegati non amministrativi.
    """
    root = Element("SInCRO")
    root.set("xmlns", SINCRO_NS)
    root.set("versione", SINCRO_VER)
    root.set("tipo", "DI")

    _intestazione(root, aip_id, pdv_id, tenant_id, now, "DI")

    desc = _sub(root, "Descrizione")
    _sub(desc, "ClasseDocumentale", classe_documentale)
    _chiave_documentale(desc, chiave)
    _sub(desc, "NumeroDocumenti",   str(len(documenti)))
    if allegato5:
        _allegato5(desc, allegato5, _nome_principale(documenti))

    _file_group(root, documenti)
    return root


# ─── Tipologia DAI — Documento Amministrativo Informatico ────────────

def _build_dai(
    aip_id: str, pdv_id: str, tenant_id: str,
    classe_documentale: str, documenti: list[dict],
    chiave: dict | None, now: datetime,
    allegato5: dict | None = None,
) -> Element:
    """
    Documento Amministrativo Informatico (DAI) — struttura SInCRO estesa.
    Per documenti PA: fatture, contratti, delibere, determinazioni, verbali, decreti.
    Include tutti i 38 campi Allegato 5 AGID + sezione Registro.
    """
    root = Element("SInCRO")
    root.set("xmlns", SINCRO_NS)
    root.set("versione", SINCRO_VER)
    root.set("tipo", "DAI")

    _intestazione(root, aip_id, pdv_id, tenant_id, now, "DAI")

    desc = _sub(root, "Descrizione")
    _sub(desc, "ClasseDocumentale", classe_documentale)
    _chiave_documentale(desc, chiave)
    _sub(desc, "NumeroDocumenti",   str(len(documenti)))

    # Allegato 5 — obbligatorio per DAI: nessun segnaposto inventato
    if not allegato5:
        raise MetadatiIPdANonValidi(
            "IPdA di tipo DAI senza metadati Allegato 5 del documento principale"
        )
    _allegato5(desc, allegato5, _nome_principale(documenti))

    # Sezione Registro — specifica DAI
    reg = _sub(root, "Registro")
    if chiave:
        _sub(reg, "NumeroProtocollo", chiave.get("numero", ""))
        _sub(reg, "Anno",             chiave.get("anno", ""))
        _sub(reg, "Registro",         chiave.get("registro", ""))
    if allegato5:
        if allegato5.get("numero_registrazione"):
            _sub(reg, "NumeroRegistrazione", allegato5["numero_registrazione"])
        # R11: nei profili v2 codice IPA e AOO stanno nel soggetto
        # «Amministrazione Che Effettua La Registrazione»
        amministrazione = _amministrazione_registrante(allegato5)
        codice_ipa = allegato5.get("codice_ipa") or amministrazione.get("codice_ipa")
        aoo = allegato5.get("aoo") or amministrazione.get("aoo")
        if codice_ipa:
            _sub(reg, "CodiceIPA",           codice_ipa)
        if aoo:
            _sub(reg, "AOO",                 aoo)

    # Sezione Firma — stub (TSA in Fase 5)
    firma = _sub(root, "Firma")
    _sub(firma, "TipoFirma",    allegato5.get("tipo_firma", "N/A") if allegato5 else "N/A")
    _sub(firma, "StatoFirma",   "DA_APPORRE")  # sarà APPOSTA dopo TSA Fase 5
    _sub(firma, "NotaTSA",      "TSA eIDAS - implementazione Fase 5")

    _file_group(root, documenti)
    return root


def _amministrazione_registrante(meta: dict) -> dict:
    soggetti = meta.get("soggetti")
    if not isinstance(soggetti, list):
        return {}
    return next((s for s in soggetti if isinstance(s, dict) and s.get("ruolo") ==
                 "Amministrazione Che Effettua La Registrazione"), {})


# ─── Tipologia AGG — Aggregazione Documentale ────────────────────────

def _build_agg(
    aip_id: str, pdv_id: str, tenant_id: str,
    classe_documentale: str, documenti: list[dict],
    chiave: dict | None, now: datetime,
    allegato5: dict | None = None,
    fascicolo_id: str | None = None,
    titolo_fascicolo: str | None = None,
) -> Element:
    """
    Aggregazione Documentale (AGG) — struttura SInCRO gerarchica.
    Per fascicoli, pratiche, dossier contenenti N documenti correlati.
    """
    root = Element("SInCRO")
    root.set("xmlns", SINCRO_NS)
    root.set("versione", SINCRO_VER)
    root.set("tipo", "AGG")

    _intestazione(root, aip_id, pdv_id, tenant_id, now, "AGG")

    # Fascicolo padre
    fasc = _sub(root, "Fascicolo")
    _sub(fasc, "IdentificativoFascicolo", fascicolo_id or aip_id)
    _sub(fasc, "TitoloFascicolo",         titolo_fascicolo or classe_documentale)
    _sub(fasc, "DataApertura",            now.strftime("%Y-%m-%d"))
    _sub(fasc, "ClasseDocumentale",       classe_documentale)
    _chiave_documentale(fasc, chiave)
    _sub(fasc, "NumeroDocumenti",         str(len(documenti)))

    if allegato5:
        _allegato5(fasc, allegato5, _nome_principale(documenti))

    # Documenti figli
    figli = _sub(root, "DocumentiFiglio")
    for doc in documenti:
        figlio = _sub(figli, "Documento")
        _sub(figlio, "IdentificativoDocumento", doc.get("id", ""))
        _sub(figlio, "TipoDocumento",            doc.get("tipo", "PRINCIPALE"))
        _sub(figlio, "NomeFile",                 doc.get("nome_file", ""))
        _sub(figlio, "FormatoMIME",              doc.get("formato", "application/octet-stream"))
        _sub(figlio, "DimensioneBytes",          str(doc.get("dimensione_bytes", 0)))
        h = _sub(figlio, "Hash")
        _sub(h, "Impronta",  doc.get("hash_sha256", ""))
        _sub(h, "Algoritmo", "SHA-256")
        # Sprint 2A FIX-012: RiferimentoStorage rimosso (info interna)

    _file_group(root, documenti)
    return root


# ─── Tipologia SInCRO dalla classe documentale ───────────────────────

#: Tipologie di IPdA ammesse (stessi valori dell'enum ``tipologia_xml`` delle classi).
TIPOLOGIE_IPDA: frozenset[str] = frozenset({"DI", "DAI", "AGG"})


def tipologia_ipda(tipo_xml: object) -> str:
    """
    Tipologia dell'IPdA come configurata nella classe documentale
    (``classi_documentali.tipologia_xml``, REGOLE §8.8: dato, non codice).

    R03 (richiesta del coordinatore, rapporto a2 §6.1): prima la tipologia si
    indovinava dal NOME della classe (elenchi fissi «fattura → DAI»,
    «fascicolo → AGG») e la colonna non era letta. Ora la colonna è l'unica
    fonte; assente o non valida → ``MetadatiIPdANonValidi`` (nessun ripiego).
    """
    valore = str(getattr(tipo_xml, "value", tipo_xml) or "").strip().upper()
    if valore not in TIPOLOGIE_IPDA:
        raise MetadatiIPdANonValidi(
            f"Tipologia dell'IPdA della classe documentale non valida: {tipo_xml!r} "
            f"(ammesse: {', '.join(sorted(TIPOLOGIE_IPDA))})"
        )
    return valore


def verifica_metadati_ipda(classe_documentale: str, allegato5: dict | None,
                           tipo_xml: object) -> str:
    """Controllo preventivo (prima di scrivere byte sullo storage WORM): i
    metadati bastano per l'IPdA della tipologia della classe? Restituisce la
    tipologia; solleva ``MetadatiIPdANonValidi`` con lo stesso criterio di
    ``build_sincro_xml``."""
    tipo = tipologia_ipda(tipo_xml)
    if tipo == "DAI" and not allegato5:
        raise MetadatiIPdANonValidi(
            "IPdA di tipo DAI senza metadati Allegato 5 del documento principale"
        )
    if allegato5:
        _modalita_formazione(allegato5)
    return tipo


# ─── Entry point principale ───────────────────────────────────────────

def build_sincro_xml(
    aip_id: str,
    pdv_id: str,
    tenant_id: str,
    classe_documentale: str,
    documenti: list[dict],
    chiave: dict | None = None,
    data_versamento: datetime | None = None,
    allegato5: dict | None = None,
    tipo_xml: object = None,              # tipologia della classe (DI/DAI/AGG): obbligatoria
    fascicolo_id: str | None = None,
    titolo_fascicolo: str | None = None,
    formato: str = FORMATO_ATTUALE,
) -> bytes:
    """
    Costruisce il SInCRO XML nella tipologia della classe documentale.

    Args:
        tipo_xml: tipologia (DI, DAI, AGG) da ``classi_documentali.tipologia_xml``.
            Obbligatoria: senza, ``MetadatiIPdANonValidi`` (nessuna deduzione dal nome).
        allegato5: dict con i campi MetadatiDocumento del documento principale.
    """
    now  = data_versamento or datetime.now(UTC)
    tipo = tipologia_ipda(tipo_xml)

    if tipo == "AGG":
        root = _build_agg(
            aip_id, pdv_id, tenant_id, classe_documentale,
            documenti, chiave, now, allegato5,
            fascicolo_id, titolo_fascicolo,
        )
    elif tipo == "DAI":
        root = _build_dai(
            aip_id, pdv_id, tenant_id, classe_documentale,
            documenti, chiave, now, allegato5,
        )
    else:  # DI
        root = _build_di(
            aip_id, pdv_id, tenant_id, classe_documentale,
            documenti, chiave, now, allegato5,
        )

    if formato not in FORMATI_IPDA:
        raise ValueError(f"formato dell'IPdA sconosciuto: {formato!r}")
    if formato == FORMATO_STORICO:
        _come_storico(root)
    raw = tostring(root, encoding="unicode")
    dom = parseString(raw)  # noqa: S318 — XML prodotto qui, mai ricevuto
    if formato == FORMATO_ATTUALE:
        dom.insertBefore(dom.createComment(DICHIARAZIONE), dom.documentElement)
    return dom.toprettyxml(indent="  ", encoding="UTF-8")


def compute_sincro_hash(sincro_xml: bytes) -> str:
    return hashlib.sha256(sincro_xml).hexdigest()
