# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/impostazioni.py — registro delle impostazioni e catena di
risoluzione unica (REGOLE_SVILUPPO §8.1, §8.2, §8.5, §8.14; decisione VD-P-01).

Pacchetto PURO: solo libreria standard, nessun orologio (il tempo arriva nel
contesto), nessun accesso a database. I valori dei livelli li dà un
«fornitore» (``FornitoreValori``): il gateway lo realizza leggendo dal
database PRIMA di chiamare la risoluzione, che resta sincrona e pura.

Catena di risoluzione (dal più specifico al più generale), un solo punto:

    override motivato sul singolo PdV → accordo di versamento → classe
    documentale → ente (cliente) → profilo del conservatore → predefinito

R05 (Q1): i livelli ACCORDO (accordo di versamento versionato, ``accordi.py``) e
PDV (personalizzazione motivata sul singolo versamento, ``override.py``) sono
valorizzati dal gateway. Un'impostazione ``locked`` (valore imposto dalla norma
o dal conservatore) ignora i livelli del cliente: si risolve solo fra
conservatore e predefinito.

Ogni voce dichiara anche DOVE si può personalizzare (``ambito_ammesso``), se la
gestisce solo il conservatore (``riservata_conservatore``: es. le quote) e chi
la applica (``applicata_da``): una voce che nessun servizio legge non si può
modificare (REGOLE §9.9) e la console lo dice.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, NamedTuple, Protocol


class Livello(StrEnum):
    """Livelli della catena di risoluzione (REGOLE §8.2; contratto R05 Q1).

    ``PDV`` è la personalizzazione motivata sul singolo versamento; il nome
    storico ``OVERRIDE_PDV`` resta come alias (nessun dato lo usava prima di R05)."""

    PDV = "PDV"
    OVERRIDE_PDV = "PDV"
    ACCORDO = "ACCORDO"
    CLASSE = "CLASSE"
    ENTE = "ENTE"
    CONSERVATORE = "CONSERVATORE"
    PREDEFINITO = "PREDEFINITO"


#: Ordine di risoluzione: il primo livello che ha un valore vince.
CATENA: tuple[Livello, ...] = (
    Livello.PDV,
    Livello.ACCORDO,
    Livello.CLASSE,
    Livello.ENTE,
    Livello.CONSERVATORE,
    Livello.PREDEFINITO,
)

#: Livelli personalizzabili dal cliente: ignorati per le impostazioni ``locked``.
LIVELLI_CLIENTE: frozenset[Livello] = frozenset(
    {
        Livello.PDV,
        Livello.ACCORDO,
        Livello.CLASSE,
        Livello.ENTE,
    }
)

#: Livelli personalizzabili quando la voce non dichiara altro: tutti tranne il PdV
#: (la personalizzazione del singolo versamento va concessa voce per voce).
AMBITO_PREDEFINITO: frozenset[Livello] = frozenset(
    {Livello.CONSERVATORE, Livello.ENTE, Livello.CLASSE, Livello.ACCORDO}
)
#: Livelli che si possono scegliere in configurazione (il PREDEFINITO sta nel codice).
LIVELLI_CONFIGURABILI: tuple[Livello, ...] = (
    Livello.CONSERVATORE, Livello.ENTE, Livello.CLASSE, Livello.ACCORDO, Livello.PDV,
)


class TipoImpostazione(StrEnum):
    """Tipi ammessi per il valore di un'impostazione (REGOLE §8.1)."""

    INTERO = "int"
    BOOLEANO = "bool"
    SCELTA = "choice"
    CODICI = "codes"
    DURATA = "duration"
    DATA = "date"


class VersoSicuro(StrEnum):
    """R07 (revisione W1): verso «più sicuro» di un parametro, dichiarato per voce.

    Per le voci personalizzabili dal cliente il motore lo FA valere: il livello
    del cliente può solo spostare il valore in questo verso rispetto al tetto del
    conservatore (valore del conservatore o limite per l'ente). Per le voci
    riservate al conservatore è dichiarativo (registro e console).

      MINORE         più basso è più sicuro (durate, giorni di conservazione
                     temporanea, quote, date di fine di una proroga)
      MAGGIORE       più alto è più sicuro (es. secondi fra due versamenti)
      ORDINE_SCELTE  le ``scelte`` sono elencate dalla più sicura alla meno
      SOTTOINSIEME   (codici) meno elementi è più sicuro. R10 (D08): anche per
                     le voci del cliente, con il confronto PER INSIEMI (non per
                     numero): il valore del cliente deve stare dentro il tetto
                     e il risultato è l'intersezione (es. metodi d'accesso)
      SOVRAINSIEME   (codici, R10, D32) più elementi è più sicuro: il valore del
                     cliente deve contenere il tetto e il risultato è l'unione
                     (es. avvisi obbligatori)
    """

    MINORE = "minore"
    MAGGIORE = "maggiore"
    ORDINE_SCELTE = "ordine_scelte"
    SOTTOINSIEME = "sottoinsieme"
    SOVRAINSIEME = "sovrainsieme"


#: R07 (W1): prefisso del LIMITE che il conservatore fissa per un ente su una
#: voce con verso sicuro: ``LIMITE.<codice>`` al livello ENTE, riservato al
#: conservatore. L'amministratore dell'ente non lo scrive né lo annulla e il suo
#: valore non può superarlo (nel verso meno sicuro).
PREFISSO_LIMITE = "LIMITE."


def codice_limite(codice: str) -> str:
    return PREFISSO_LIMITE + codice


class ImpostazioneSconosciuta(KeyError):
    """Codice non presente nel registro."""


class ValoreImpostazioneNonValido(ValueError):
    """Un livello fornisce un valore che non rispetta il tipo dell'impostazione.

    È un errore di configurazione: il chiamante non deve indovinare un valore."""

    def __init__(self, codice: str, livello: Livello | None, motivo: str) -> None:
        dove = f" (livello {livello.value})" if livello else ""
        super().__init__(f"Impostazione {codice}{dove}: {motivo}")
        self.codice = codice
        self.livello = livello
        self.motivo = motivo


_DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Impostazione:
    """Voce del registro (REGOLE §8.1).

    ``predefinito`` è già nel tipo dell'impostazione; ``norm_ref`` cita la norma
    da cui deriva il valore (``None`` se è una scelta di servizio); ``locked``
    vuol dire «non personalizzabile dal cliente» (§8.5).

    R05 (Q1):
      - ``ambito_ammesso``: livelli a cui si può dare un valore (``None`` =
        ``AMBITO_PREDEFINITO``; per una voce ``locked`` solo CONSERVATORE);
      - ``riservata_conservatore``: anche ai livelli del cliente il valore lo
        decide il conservatore (quote, retention dei registri);
      - ``applicata_da``: chi legge la voce («gateway», «validation»…); ``None``
        = registrata ma non ancora collegata: si mostra, non si modifica (§9.9);
      - ``gruppo``: raggruppamento per la console (versamento, quote, registri…).

    R07:
      - ``verso_sicuro`` (revisione W1, ``VersoSicuro``): verso «più sicuro»
        del parametro. Se la voce è personalizzabile dal cliente (non locked,
        non riservata, con un livello del cliente nell'ambito: ``solo_riduzione``)
        il livello del cliente può solo andare in quel verso rispetto al TETTO:
        il limite del conservatore per l'ente (``LIMITE.<codice>``, voce generata
        dal registro) o, senza limite, il valore del conservatore (o il
        predefinito). La console blocca un valore oltre il tetto e la
        risoluzione lo riporta comunque al tetto, così un tetto abbassato dopo
        vale subito anche per chi aveva di più. Il limite distingue «valore» e
        «limite»: il conservatore può consentire a un ente più del valore
        generale (es. RALLENTA) senza che l'ente possa andare oltre;
      - ``limite_di``: per una voce ``LIMITE.<codice>``, il codice limitato;
      - ``formato_codice``: espressione regolare che ogni elemento di una voce
        CODICI deve rispettare (es. porte dei webhook, D16)."""

    codice: str
    tipo: TipoImpostazione
    predefinito: Any
    descrizione: str
    norm_ref: str | None = None
    locked: bool = False
    scelte: tuple[str, ...] = ()
    minimo: int | None = None
    massimo: int | None = None
    ambito_ammesso: frozenset[Livello] | None = None
    riservata_conservatore: bool = False
    applicata_da: str | None = "gateway"
    gruppo: str = "versamento"
    verso_sicuro: VersoSicuro | None = None
    formato_codice: str | None = None
    limite_di: str | None = None

    @property
    def solo_riduzione(self) -> bool:
        """Il motore fa valere il verso sicuro: la voce ha un verso ed è
        personalizzabile da un livello del cliente."""
        return (self.verso_sicuro is not None and not self.locked
                and not self.riservata_conservatore
                and bool(self.livelli_ammessi() & LIVELLI_CLIENTE))

    @property
    def ha_limite_per_ente(self) -> bool:
        """Il conservatore può fissarne il limite per ente (``LIMITE.<codice>``)."""
        return self.solo_riduzione and Livello.ENTE in self.livelli_ammessi()

    def chiave_sicurezza(self, valore: Any) -> Any:
        """Chiave di confronto: più bassa = più sicura (solo voci con verso)."""
        verso = self.verso_sicuro
        if verso is VersoSicuro.MINORE:
            return valore
        if verso is VersoSicuro.MAGGIORE:
            return -valore
        if verso is VersoSicuro.ORDINE_SCELTE:
            return self.scelte.index(valore)
        if verso is VersoSicuro.SOTTOINSIEME:
            return len(valore)
        if verso is VersoSicuro.SOVRAINSIEME:
            return -len(valore)
        raise ValueError(f"Impostazione {self.codice}: nessun verso sicuro")

    @property
    def per_insiemi(self) -> bool:
        """R10: verso di un insieme di codici (confronto per inclusione, non per numero)."""
        return self.verso_sicuro in (VersoSicuro.SOTTOINSIEME, VersoSicuro.SOVRAINSIEME)

    def entro_tetto(self, valore: Any, tetto: Any) -> bool:
        """R10: ``valore`` è sicuro almeno quanto ``tetto`` (mai oltre nel verso
        meno sicuro). Per gli insiemi conta l'inclusione: un elenco più corto ma
        con un codice che il tetto non ha NON è più sicuro."""
        if self.verso_sicuro is VersoSicuro.SOTTOINSIEME:
            return set(valore) <= set(tetto)
        if self.verso_sicuro is VersoSicuro.SOVRAINSIEME:
            return set(valore) >= set(tetto)
        return bool(self.chiave_sicurezza(valore) <= self.chiave_sicurezza(tetto))

    def livelli_ammessi(self) -> frozenset[Livello]:
        """Livelli a cui la voce si può valorizzare (vuoto = sola lettura, §9.9)."""
        if self.applicata_da is None:
            return frozenset()
        if self.locked:
            return frozenset({Livello.CONSERVATORE})
        return self.ambito_ammesso if self.ambito_ammesso is not None else AMBITO_PREDEFINITO

    def converti(self, valore: Any, livello: Livello | None = None) -> Any:
        """Valore ricondotto al tipo; solleva ``ValoreImpostazioneNonValido``."""
        return _converti(self, valore, livello)


def _errore(imp: Impostazione, livello: Livello | None, motivo: str) -> ValoreImpostazioneNonValido:
    return ValoreImpostazioneNonValido(imp.codice, livello, motivo)


def _converti(imp: Impostazione, valore: Any, livello: Livello | None) -> Any:
    tipo = imp.tipo
    if tipo is TipoImpostazione.INTERO:
        if isinstance(valore, bool) or not isinstance(valore, int):
            if isinstance(valore, str) and re.fullmatch(r"-?\d+", valore.strip()):
                valore = int(valore.strip())
            else:
                raise _errore(imp, livello, "atteso un numero intero")
        if imp.minimo is not None and valore < imp.minimo:
            raise _errore(imp, livello, f"valore inferiore al minimo {imp.minimo}")
        if imp.massimo is not None and valore > imp.massimo:
            raise _errore(imp, livello, f"valore superiore al massimo {imp.massimo}")
        return valore
    if tipo is TipoImpostazione.BOOLEANO:
        if not isinstance(valore, bool):
            raise _errore(imp, livello, "atteso un valore booleano")
        return valore
    if tipo is TipoImpostazione.SCELTA:
        if not isinstance(valore, str) or valore not in imp.scelte:
            raise _errore(imp, livello, f"valore non fra quelli ammessi ({', '.join(imp.scelte)})")
        return valore
    if tipo is TipoImpostazione.CODICI:
        if isinstance(valore, str) or not isinstance(valore, Iterable):
            raise _errore(imp, livello, "attesa una lista di codici")
        codici = tuple(valore)
        if not all(isinstance(c, str) and c.strip() for c in codici):
            raise _errore(imp, livello, "ogni codice deve essere un testo non vuoto")
        puliti = tuple(c.strip() for c in codici)
        if imp.formato_codice is not None:
            errati = [c for c in puliti if not re.fullmatch(imp.formato_codice, c)]
            if errati:
                raise _errore(imp, livello, f"codici non ammessi: {', '.join(errati[:5])}")
        return puliti
    if tipo is TipoImpostazione.DURATA:
        if isinstance(valore, timedelta):
            return valore
        if isinstance(valore, bool) or not isinstance(valore, int) or valore < 0:
            raise _errore(imp, livello, "attesa una durata in secondi (intero non negativo)")
        return timedelta(seconds=valore)
    if tipo is TipoImpostazione.DATA:
        if isinstance(valore, datetime):
            raise _errore(imp, livello, "attesa una data, non un istante")
        if isinstance(valore, date):
            return valore
        if isinstance(valore, str) and _DATA_ISO.match(valore):
            try:
                return date.fromisoformat(valore)
            except ValueError:
                pass
        raise _errore(imp, livello, "attesa una data nel formato AAAA-MM-GG")
    raise _errore(imp, livello, f"tipo non gestito: {tipo}")  # pragma: no cover


_TIPI_ORDINATI = frozenset({TipoImpostazione.INTERO, TipoImpostazione.DATA,
                            TipoImpostazione.DURATA})


def _controlla_verso(voce: Impostazione) -> None:
    """Un verso che il motore deve far valere ha senso solo per il tipo giusto."""
    verso = voce.verso_sicuro
    if verso is None:
        return
    ammessi = {VersoSicuro.MINORE: _TIPI_ORDINATI, VersoSicuro.MAGGIORE: {TipoImpostazione.INTERO},
               VersoSicuro.ORDINE_SCELTE: {TipoImpostazione.SCELTA},
               VersoSicuro.SOTTOINSIEME: {TipoImpostazione.CODICI},
               VersoSicuro.SOVRAINSIEME: {TipoImpostazione.CODICI}}[verso]
    if voce.tipo not in ammessi:
        raise ValueError(f"Impostazione {voce.codice}: verso {verso.value} non valido per "
                         f"il tipo {voce.tipo.value}")


def voce_limite(voce: Impostazione) -> Impostazione:
    """Voce ``LIMITE.<codice>``: tetto del conservatore per UN ente (R07, W1)."""
    return replace(
        voce, codice=codice_limite(voce.codice),
        descrizione=(f"Limite del conservatore per l'ente su {voce.codice}: l'amministratore "
                     "dell'ente non può superarlo né modificarlo (vuoto = vale il valore "
                     "del conservatore)"),
        ambito_ammesso=frozenset({Livello.ENTE}), riservata_conservatore=True,
        verso_sicuro=None, limite_di=voce.codice)


class RegistroImpostazioni:
    """Insieme delle impostazioni note; un codice si registra una sola volta."""

    def __init__(self, voci: Iterable[Impostazione] = ()) -> None:
        self._voci: dict[str, Impostazione] = {}
        for voce in voci:
            self.registra(voce)

    def registra(self, voce: Impostazione) -> None:
        if voce.limite_di is not None and self._voci.get(voce.codice) == voce:
            return  # limite già generato dalla sua voce (registro ricostruito da un altro)
        if voce.codice in self._voci:
            raise ValueError(f"Impostazione {voce.codice} già registrata")
        _controlla_verso(voce)
        # Il predefinito deve rispettare il tipo: un registro incoerente non parte.
        object.__setattr__(
            voce, "predefinito", voce.converti(voce.predefinito, Livello.PREDEFINITO)
        )
        self._voci[voce.codice] = voce
        if voce.ha_limite_per_ente and codice_limite(voce.codice) not in self._voci:
            self._voci[codice_limite(voce.codice)] = voce_limite(voce)

    def __getitem__(self, codice: str) -> Impostazione:
        try:
            return self._voci[codice]
        except KeyError:
            raise ImpostazioneSconosciuta(codice) from None

    def __contains__(self, codice: object) -> bool:
        return codice in self._voci

    def __iter__(self) -> Iterator[Impostazione]:
        return iter(self._voci.values())

    def codici(self) -> tuple[str, ...]:
        return tuple(self._voci)


@dataclass(frozen=True)
class ContestoRisoluzione:
    """Di chi e quando si risolve un'impostazione. Il tempo è un argomento (§17.6)."""

    adesso: datetime
    tenant_id: str | None = None
    classe_id: str | None = None
    accordo_id: str | None = None
    pdv_id: str | None = None


class _Assente:
    """Segnaposto: il livello non ha un valore per il codice."""

    _istanza: _Assente | None = None

    def __new__(cls) -> _Assente:
        if cls._istanza is None:
            cls._istanza = super().__new__(cls)
        return cls._istanza

    def __repr__(self) -> str:
        return "ASSENTE"

    def __bool__(self) -> bool:
        return False


ASSENTE: Any = _Assente()


class FornitoreValori(Protocol):
    """Chi conosce i valori dei livelli (database, file, test).

    Restituisce ``ASSENTE`` se il livello non ha un valore per quel codice.
    Il livello PREDEFINITO non si chiede mai al fornitore: sta nel registro."""

    def valore(self, livello: Livello, contesto: ContestoRisoluzione, codice: str) -> Any: ...


@dataclass
class FornitoreDaDizionari:
    """Fornitore con i valori già caricati per UN contesto: ``{livello: {codice: valore}}``.

    È la forma con cui il gateway passa al motore ciò che ha letto dal database."""

    valori: Mapping[Livello, Mapping[str, Any]] = field(default_factory=dict)

    def valore(self, livello: Livello, contesto: ContestoRisoluzione, codice: str) -> Any:
        return self.valori.get(livello, {}).get(codice, ASSENTE)


class ValoreEffettivo(NamedTuple):
    """Esito della risoluzione: valore e livello da cui viene (l'origine si mostra sempre)."""

    valore: Any
    origine: Livello


class Risolutore:
    """Unico punto di risoluzione (REGOLE §8.2): ``effettiva(contesto, codice)``."""

    def __init__(self, registro: RegistroImpostazioni, fornitore: FornitoreValori) -> None:
        self.registro = registro
        self.fornitore = fornitore

    def catena(self, contesto: ContestoRisoluzione, codice: str) -> list[tuple[Livello, Any]]:
        """Valore di OGNI livello (``ASSENTE`` se manca), dal più specifico al
        predefinito, per mostrare «ereditato da» e «valore del livello superiore»
        (REGOLE §8.5). I livelli ignorati per una voce ``locked`` risultano ASSENTE."""
        voce = self.registro[codice]
        righe: list[tuple[Livello, Any]] = []
        for livello in CATENA:
            if livello is Livello.PREDEFINITO:
                righe.append((livello, voce.predefinito))
            elif voce.locked and livello in LIVELLI_CLIENTE:
                righe.append((livello, ASSENTE))
            else:
                grezzo = self.fornitore.valore(livello, contesto, codice)
                righe.append((livello, ASSENTE if grezzo is ASSENTE
                              else voce.converti(grezzo, livello)))
        return righe

    def effettiva(self, contesto: ContestoRisoluzione, codice: str) -> ValoreEffettivo:
        voce = self.registro[codice]
        if voce.solo_riduzione:
            return self._piu_sicuro(contesto, voce)
        for livello in CATENA:
            if livello is Livello.PREDEFINITO:
                return ValoreEffettivo(voce.predefinito, Livello.PREDEFINITO)
            if voce.locked and livello in LIVELLI_CLIENTE:
                continue
            grezzo = self.fornitore.valore(livello, contesto, codice)
            if grezzo is ASSENTE:
                continue
            return ValoreEffettivo(voce.converti(grezzo, livello), livello)
        raise AssertionError("catena senza PREDEFINITO")  # pragma: no cover

    def tetto(self, contesto: ContestoRisoluzione, codice: str) -> ValoreEffettivo:
        """R07 (W1): valore meno sicuro che un livello del cliente può dare a una
        voce con verso sicuro: il limite del conservatore per l'ente
        (``LIMITE.<codice>`` al livello ENTE) o, senza, il valore del
        conservatore, altrimenti il predefinito. Il limite può anche superare il
        valore generale del conservatore (concessione per quell'ente)."""
        voce = self.registro[codice]
        if voce.ha_limite_per_ente:
            grezzo = self.fornitore.valore(Livello.ENTE, contesto, codice_limite(codice))
            if grezzo is not ASSENTE:
                return ValoreEffettivo(voce.converti(grezzo, Livello.ENTE), Livello.ENTE)
        grezzo = self.fornitore.valore(Livello.CONSERVATORE, contesto, codice)
        if grezzo is not ASSENTE:
            return ValoreEffettivo(voce.converti(grezzo, Livello.CONSERVATORE),
                                   Livello.CONSERVATORE)
        return ValoreEffettivo(voce.predefinito, Livello.PREDEFINITO)

    def _piu_sicuro(self, contesto: ContestoRisoluzione, voce: Impostazione) -> ValoreEffettivo:
        """R07 (``solo_riduzione``): fra i livelli del cliente valorizzati vince il
        più sicuro (a parità il più specifico); senza, vale il valore del
        servizio (conservatore, altrimenti predefinito). Il risultato non va mai
        oltre il ``tetto`` nel verso meno sicuro: oltre, vale il tetto."""
        chiave = voce.chiave_sicurezza
        tetto = self.tetto(contesto, voce.codice)
        if voce.per_insiemi:
            return self._insiemi(contesto, voce, tetto)
        migliore: ValoreEffettivo | None = None
        servizio: ValoreEffettivo | None = None
        for livello, valore in self.catena(contesto, voce.codice):
            if valore is ASSENTE:
                continue
            if livello in LIVELLI_CLIENTE:
                if migliore is None or chiave(valore) < chiave(migliore.valore):
                    migliore = ValoreEffettivo(valore, livello)
            elif servizio is None:
                servizio = ValoreEffettivo(valore, livello)
        scelto = migliore or servizio
        assert scelto is not None  # il PREDEFINITO c'è sempre
        return scelto if chiave(scelto.valore) <= chiave(tetto.valore) else tetto

    def _insiemi(self, contesto: ContestoRisoluzione, voce: Impostazione,
                 tetto: ValoreEffettivo) -> ValoreEffettivo:
        """R10: voci a insiemi. SOTTOINSIEME: intersezione del tetto con ogni livello
        del cliente valorizzato (ordine del tetto); SOVRAINSIEME: unione. Senza
        livelli del cliente vale il tetto (valore del conservatore o limite)."""
        risultato = list(tetto.valore)
        origine = tetto.origine
        for livello, valore in self.catena(contesto, voce.codice):
            if valore is ASSENTE or livello not in LIVELLI_CLIENTE:
                continue
            if voce.verso_sicuro is VersoSicuro.SOTTOINSIEME:
                risultato = [c for c in risultato if c in set(valore)]
            else:
                risultato += [c for c in valore if c not in risultato]
            origine = livello if origine not in LIVELLI_CLIENTE else origine
        return ValoreEffettivo(tuple(risultato), origine)


def effettiva(
    contesto: ContestoRisoluzione,
    codice: str,
    *,
    registro: RegistroImpostazioni,
    fornitore: FornitoreValori,
) -> ValoreEffettivo:
    """Forma funzionale di ``Risolutore.effettiva``."""
    return Risolutore(registro, fornitore).effettiva(contesto, codice)


#: Fuso dei termini espressi in giorni (validità di profili, fine della transizione).
FUSO_NORMATIVO = "Europe/Rome"


def giorno_locale(adesso: datetime) -> date:
    """Giorno di calendario italiano dell'istante (le date di validità sono giorni italiani)."""
    if adesso.tzinfo is None:
        raise ValueError("istante senza fuso orario")
    from zoneinfo import ZoneInfo

    return adesso.astimezone(ZoneInfo(FUSO_NORMATIVO)).date()


def valore_per_fotografia(valore: Any) -> Any:
    """Valore serializzabile in JSON per la fotografia del PdV (REGOLE §8.3)."""
    if isinstance(valore, tuple):
        return list(valore)
    if isinstance(valore, date):
        return valore.isoformat()
    if isinstance(valore, timedelta):
        return int(valore.total_seconds())
    return valore
