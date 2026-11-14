# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/validatore.py — validatore preventivo del versamento (R05 Q1;
REGOLE_SVILUPPO §7.3, §9.2; piano R03-R05 §6.1, §6.3).

Lo stesso codice gira:
  - nel servizio onboarding, ``POST /api/v1/configurazione/valida`` (con la
    configurazione letta dal database come il gateway, ``lettura.py``);
  - nella CLI ``tools/validatore/valida_pdv.py``, anche SENZA rete, con un
    «profilo esportato» (``esporta_profilo`` / ``ambiente_da_esportazione``).

Cosa controlla, con gli stessi codici del versamento:
  1. forma dell'indice (``controlla_forma``): i controlli che il gateway fa con
     lo schema Pydantic dell'indice e con ``controlla_indice_e_fonti``
     (VRS-001 schema, VRS-111 nomi ripetuti, VRS-114 allegati annidati,
     algoritmo d'impronta (VRS-001 come lo schema), VRS-101 file dichiarato ma non fornito,
     VRS-102 impronta diversa); i valori normativi vengono da ``normative.py``;
  2. estensioni dei nomi file ammesse dai formati della classe (SEC-006);
  3. regole della fase INDICE (RGL-101…106 con l'effetto risolto);
  4. con le informazioni dei file (``file_impronte``: impronta, algoritmo,
     dimensione, formato) le regole della fase FILE (RGL-104, RGL-105 e, da
     R08, RGL-107/108 del catalogo dei formati) e, per ogni file, l'esito del
     catalogo dei formati (``file``: AMMESSO, AVVISO, DA_CONVERTIRE, RIFIUTATO) e
     se il file verrebbe rifiutato;
  5. (R08) il livello di verifica delle firme che la verifica applicherà
     (``firme``): la verifica vera richiede il contenuto e resta al versamento.

Cosa NON controlla (dichiarato in ``controlli_non_eseguiti``): il formato
riconosciuto dal CONTENUTO (il validatore usa il formato indicato), eseguibili
e antivirus, duplicati e idempotenza, quote. Il gateway resta l'unico che
accetta un versamento: un esito CONFORME qui non lo garantisce.

Pacchetto puro: nessun I/O; il tempo arriva come argomento.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from libs.verumdoc_rules.catalogo_formati import NON_IDENTIFICATO, codice_esito, esito_formato
from libs.verumdoc_rules.catalogo_v1 import FORMATI_AMMESSI
from libs.verumdoc_rules.catalogo_v4 import CATALOGO, REGISTRO
from libs.verumdoc_rules.firme import FIRME_LIVELLO_VERIFICA, PAROLE_LIVELLO
from libs.verumdoc_rules.formati import estensioni_ammesse, motivo_estensione
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    valore_per_fotografia,
)
from libs.verumdoc_rules.normative import (
    ALGORITMI_IMPRONTA_AMMESSI,
    MODALITA_FORMAZIONE_AMMESSE,
    OGGETTO_LUNGHEZZA_MINIMA,
    REGISTRO_NORMATIVO,
    TIPI_DOCUMENTO_INDICE,
)
from libs.verumdoc_rules.profili import ProfiloMetadati
from libs.verumdoc_rules.regole import (
    AmbienteRegole,
    CatalogoRegole,
    DatiVersamento,
    DocumentoDaValutare,
    Effetto,
    EsitoRegole,
    Fase,
    Rilievo,
    rilievo_override,
)

#: Nomi dei metadati previsti dallo schema dell'indice (``MetadatiDocumento`` del
#: gateway): un test del gateway verifica che coincidano (una sola definizione
#: di fatto, controllata).
CAMPI_METADATI_INDICE: frozenset[str] = frozenset({
    "identificativo_documento", "modalita_formazione", "tipologia_documentale",
    "data_documento", "soggetto_produttore_id", "soggetto_produttore_nome", "oggetto",
    "lingua", "classificazione", "fascicolo_id", "numero_registrazione",
    "data_registrazione", "tipo_firma", "soggetto_destinatario", "codice_ipa", "aoo",
    "versione_documento", "metadati_extra",
})
_CAMPI_DATA = ("data_documento", "data_registrazione")
#: Limiti di lunghezza dei campi di testo dell'indice (sanitize_string del gateway).
_MAX_CLASSE, _MAX_ID, _MAX_NOME_FILE, _MAX_OGGETTO = 50, 100, 255, 1000
_ID_ACCORDO = re.compile(r"^[A-Za-z0-9._-]{1,50}$")

FORMATO_ESPORTAZIONE = "verumdoc-profilo-validatore-1"

CONTROLLI_NON_ESEGUITI = (
    "formato riconosciuto dal contenuto del file (si usa il formato indicato; la verifica "
    "lo identifica con PRONOM e veraPDF)",
    "verifica delle firme (eseguita dopo il versamento al livello indicato in «firme»)",
    "eseguibili e antivirus",
    "duplicati, chiave dell'unità documentaria e Idempotency-Key",
    "quote dell'ente",
)


def _predefinito(codice: str) -> Any:
    return REGISTRO_NORMATIVO[codice].predefinito


@dataclass
class EsitoValidazione:
    """Esito del validatore: stessi rilievi (codici e forma) del versamento."""

    rilievi: list[dict[str, Any]] = field(default_factory=list)
    avvisi: list[dict[str, Any]] = field(default_factory=list)
    regole_applicate: dict[str, Any] | None = None
    fasi_eseguite: list[str] = field(default_factory=list)
    #: R08: esito per file (catalogo dei formati, rifiutato sì/no)
    file: list[dict[str, Any]] = field(default_factory=list)
    firme: dict[str, Any] | None = None

    @property
    def conforme(self) -> bool:
        return not self.rilievi

    def come_dict(self) -> dict[str, Any]:
        return {
            "esito": "CONFORME" if self.conforme else "NON_CONFORME",
            "rilievi": self.rilievi, "avvisi": self.avvisi,
            "regole_applicate": self.regole_applicate,
            "fasi_eseguite": self.fasi_eseguite,
            "file": self.file, "firme": self.firme,
            "file_rifiutati": [f["nome_file"] for f in self.file if f["rifiutato"]],
            "controlli_non_eseguiti": list(CONTROLLI_NON_ESEGUITI),
        }


def rilievo(codice: str, messaggio: str, *, campo: str | None = None,
            documento: str | None = None, effetto: Effetto = Effetto.BLOCCA) -> dict[str, Any]:
    """Rilievo nella forma della risposta del versamento (``avviso`` del gateway)."""
    return {"codice": codice, "effetto": effetto.value, "campo": campo,
            "documento": documento, "messaggio": messaggio}


def da_rilievo(r: Rilievo) -> dict[str, Any]:
    return rilievo(r.regola, r.messaggio, campo=r.campo, documento=r.documento,
                   effetto=r.effetto)


@dataclass(frozen=True)
class Intestazione:
    """Ciò che serve per preparare l'ambiente: classe, accordo, override."""

    classe: str
    accordo: str | None
    override: Mapping[str, Any] | None


def _testo(valore: Any, massimo: int) -> bool:
    return isinstance(valore, str) and 0 < len(valore) <= massimo and "\x00" not in valore


def _data(valore: Any) -> bool:
    if not isinstance(valore, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valore):
        return False
    try:
        date.fromisoformat(valore)
    except ValueError:
        return False
    return True


def _forma_parametri(parametri: Any, doc_id: str) -> list[dict[str, Any]]:
    if parametri is None:
        return []
    if not isinstance(parametri, Mapping):
        return [rilievo("VRS-001", "«parametri» deve essere un oggetto", campo="parametri",
                        documento=doc_id)]
    errori: list[dict[str, Any]] = []
    modalita = parametri.get("modalita_formazione")
    if modalita is not None and modalita not in _predefinito(MODALITA_FORMAZIONE_AMMESSE):
        errori.append(rilievo("VRS-001", "modalita_formazione: attesa una lettera fra "
                              + ", ".join(_predefinito(MODALITA_FORMAZIONE_AMMESSE)),
                              campo="modalita_formazione", documento=doc_id))
    for nome in _CAMPI_DATA:
        if parametri.get(nome) is not None and not _data(parametri[nome]):
            errori.append(rilievo("VRS-001", f"{nome}: attesa una data AAAA-MM-GG",
                                  campo=nome, documento=doc_id))
    oggetto = parametri.get("oggetto")
    minimo = int(_predefinito(OGGETTO_LUNGHEZZA_MINIMA))
    if oggetto is not None and (not isinstance(oggetto, str)
                                or not minimo <= len(oggetto) <= _MAX_OGGETTO):
        errori.append(rilievo("VRS-001", f"oggetto: da {minimo} a {_MAX_OGGETTO} caratteri",
                              campo="oggetto", documento=doc_id))
    dest = parametri.get("soggetto_destinatario")
    if dest is not None and (not isinstance(dest, list)
                             or not all(isinstance(d, str) for d in dest)):
        errori.append(rilievo("VRS-001", "soggetto_destinatario: atteso un elenco di testi",
                              campo="soggetto_destinatario", documento=doc_id))
    extra = parametri.get("metadati_extra")
    if extra is not None and not isinstance(extra, Mapping):
        errori.append(rilievo("VRS-001", "metadati_extra: atteso un oggetto",
                              campo="metadati_extra", documento=doc_id))
    for nome in ("identificativo_documento", "tipologia_documentale", "soggetto_produttore_id",
                 "soggetto_produttore_nome", "lingua", "classificazione", "fascicolo_id",
                 "numero_registrazione", "tipo_firma", "codice_ipa", "aoo",
                 "versione_documento"):
        if parametri.get(nome) is not None and not isinstance(parametri[nome], str):
            errori.append(rilievo("VRS-001", f"{nome}: atteso un testo", campo=nome,
                                  documento=doc_id))
    return errori


def controlla_forma(indice: Any) -> tuple[Intestazione | None, list[dict[str, Any]]]:
    """Controlli di forma dell'indice (senza configurazione). L'intestazione è
    None se l'indice è troppo malformato per proseguire."""
    if not isinstance(indice, Mapping):
        return None, [rilievo("VRS-001", "L'indice deve essere un oggetto JSON")]
    errori: list[dict[str, Any]] = []
    classe = indice.get("classe_documentale")
    if not _testo(classe, _MAX_CLASSE):
        errori.append(rilievo("VRS-001", f"classe_documentale: testo da 1 a {_MAX_CLASSE} "
                                         "caratteri", campo="classe_documentale"))
    accordo = indice.get("accordo")
    if accordo is not None and not (isinstance(accordo, str) and _ID_ACCORDO.match(accordo)):
        errori.append(rilievo("VRS-001", "accordo: codice di 1-50 caratteri fra lettere, "
                                         "cifre e . _ -", campo="accordo"))
    override = indice.get("override")
    if override is not None and not isinstance(override, Mapping):
        errori.append(rilievo("VRS-001", "override: atteso un oggetto {motivo, valori}",
                              campo="override"))
    chiave = indice.get("chiave")
    if chiave is not None and (not isinstance(chiave, Mapping) or any(
            chiave.get(k) is not None and not isinstance(chiave.get(k), str)
            for k in ("numero", "anno", "registro"))):
        errori.append(rilievo("VRS-001", "chiave: oggetto {registro, anno, numero} di testi",
                              campo="chiave"))
    documenti = indice.get("documenti")
    if not isinstance(documenti, list) or not documenti:
        errori.append(rilievo("VRS-001", "documenti: almeno un documento", campo="documenti"))
        return None, errori
    tipi = _predefinito(TIPI_DOCUMENTO_INDICE)
    algoritmi = _predefinito(ALGORITMI_IMPRONTA_AMMESSI)
    annidati: list[str] = []
    nomi: list[str] = []
    for posizione, doc in enumerate(documenti):
        if not isinstance(doc, Mapping):
            errori.append(rilievo("VRS-001", f"documenti[{posizione}]: atteso un oggetto"))
            continue
        doc_id: str = str(doc["id"]) if isinstance(doc.get("id"), str) else f"#{posizione}"
        if not _testo(doc.get("id"), _MAX_ID):
            errori.append(rilievo("VRS-001", f"id: testo da 1 a {_MAX_ID} caratteri",
                                  campo="id", documento=doc_id))
        if doc.get("tipo") not in tipi:
            errori.append(rilievo("VRS-001", "tipo: uno fra " + ", ".join(tipi), campo="tipo",
                                  documento=doc_id))
        if not _testo(doc.get("nome_file"), _MAX_NOME_FILE):
            errori.append(rilievo("VRS-001", f"nome_file: testo da 1 a {_MAX_NOME_FILE} "
                                             "caratteri", campo="nome_file", documento=doc_id))
        else:
            nomi.append(doc["nome_file"])
        impronta = doc.get("hash")
        if not isinstance(impronta, Mapping) or not isinstance(impronta.get("impronta"), str):
            errori.append(rilievo("VRS-001", "hash: oggetto {impronta, algoritmo}",
                                  campo="hash", documento=doc_id))
        elif str(impronta.get("algoritmo") or "SHA-256") not in algoritmi:
            # come lo schema del gateway (pattern esatto): VRS-001
            errori.append(rilievo("VRS-001", "Algoritmo d'impronta non ammesso (ammessi: "
                                  + ", ".join(algoritmi) + ")", campo="hash.algoritmo",
                                  documento=doc_id))
        if doc.get("allegati"):
            annidati.append(doc_id)
        errori.extend(_forma_parametri(doc.get("parametri"), doc_id))
    if annidati:
        errori.append(rilievo("VRS-114", "Documenti annidati in «allegati» non ammessi: "
                                         + ", ".join(sorted(annidati))))
    doppi = sorted({n for n in nomi if nomi.count(n) > 1})
    if doppi:
        errori.append(rilievo("VRS-111", "Nome file dichiarato più volte nell'indice: "
                                         + ", ".join(doppi)))
    if not isinstance(classe, str):
        return None, errori
    return Intestazione(classe, accordo if isinstance(accordo, str) else None,
                        override if isinstance(override, Mapping) else None), errori


def dati_da_indice(indice: Mapping[str, Any],
                   file_info: Mapping[str, Mapping[str, Any]] | None = None,
                   campi_noti: frozenset[str] = CAMPI_METADATI_INDICE,
                   normalizza: Any = None) -> DatiVersamento:
    """Vista pura del versamento (come ``dati_per_regole`` del gateway) da un indice
    in forma JSON; ``file_info``: per nome file, ``dimensione`` e ``formato``."""
    documenti = []
    for doc in indice.get("documenti") or ():
        info = (file_info or {}).get(str(doc.get("nome_file")), {})
        parametri = doc.get("parametri")
        documenti.append(DocumentoDaValutare(
            id=str(doc.get("id")), tipo=str(doc.get("tipo")),
            nome_file=str(doc.get("nome_file")),
            metadati=dict(parametri) if isinstance(parametri, Mapping) else None,
            dimensione_byte=int(info["dimensione"]) if info.get("dimensione") is not None
            else None,
            formato_rilevato=str(info["formato"]) if info.get("formato") else None,
        ))
    extra: dict[str, Any] = {"normalizza_formato": normalizza} if normalizza else {}
    return DatiVersamento(classe=str(indice.get("classe_documentale")),
                          documenti=tuple(documenti), campi_noti=campi_noti, **extra)


def _normalizza_mime(mime: str) -> str:
    return mime.split(";", 1)[0].strip().lower()


def _controlla_file(indice: Mapping[str, Any],
                    file_info: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    errori = []
    for doc in indice.get("documenti") or ():
        nome = str(doc.get("nome_file"))
        info = file_info.get(nome)
        if info is None:
            errori.append(rilievo("VRS-101", f"File {nome!r} dichiarato ma non indicato fra i "
                                  "file", documento=str(doc.get("id"))))
            continue
        dichiarata = str((doc.get("hash") or {}).get("impronta") or "").lower()
        calcolata = str(info.get("impronta") or "").lower()
        alg_doc = str((doc.get("hash") or {}).get("algoritmo") or "SHA-256").upper()
        alg_file = str(info.get("algoritmo") or "SHA-256").upper()
        if calcolata and alg_doc == alg_file and calcolata != dichiarata:
            errori.append(rilievo("VRS-102", f"Hash {alg_doc} errato per {nome!r}",
                                  campo="hash", documento=str(doc.get("id"))))
    dichiarati = {str(d.get("nome_file")) for d in indice.get("documenti") or ()}
    in_piu = sorted(set(file_info) - dichiarati)
    if in_piu:
        errori.append(rilievo("VRS-104", "File non dichiarati nell'indice: " + ", ".join(in_piu)))
    return errori


def valida_versamento(indice: Mapping[str, Any], ambiente: AmbienteRegole, *,
                      catalogo: CatalogoRegole = CATALOGO,
                      file_info: Mapping[str, Mapping[str, Any]] | None = None,
                      rilievi_forma: list[dict[str, Any]] | None = None) -> EsitoValidazione:
    """Rilievi del versamento per un indice di forma valida (``controlla_forma``)
    con l'ambiente già preparato (classe, accordo, override, profilo)."""
    esito = EsitoValidazione(rilievi=list(rilievi_forma or ()))
    esito.fasi_eseguite.append("forma")
    # Estensioni ammesse dai formati della classe (SEC-006, come il gateway)
    blocca = Effetto(ambiente.valore("REGOLA.RGL-105.EFFETTO")) is Effetto.BLOCCA
    ammesse = estensioni_ammesse(ambiente.valore(FORMATI_AMMESSI), restrizione_attiva=blocca,
                                 normalizza=_normalizza_mime)
    for doc in indice.get("documenti") or ():
        motivo = motivo_estensione(str(doc.get("nome_file")), ammesse)
        if motivo:
            esito.rilievi.append(rilievo("SEC-006", motivo, campo="nome_file",
                                         documento=str(doc.get("id"))))
    dati = dati_da_indice(indice, file_info, normalizza=_normalizza_mime)
    regole: EsitoRegole = catalogo.valuta(dati, ambiente, Fase.INDICE)
    esito.fasi_eseguite.append("regole_indice")
    if file_info is not None:
        esito.rilievi.extend(_controlla_file(indice, file_info))
        regole = regole.unisci(catalogo.valuta(dati, ambiente, Fase.FILE))
        esito.fasi_eseguite.append("regole_file")
    esito.rilievi.extend(da_rilievo(r) for r in regole.bloccanti)
    esito.avvisi.extend(da_rilievo(r) for r in regole.avvisi)
    personalizzazione = rilievo_override(ambiente)
    if personalizzazione is not None:
        esito.avvisi.append(da_rilievo(personalizzazione))
    esito.regole_applicate = catalogo.fotografia(ambiente)
    esito.file = _esito_per_file(indice, file_info, ambiente, esito)
    if FIRME_LIVELLO_VERIFICA in ambiente.risolutore.registro:
        livello = str(ambiente.valore(FIRME_LIVELLO_VERIFICA))
        esito.firme = {"livello": livello, "descrizione": PAROLE_LIVELLO[livello],
                       "origine": ambiente.effettiva(FIRME_LIVELLO_VERIFICA).origine.value}
    return esito


def _esito_per_file(indice: Mapping[str, Any], file_info: Mapping[str, Mapping[str, Any]] | None,
                    ambiente: AmbienteRegole, esito: EsitoValidazione) -> list[dict[str, Any]]:
    """Per ogni file: esito del catalogo dei formati e se verrebbe rifiutato."""
    righe: list[dict[str, Any]] = []
    con_catalogo = codice_esito(NON_IDENTIFICATO) in ambiente.risolutore.registro
    for doc in indice.get("documenti") or ():
        doc_id, nome = str(doc.get("id")), str(doc.get("nome_file"))
        info = (file_info or {}).get(nome) or {}
        formato = str(info.get("formato") or doc.get("formato") or "") or None
        catalogo_file = None
        if con_catalogo and formato:
            catalogo_file = esito_formato(puid=None, mime=formato, pdfa_conforme=None,
                                          valore=ambiente.valore).come_dict()
        motivi = [r["messaggio"] for r in esito.rilievi if r.get("documento") == doc_id]
        avvisi = [r["messaggio"] for r in esito.avvisi if r.get("documento") == doc_id]
        righe.append({"documento": doc_id, "nome_file": nome, "formato": formato,
                      "catalogo": catalogo_file, "rifiutato": bool(motivi),
                      "motivi": motivi, "avvisi": avvisi})
    return righe


# ── Profilo esportato (validatore senza rete) ──────────────────────

def esporta_profilo(ambiente: AmbienteRegole, *, registro: RegistroImpostazioni = REGISTRO,
                    catalogo: CatalogoRegole = CATALOGO, classe: str, ente: str | None,
                    adesso: datetime) -> dict[str, Any]:
    """Configurazione effettiva di un contesto (ente, classe, accordo) in JSON, per
    validare senza rete. Non contiene segreti né dati di altri enti."""
    for codice in registro.codici():
        ambiente.effettiva(codice)
    return {
        "formato": FORMATO_ESPORTAZIONE,
        "catalogo_regole": catalogo.versione,
        "esportato_il": adesso.isoformat(),
        "ente": ente,
        "classe_documentale": classe,
        "accordo": ambiente.accordo.come_dict() if ambiente.accordo else None,
        "impostazioni": ambiente.impostazioni_consultate(),
        "profilo_metadati": ambiente.profilo.come_dict() if ambiente.profilo else None,
    }


class EsportazioneNonValida(ValueError):
    """Il file del profilo esportato non è leggibile o è di un'altra versione."""


def ambiente_da_esportazione(esportato: Mapping[str, Any], *, adesso: datetime,
                             override: Mapping[str, Any] | None = None,
                             registro: RegistroImpostazioni = REGISTRO,
                             catalogo: CatalogoRegole = CATALOGO
                             ) -> tuple[AmbienteRegole, list[dict[str, Any]]]:
    """Ambiente del motore da un profilo esportato; restituisce anche gli avvisi
    (es. catalogo diverso da quello di questa copia del validatore)."""
    from libs.verumdoc_rules.accordi import AccordoVersamento
    from libs.verumdoc_rules.override import valida_override

    if esportato.get("formato") != FORMATO_ESPORTAZIONE:
        raise EsportazioneNonValida("formato del profilo esportato non riconosciuto")
    avvisi: list[dict[str, Any]] = []
    if str(esportato.get("catalogo_regole")) != catalogo.versione:
        avvisi.append(rilievo("RGL-503", "Profilo esportato con il catalogo "
                              f"{esportato.get('catalogo_regole')}, validatore con il "
                              f"{catalogo.versione}: aggiornare il validatore o il profilo",
                              effetto=Effetto.AVVISA))
    livelli: dict[Livello, dict[str, Any]] = {}
    impostazioni = esportato.get("impostazioni")
    if not isinstance(impostazioni, Mapping):
        raise EsportazioneNonValida("profilo esportato senza impostazioni")
    for codice, voce in impostazioni.items():
        if codice not in registro or not isinstance(voce, Mapping):
            continue
        try:
            livello = Livello(str(voce.get("origine")))
        except ValueError as exc:
            raise EsportazioneNonValida(f"origine non valida per {codice}") from exc
        if livello is Livello.PREDEFINITO:
            if valore_per_fotografia(registro[codice].predefinito) == voce.get("valore"):
                continue
            livello = Livello.CONSERVATORE  # predefinito di un'altra versione: si conserva
        if livello is Livello.PDV:
            continue
        livelli.setdefault(livello, {})[codice] = voce.get("valore")
    try:
        accordo = (AccordoVersamento.da_dict(esportato["accordo"])
                   if esportato.get("accordo") else None)
        profilo = (ProfiloMetadati.da_dict(esportato["profilo_metadati"])
                   if esportato.get("profilo_metadati") else None)
    except (KeyError, TypeError, ValueError) as exc:
        raise EsportazioneNonValida(f"profilo esportato non leggibile: {exc}") from exc
    personalizzazione = valida_override(override, registro)
    if personalizzazione is not None:
        livelli[Livello.PDV] = dict(personalizzazione.valori)
    contesto = ContestoRisoluzione(adesso=adesso)
    ambiente = AmbienteRegole(Risolutore(registro, FornitoreDaDizionari(livelli)), contesto,
                              profilo, accordo=accordo, override=personalizzazione)
    return ambiente, avvisi


__all__ = [
    "CAMPI_METADATI_INDICE",
    "CONTROLLI_NON_ESEGUITI",
    "EsitoValidazione",
    "EsportazioneNonValida",
    "FORMATO_ESPORTAZIONE",
    "Intestazione",
    "ambiente_da_esportazione",
    "controlla_forma",
    "dati_da_indice",
    "esporta_profilo",
    "rilievo",
    "valida_versamento",
]
