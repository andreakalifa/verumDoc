# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/regole.py — catalogo delle regole di validazione con effetto
configurabile (REGOLE_SVILUPPO §8.6, §8.10, §8.3; decisione VD-P-01).

Ogni regola è una funzione PURA che, dati il versamento e l'ambiente (impostazioni
risolte e profilo), restituisce dei riscontri; il motore applica l'effetto
risolto per la regola:

  BLOCCA  → il riscontro diventa un rilievo bloccante: il versamento si rifiuta
            con TUTTI i rilievi bloccanti;
  AVVISA  → rilievo d'avviso: il versamento prosegue, l'avviso si salva sul PdV,
            torna nella risposta e finisce nel rapporto di versamento;
  IGNORA  → la regola non si esegue.

L'effetto è a sua volta un'impostazione (``REGOLA.<codice>.EFFETTO``) risolta con
la stessa catena di tutte le altre (REGOLE §8.2); una regola ``locked`` (blocco
imposto dalla norma) non è personalizzabile dal cliente.

Le regole lavorano in due fasi: ``INDICE`` (solo indice e metadati, prima di
trasferire i file: controlli economici) e ``FILE`` (dimensioni e formati rilevati
dal contenuto, dopo il trasferimento).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    Impostazione,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    TipoImpostazione,
    ValoreEffettivo,
    VersoSicuro,
    valore_per_fotografia,
)
from libs.verumdoc_rules.profili import ProfiloMetadati

if TYPE_CHECKING:
    from libs.verumdoc_rules.accordi import AccordoVersamento
    from libs.verumdoc_rules.override import OverrideVersamento


class Effetto(StrEnum):
    BLOCCA = "BLOCCA"
    AVVISA = "AVVISA"
    IGNORA = "IGNORA"


class Fase(StrEnum):
    INDICE = "INDICE"
    FILE = "FILE"
    #: R08: regole valutate solo dalla verifica asincrona (validation), con i
    #: dati che solo lei calcola sul contenuto intero (PUID, PDF/A, firme)
    VERIFICA = "VERIFICA"


@dataclass(frozen=True)
class DocumentoDaValutare:
    """Vista pura di un documento del versamento.

    ``metadati`` è il dizionario ``parametri`` così come versato (chiavi non
    previste comprese); ``dimensione_byte`` e ``formato_rilevato`` sono noti
    solo nella fase FILE."""

    id: str
    tipo: str
    nome_file: str
    metadati: Mapping[str, Any] | None = None
    dimensione_byte: int | None = None
    formato_rilevato: str | None = None
    #: R08 (fase VERIFICA o FILE nella verifica): PUID PRONOM, conformità PDF/A
    #: (False = trovato NON conforme da veraPDF) ed esito normalizzato delle firme
    puid: str | None = None
    pdfa_conforme: bool | None = None
    firme: Mapping[str, Any] | None = None
    #: R09 (D24): atto di sistema (atto XML di rettifica dal canale CICLO_VITA,
    #: ``atti_sistema.e_atto_di_sistema``): sempre ammesso dalle regole dei formati
    atto_di_sistema: bool = False


def _minuscolo(formato: str) -> str:
    return formato.strip().lower()


@dataclass(frozen=True)
class DatiVersamento:
    """Ciò che le regole vedono del versamento.

    ``campi_noti``: nomi dei metadati previsti dallo schema dell'API (gli altri,
    se non sono nel profilo, sono «non previsti»); ``contenitore_libero``: nome
    del dizionario di metadati liberi dichiarati come tali (non segnalati);
    ``normalizza_formato``: riconduce un MIME alla forma canonica."""

    classe: str
    documenti: tuple[DocumentoDaValutare, ...]
    campi_noti: frozenset[str] = frozenset()
    contenitore_libero: str = "metadati_extra"
    normalizza_formato: Callable[[str], str] = _minuscolo

    def principali(self) -> tuple[DocumentoDaValutare, ...]:
        return tuple(d for d in self.documenti if d.tipo == "PRINCIPALE")


@dataclass(frozen=True)
class Riscontro:
    """Ciò che una regola trova (senza effetto). Il messaggio non contiene valori."""

    messaggio: str
    campo: str | None = None
    documento: str | None = None


@dataclass(frozen=True)
class Rilievo:
    """Riscontro con l'effetto applicato: esce nella risposta, nel PdV e nel RdV."""

    regola: str
    effetto: Effetto
    messaggio: str
    campo: str | None = None
    documento: str | None = None

    def come_dict(self) -> dict[str, Any]:
        return {
            "regola": self.regola,
            "effetto": self.effetto.value,
            "campo": self.campo,
            "documento": self.documento,
            "messaggio": self.messaggio,
        }


class AmbienteRegole:
    """Impostazioni risolte (con cache e registro dell'origine), profilo applicato
    e, da R05, accordo di versamento e personalizzazione del PdV applicati."""

    def __init__(
        self,
        risolutore: Risolutore,
        contesto: ContestoRisoluzione,
        profilo: ProfiloMetadati | None = None,
        accordo: AccordoVersamento | None = None,
        override: OverrideVersamento | None = None,
    ) -> None:
        self.risolutore = risolutore
        self.contesto = contesto
        self.profilo = profilo
        self.accordo = accordo
        self.override = override
        self._consultate: dict[str, ValoreEffettivo] = {}

    def effettiva(self, codice: str) -> ValoreEffettivo:
        if codice not in self._consultate:
            self._consultate[codice] = self.risolutore.effettiva(self.contesto, codice)
        return self._consultate[codice]

    def valore(self, codice: str) -> Any:
        return self.effettiva(codice).valore

    def impostazioni_consultate(self) -> dict[str, dict[str, Any]]:
        return {
            codice: {"valore": valore_per_fotografia(v.valore), "origine": v.origine.value}
            for codice, v in sorted(self._consultate.items())
        }


FunzioneRegola = Callable[[DatiVersamento, AmbienteRegole], Iterable[Riscontro]]


@dataclass(frozen=True)
class Regola:
    codice: str
    etichetta: str
    fase: Fase
    effetto_predefinito: Effetto
    funzione: FunzioneRegola
    locked: bool = False
    norm_ref: str | None = None
    ambito: str = "versamento"
    #: livelli a cui si può scegliere l'effetto (None = predefinito del registro)
    ambito_ammesso: frozenset[Livello] | None = None
    #: R08 revisione X2: True = l'effetto ha il verso «più sicuro» BLOCCA >
    #: AVVISA > IGNORA (ordine di ``Effetto``): l'ente e la classe possono solo
    #: renderlo più severo del conservatore (o del suo LIMITE per l'ente); il
    #: motore lo fa valere e la console blocca il valore meno severo
    solo_piu_severo: bool = False

    @property
    def codice_impostazione(self) -> str:
        return f"REGOLA.{self.codice}.EFFETTO"

    def impostazione_effetto(self) -> Impostazione:
        return Impostazione(
            codice=self.codice_impostazione,
            tipo=TipoImpostazione.SCELTA,
            predefinito=self.effetto_predefinito.value,
            descrizione=f"Effetto della regola {self.codice} — {self.etichetta}",
            norm_ref=self.norm_ref,
            locked=self.locked,
            scelte=tuple(e.value for e in Effetto),
            ambito_ammesso=self.ambito_ammesso,
            gruppo="regole",
            verso_sicuro=VersoSicuro.ORDINE_SCELTE if self.solo_piu_severo else None,
        )


@dataclass
class EsitoRegole:
    rilievi: list[Rilievo] = field(default_factory=list)

    @property
    def bloccanti(self) -> list[Rilievo]:
        return [r for r in self.rilievi if r.effetto is Effetto.BLOCCA]

    @property
    def avvisi(self) -> list[Rilievo]:
        return [r for r in self.rilievi if r.effetto is Effetto.AVVISA]

    @property
    def blocca(self) -> bool:
        return any(r.effetto is Effetto.BLOCCA for r in self.rilievi)

    def unisci(self, altro: EsitoRegole) -> EsitoRegole:
        return EsitoRegole([*self.rilievi, *altro.rilievi])


class CatalogoRegole:
    """Catalogo versionato: cambia ``versione`` quando cambia una regola o se ne aggiunge una."""

    def __init__(self, versione: str, regole: Iterable[Regola] = (), *,
                 fotografa: Iterable[str] = (),
                 extra_fotografia: Mapping[str, Any] | None = None) -> None:
        self.versione = versione
        #: R08: impostazioni lette solo dalla verifica asincrona (livello delle
        #: firme, esiti del catalogo dei formati): si fotografano comunque
        self.fotografa = tuple(fotografa)
        self.extra_fotografia = dict(extra_fotografia or {})
        self._regole: dict[str, Regola] = {}
        for regola in regole:
            self.registra(regola)

    def registra(self, regola: Regola) -> None:
        if regola.codice in self._regole:
            raise ValueError(f"Regola {regola.codice} già registrata")
        if regola.locked and regola.effetto_predefinito is not Effetto.BLOCCA:
            raise ValueError(f"Regola {regola.codice}: una regola locked blocca sempre")
        self._regole[regola.codice] = regola

    def __getitem__(self, codice: str) -> Regola:
        return self._regole[codice]

    def regole(self, fase: Fase | None = None) -> tuple[Regola, ...]:
        return tuple(r for r in self._regole.values() if fase is None or r.fase is fase)

    def impostazioni(self) -> list[Impostazione]:
        """Le impostazioni d'effetto da aggiungere al registro."""
        return [r.impostazione_effetto() for r in self._regole.values()]

    def valuta(self, dati: DatiVersamento, ambiente: AmbienteRegole, fase: Fase) -> EsitoRegole:
        """Esegue le regole della fase con l'effetto risolto; ordine stabile (codice)."""
        esito = EsitoRegole()
        for regola in sorted(self.regole(fase), key=lambda r: r.codice):
            effetto = Effetto(ambiente.valore(regola.codice_impostazione))
            if effetto is Effetto.IGNORA:
                continue
            for r in regola.funzione(dati, ambiente):
                esito.rilievi.append(
                    Rilievo(regola.codice, effetto, r.messaggio, r.campo, r.documento)
                )
        return esito

    def valuta_regole(self, codici: Iterable[str], dati: DatiVersamento,
                      ambiente: AmbienteRegole) -> EsitoRegole:
        """Solo le regole indicate (qualunque fase), con l'effetto risolto: la
        verifica asincrona riapplica così le regole del contenuto (R08)."""
        esito = EsitoRegole()
        for codice in sorted(codici):
            regola = self._regole[codice]
            effetto = Effetto(ambiente.valore(regola.codice_impostazione))
            if effetto is Effetto.IGNORA:
                continue
            for r in regola.funzione(dati, ambiente):
                esito.rilievi.append(
                    Rilievo(regola.codice, effetto, r.messaggio, r.campo, r.documento))
        return esito

    def fotografia(self, ambiente: AmbienteRegole) -> dict[str, Any]:
        """Versioni applicate (REGOLE §8.3): catalogo, profilo, impostazioni con origine."""
        for regola in self._regole.values():
            ambiente.effettiva(regola.codice_impostazione)
        for codice in self.fotografa:
            ambiente.effettiva(codice)
        foto: dict[str, Any] = {
            "catalogo_regole": self.versione,
            "profilo_metadati": ambiente.profilo.riferimento() if ambiente.profilo else None,
            "impostazioni": ambiente.impostazioni_consultate(),
        }
        # R05 (Q1): accordo di versamento e personalizzazione del PdV applicati
        # (chiavi sempre presenti dal catalogo 2: None = nessuno)
        if self.versione != "1":
            foto["accordo"] = ambiente.accordo.riferimento() if ambiente.accordo else None
            foto["override"] = ambiente.override.riferimento() if ambiente.override else None
        foto.update(self.extra_fotografia)
        return foto


def rilievo_override(ambiente: AmbienteRegole) -> Rilievo | None:
    """Avviso RGL-110 quando il versamento porta una personalizzazione motivata:
    finisce nella risposta, nel PdV e nel rapporto di versamento (§8.5, §15.7)."""
    if ambiente.override is None:
        return None
    codici = ", ".join(sorted(ambiente.override.valori))
    return Rilievo("RGL-110", Effetto.AVVISA,
                   f"Impostazioni personalizzate per questo versamento: {codici}",
                   campo="override")


def crea_registro(voci: Iterable[Impostazione], catalogo: CatalogoRegole) -> RegistroImpostazioni:
    """Registro delle impostazioni comprensivo degli effetti delle regole del catalogo."""
    return RegistroImpostazioni([*voci, *catalogo.impostazioni()])


__all__ = [
    "AmbienteRegole",
    "CatalogoRegole",
    "DatiVersamento",
    "DocumentoDaValutare",
    "Effetto",
    "EsitoRegole",
    "Fase",
    "FunzioneRegola",
    "Livello",
    "Regola",
    "Rilievo",
    "Riscontro",
    "crea_registro",
    "rilievo_override",
]
