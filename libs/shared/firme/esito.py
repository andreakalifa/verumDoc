# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme/esito.py — esito NORMALIZZATO della verifica di una firma
(R08, decisione D01; VD-C-073).

Ogni verificatore (EU DSS nel servizio ``verifica-firme``, pyHanko come
ripiego, la sola rilevazione) restituisce la stessa forma, così validazione,
costruzione del pacchetto, ciclo di vita e console non sanno chi ha verificato:

  - per ogni firma: firmatario, certificato (soggetto, emittente, seriale,
    validità, impronta), validità crittografica, catena fino a una trust
    anchor, stato di revoca, data dichiarata, formato (CAdES, PAdES, XAdES,
    ASiC), indicazione eIDAS (ETSI EN 319 102-1: TOTAL_PASSED, INDETERMINATE,
    TOTAL_FAILED) con la sotto-indicazione e i motivi IN PAROLE;
  - per il file: formato della firma, livello di verifica eseguito,
    verificatore, istante e rapporto grezzo del verificatore (troncato).

Il giudizio «basta per il livello richiesto?» NON sta qui: è una regola del
motore (``libs/verumdoc_rules/firme.py``, RGL-109) applicata con il suo effetto.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Lunghezza massima del rapporto grezzo conservato (caratteri).
RAPPORTO_GREZZO_MAX = 64 * 1024


class Indicazione(StrEnum):
    """Indicazione principale ETSI EN 319 102-1 (eIDAS)."""

    TOTAL_PASSED = "TOTAL_PASSED"
    INDETERMINATE = "INDETERMINATE"
    TOTAL_FAILED = "TOTAL_FAILED"


#: Dalla peggiore alla migliore (l'esito di un file è quello della firma peggiore).
_ORDINE_INDICAZIONI = (Indicazione.TOTAL_FAILED, Indicazione.INDETERMINATE,
                       Indicazione.TOTAL_PASSED)


class FormatoFirma(StrEnum):
    CADES = "CAdES"
    PADES = "PAdES"
    XADES = "XAdES"
    ASIC = "ASiC"
    NESSUNO = "NESSUNO"


class StatoRevoca(StrEnum):
    BUONO = "BUONO"
    REVOCATO = "REVOCATO"
    SCONOSCIUTO = "SCONOSCIUTO"          # informazioni di revoca richieste ma assenti
    NON_CONTROLLATO = "NON_CONTROLLATO"  # livello che non le chiede


class LivelloVerifica(StrEnum):
    """Livello di verifica (parametro ``FIRME_LIVELLO_VERIFICA``), dal minimo."""

    NESSUNA = "NESSUNA"
    RILEVATA = "RILEVATA"
    CRITTOGRAFICA = "CRITTOGRAFICA"
    CON_REVOCHE = "CON_REVOCHE"

    @property
    def rango(self) -> int:
        return list(LivelloVerifica).index(self)


#: Sotto-indicazioni usate (ETSI EN 319 102-1 §5.1.3, più FORMATO_NON_SUPPORTATO
#: e NON_VERIFICATA, propri del servizio, dichiarati nella documentazione).
SOTTO_HASH = "HASH_FAILURE"
SOTTO_FIRMA = "SIG_CRYPTO_FAILURE"
SOTTO_REVOCATO = "REVOKED"
SOTTO_REVOCATO_SENZA_POE = "REVOKED_NO_POE"
SOTTO_CATENA = "NO_CERTIFICATE_CHAIN_FOUND"
SOTTO_CATENA_GENERICO = "CERTIFICATE_CHAIN_GENERAL_FAILURE"
SOTTO_REVOCA_ASSENTE = "TRY_LATER"
SOTTO_SCADUTO = "OUT_OF_BOUNDS_NO_POE"
SOTTO_FORMATO = "FORMAT_FAILURE"
SOTTO_NON_SUPPORTATO = "FORMATO_NON_SUPPORTATO"
SOTTO_NON_VERIFICATA = "NON_VERIFICATA"
SOTTO_DATI_ASSENTI = "SIGNED_DATA_NOT_FOUND"

#: Spiegazione IN PAROLE di ogni sotto-indicazione (console, RdV, errori).
PAROLE: dict[str, str] = {
    SOTTO_HASH: "il documento è stato modificato dopo la firma",
    SOTTO_FIRMA: "la firma non corrisponde alla chiave del certificato",
    SOTTO_REVOCATO: "il certificato del firmatario è revocato",
    SOTTO_REVOCATO_SENZA_POE: ("il certificato del firmatario risulta revocato e non c'è una "
                               "marca temporale che provi la firma prima della revoca"),
    SOTTO_CATENA: "il certificato non risale a un prestatore fidato (Trusted List)",
    SOTTO_CATENA_GENERICO: "la catena dei certificati non è verificabile",
    SOTTO_REVOCA_ASSENTE: "lo stato di revoca del certificato non è disponibile",
    SOTTO_SCADUTO: "il certificato era scaduto o non ancora valido alla data di verifica",
    SOTTO_FORMATO: "la busta di firma non è leggibile",
    SOTTO_NON_SUPPORTATO: "formato di firma non supportato dal verificatore in uso",
    SOTTO_NON_VERIFICATA: "firma rilevata ma non verificata (livello «rilevata»)",
    SOTTO_DATI_ASSENTI: ("firma staccata non verificabile: il documento firmato non è "
                         "associato alla firma (nel PdV va versato accanto con lo stesso nome "
                         "senza «.p7s», es. fattura.xml e fattura.xml.p7s; nel ciclo di vita "
                         "si carica il .p7m o il PDF firmato)"),
}


def in_parole(sottoindicazione: str | None) -> str | None:
    if sottoindicazione is None:
        return None
    return PAROLE.get(sottoindicazione, sottoindicazione.replace("_", " ").lower())


def codice_fiscale(numero_seriale: Any) -> str | None:
    """CF dal serialNumber del soggetto: «TINIT-RSSMRA…» (ETSI EN 319 412-1) o il
    solo CF; None se assente o di un'altra forma (es. «IDCIT-…», «VATIT-…»)."""
    if not numero_seriale:
        return None
    valore = str(numero_seriale).strip().upper()
    if valore.startswith("TINIT-"):
        valore = valore[6:]
    if len(valore) == 16 and valore.isalnum():
        return valore
    return None


@dataclass(frozen=True)
class Certificato:
    soggetto: str
    emittente: str
    seriale: str
    valido_dal: str | None = None
    valido_al: str | None = None
    impronta_sha256: str | None = None
    #: revisione X3: codice fiscale del titolare (serialNumber del soggetto
    #: «TINIT-<CF>», ETSI EN 319 412-1), maiuscolo; None se il certificato non lo porta
    codice_fiscale: str | None = None

    def come_dict(self) -> dict[str, Any]:
        return {"soggetto": self.soggetto, "emittente": self.emittente,
                "seriale": self.seriale, "valido_dal": self.valido_dal,
                "valido_al": self.valido_al, "impronta_sha256": self.impronta_sha256,
                "codice_fiscale": self.codice_fiscale}

    @classmethod
    def da_dict(cls, d: Mapping[str, Any] | None) -> Certificato | None:
        if not isinstance(d, Mapping):
            return None
        return cls(str(d.get("soggetto") or ""), str(d.get("emittente") or ""),
                   str(d.get("seriale") or ""), d.get("valido_dal"), d.get("valido_al"),
                   d.get("impronta_sha256"), codice_fiscale(d.get("codice_fiscale")))


@dataclass(frozen=True)
class FirmaVerificata:
    """Una firma del file con l'esito normalizzato."""

    formato: FormatoFirma
    indicazione: Indicazione
    firmatario: str | None = None
    certificato: Certificato | None = None
    #: None = non verificata (livello RILEVATA)
    crittografia_valida: bool | None = None
    catena_attendibile: bool | None = None
    trust_anchor: str | None = None
    revoca: StatoRevoca = StatoRevoca.NON_CONTROLLATO
    data_firma: str | None = None
    sottoindicazione: str | None = None
    motivi: tuple[str, ...] = ()

    def come_dict(self) -> dict[str, Any]:
        return {
            "formato": self.formato.value, "indicazione": self.indicazione.value,
            "sottoindicazione": self.sottoindicazione, "firmatario": self.firmatario,
            "certificato": self.certificato.come_dict() if self.certificato else None,
            "crittografia_valida": self.crittografia_valida,
            "catena_attendibile": self.catena_attendibile, "trust_anchor": self.trust_anchor,
            "revoca": self.revoca.value, "data_firma": self.data_firma,
            "motivi": list(self.motivi),
        }

    @classmethod
    def da_dict(cls, d: Mapping[str, Any]) -> FirmaVerificata:
        return cls(
            formato=FormatoFirma(d["formato"]), indicazione=Indicazione(d["indicazione"]),
            firmatario=d.get("firmatario"), certificato=Certificato.da_dict(d.get("certificato")),
            crittografia_valida=d.get("crittografia_valida"),
            catena_attendibile=d.get("catena_attendibile"), trust_anchor=d.get("trust_anchor"),
            revoca=StatoRevoca(d.get("revoca") or StatoRevoca.NON_CONTROLLATO),
            data_firma=d.get("data_firma"), sottoindicazione=d.get("sottoindicazione"),
            motivi=tuple(str(m) for m in d.get("motivi") or ()))


@dataclass(frozen=True)
class EsitoVerifica:
    """Esito della verifica delle firme di UN file."""

    formato: FormatoFirma
    livello: LivelloVerifica
    verificatore: str
    firme: tuple[FirmaVerificata, ...] = ()
    verificato_il: str | None = None
    #: motivo generale (busta illeggibile, file non firmato, formato non supportato)
    motivo: str | None = None
    rapporto_grezzo: str | None = None
    #: verificato dal ripiego (es. pyHanko al posto di EU DSS non raggiungibile)
    ripiego: bool = False
    note: tuple[str, ...] = field(default_factory=tuple)
    #: revisione X5/X8: firme NON verificate per un limite di risorse (file
    #: oltre la dimensione massima della verifica, tempo massimo superato): il
    #: motivo in parole. Il giudizio (RGL-109) non rifiuta al livello RILEVATA
    #: e rifiuta un file firmato da CRITTOGRAFICA in su
    limite_superato: str | None = None

    @property
    def firmato(self) -> bool:
        return self.formato is not FormatoFirma.NESSUNO

    @property
    def indicazione(self) -> Indicazione | None:
        """La peggiore fra le firme; None se il file non è firmato. Una busta
        riconosciuta ma senza firme leggibili è TOTAL_FAILED (FORMAT_FAILURE)."""
        if not self.firmato:
            return None
        if not self.firme:
            return Indicazione.TOTAL_FAILED
        return min((f.indicazione for f in self.firme), key=_ORDINE_INDICAZIONI.index)

    def come_dict(self) -> dict[str, Any]:
        indicazione = self.indicazione
        return {
            "formato": self.formato.value, "livello": self.livello.value,
            "verificatore": self.verificatore, "verificato_il": self.verificato_il,
            "indicazione": indicazione.value if indicazione else None,
            "motivo": self.motivo, "ripiego": self.ripiego, "note": list(self.note),
            "limite_superato": self.limite_superato,
            "firme": [f.come_dict() for f in self.firme],
            "rapporto_grezzo": (self.rapporto_grezzo[:RAPPORTO_GREZZO_MAX]
                                if self.rapporto_grezzo else None),
        }

    @classmethod
    def da_dict(cls, d: Mapping[str, Any]) -> EsitoVerifica:
        firme: Sequence[Mapping[str, Any]] = d.get("firme") or ()
        return cls(
            formato=FormatoFirma(d["formato"]), livello=LivelloVerifica(d["livello"]),
            verificatore=str(d.get("verificatore") or "?"),
            firme=tuple(FirmaVerificata.da_dict(f) for f in firme),
            verificato_il=d.get("verificato_il"), motivo=d.get("motivo"),
            rapporto_grezzo=d.get("rapporto_grezzo"), ripiego=bool(d.get("ripiego")),
            note=tuple(str(n) for n in d.get("note") or ()),
            limite_superato=d.get("limite_superato"))


def esito_non_firmato(livello: LivelloVerifica, verificatore: str,
                      verificato_il: str | None = None) -> EsitoVerifica:
    return EsitoVerifica(FormatoFirma.NESSUNO, livello, verificatore,
                         verificato_il=verificato_il, motivo="nessuna firma nel file")


def esito_oltre_limite(formato: FormatoFirma, livello: LivelloVerifica, verificatore: str,
                       motivo: str, verificato_il: str | None = None) -> EsitoVerifica:
    """Firme non verificate per un limite di risorse (dimensione, tempo): esito
    esplicito, distinto da «non firmato» e da «non valida» (revisione X5/X8)."""
    if formato is FormatoFirma.NESSUNO:
        return esito_non_firmato(livello, verificatore, verificato_il)
    return EsitoVerifica(formato, livello, verificatore, verificato_il=verificato_il,
                         motivo=f"firme non verificate: {motivo}", limite_superato=motivo)


def esito_illeggibile(formato: FormatoFirma, livello: LivelloVerifica, verificatore: str,
                      motivo: str, verificato_il: str | None = None) -> EsitoVerifica:
    """Busta riconosciuta ma non decodificabile: TOTAL_FAILED / FORMAT_FAILURE."""
    return EsitoVerifica(formato, livello, verificatore, verificato_il=verificato_il,
                         motivo=f"{in_parole(SOTTO_FORMATO)}: {motivo}")


__all__ = [
    "PAROLE", "RAPPORTO_GREZZO_MAX", "Certificato", "EsitoVerifica", "FirmaVerificata",
    "FormatoFirma", "Indicazione", "LivelloVerifica", "StatoRevoca", "codice_fiscale",
    "esito_illeggibile", "esito_non_firmato", "esito_oltre_limite", "in_parole",
]
