# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/profili.py — profili di metadati per classe documentale come
DATI versionati con validità temporale (REGOLE_SVILUPPO §8.4, §8.8, §8.12).

Un profilo elenca i metadati propri di una classe (nome, tipo, obbligatorio,
sensibile, descrizione, valori ammessi per gli enumerati). Una versione già
applicata a un PdV non si modifica: se ne crea una nuova con ``valido_dal``.
Il PdV fotografa codice e versione del profilo applicato (§8.3).

Se una classe non ha profilo valido alla data, il versamento si comporta come
prima dei profili (§8.14): restano i metadati minimi dell'Allegato 5 e i
metadati obbligatori della classe.

R05 (Q1, VD-C-088): oltre ai profili della classe esistono i profili di
TIPOLOGIA dell'Allegato 5 (documento informatico, documento amministrativo
informatico, aggregazione documentale, fattura, registro di protocollo,
contratto), dati versionati in ``dati/profili/*.json`` e nel database
(``tipologie.py``). La classe sceglie la tipologia con l'impostazione
``PROFILO_TIPOLOGIA``; il profilo applicato è la COMPOSIZIONE dei due
(``componi_profili``: i campi della classe prevalgono su quelli della tipologia
con lo stesso nome). Un campo ``calcolato`` lo valorizza il servizio (es.
l'impronta del documento) e le regole non lo chiedono al produttore; un campo
``da_riscontrare`` va verificato sul testo ufficiale dell'Allegato 5.

R11 (D07, VD-C-136): i profili riscontrati sul testo ufficiale dell'Allegato 5
portano anche
  - strutture annidate (``struttura``, ``elenco_strutture``: Soggetti con i
    ruoli, Tracciature delle modifiche, Assegnazione…) con i ``sottocampi``;
  - obblighi a condizione (``obbligatorio_se``: «obbligatorio se la modalità di
    formazione è a o b»), valori ammessi che dipendono da un altro campo
    (``valori_ammessi_se``: tipo di soggetto ammesso per ruolo), elementi
    richiesti in un elenco (``almeno_uno``: «almeno un Autore o un Mittente»);
  - intervalli (``minimo``/``massimo``, ``min_elementi``/``max_elementi``);
  - ``origine`` (allegato5, servizio, conservatore) e ``rif`` (paragrafo e
    pagina del testo da cui deriva il campo);
  - ``adozione`` del profilo: ``automatica`` (come in R05: vale la versione più
    alta valida nel giorno) o ``esplicita`` (la sceglie la classe con
    ``PROFILO_TIPOLOGIA_VERSIONE``, con anteprima, §8.4).
La composizione con il profilo della classe NON indebolisce la tipologia: un
campo con lo stesso nome può solo diventare più stretto (obbligatorio, meno
valori ammessi), mai facoltativo né di un altro tipo (``componi_profili``).

R16 (VD-C-158): ``regole_incrociate`` del profilo, condizioni fra campi DIVERSI che il
motore non esprimeva (le condizioni dei campi valgono solo fra fratelli): un campo di primo
livello che rende obbligatorio un ELEMENTO di un elenco di strutture («il mittente per i
documenti protocollati»: ``se`` + ``richiede``), la presenza di un campo che lo rende
obbligatorio («l'operatore quando ci sono tracciature»: ``se_presente`` + ``richiede``),
elementi ammessi solo con un valore di un campo di primo livello («PAI/PAE solo in entrata
o in uscita»: ``elemento`` + ``ammesso_se``). Si dichiarano SOLO gli obblighi già scritti
nel riscontro dell'Allegato 5; le versioni già pubblicate non cambiano (la chiave compare
solo se valorizzata, impronte invariate).

Pacchetto puro: nessun orologio, nessun I/O.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class TipoCampo(StrEnum):
    """Tipi dei metadati di profilo."""

    STRINGA = "stringa"
    DATA = "data"
    INTERO = "intero"
    DECIMALE = "decimale"
    BOOLEANO = "booleano"
    ENUM = "enum"
    CODICE_FISCALE = "codice_fiscale"
    PARTITA_IVA = "partita_iva"
    CODICE_IPA = "codice_ipa"
    #: elenco di testi (es. destinatari, parole chiave)
    ELENCO = "elenco"
    #: R11: data e ora ISO 8601 (es. data di registrazione dell'Allegato 5)
    DATA_ORA = "data_ora"
    #: R11: oggetto con ``sottocampi`` (es. una sola assegnazione)
    STRUTTURA = "struttura"
    #: R11: elenco di oggetti con ``sottocampi`` (es. Soggetti con i ruoli)
    ELENCO_STRUTTURE = "elenco_strutture"


#: Tipi con sottocampi.
TIPI_STRUTTURATI = frozenset({TipoCampo.STRUTTURA, TipoCampo.ELENCO_STRUTTURE})
#: Origini ammesse di un campo (R11).
ORIGINI_CAMPO = ("allegato5", "servizio", "conservatore")
#: Modi di adozione di una versione di profilo (R11).
ADOZIONE_AUTOMATICA = "automatica"
ADOZIONE_ESPLICITA = "esplicita"
ADOZIONI = (ADOZIONE_AUTOMATICA, ADOZIONE_ESPLICITA)
#: Profondità massima delle strutture annidate.
_PROFONDITA_MAX = 3
#: Problemi riportati al più in un messaggio di RGL-106.
_PROBLEMI_MAX = 5

#: Lettura di un metadato per nome: (trovato, valore).
Lettore = Callable[[str], tuple[bool, Any]]


class ProfiloNonValido(ValueError):
    """Il profilo salvato non rispetta la forma attesa (errore di configurazione)."""


def vuoto(valore: Any) -> bool:
    """Valore assente: None, testo vuoto, elenco o oggetto vuoto."""
    if valore is None:
        return True
    if isinstance(valore, str):
        return not valore.strip()
    if isinstance(valore, list | tuple | dict):
        return len(valore) == 0
    return False


def _come_testo(valore: Any) -> str:
    if isinstance(valore, bool):
        return "true" if valore else "false"
    return str(valore)


@dataclass(frozen=True)
class Condizione:
    """«Il campo ``campo`` vale uno di ``in_``» (o «nessuno di ``non_in``»).

    Falsa se il campo è assente: un obbligo a condizione non nasce da un dato
    mancante. I booleani si confrontano come ``true``/``false``."""

    campo: str
    in_: tuple[str, ...] = ()
    non_in: tuple[str, ...] = ()

    @classmethod
    def da_dict(cls, dati: Any) -> Condizione:
        if not isinstance(dati, Mapping) or not isinstance(dati.get("campo"), str):
            raise ProfiloNonValido("condizione non valida: attesa {campo, in | non_in}")
        dentro, fuori = dati.get("in"), dati.get("non_in")
        if (dentro is None) == (fuori is None) or not isinstance(dentro or fuori, list) \
                or not (dentro or fuori):
            raise ProfiloNonValido(
                f"condizione su {dati['campo']!r}: indicare un elenco non vuoto in «in» "
                "oppure in «non_in»")
        return cls(dati["campo"], tuple(_come_testo(v) for v in dentro or ()),
                   tuple(_come_testo(v) for v in fuori or ()))

    def come_dict(self) -> dict[str, Any]:
        if self.in_:
            return {"campo": self.campo, "in": list(self.in_)}
        return {"campo": self.campo, "non_in": list(self.non_in)}

    def vale(self, leggi: Lettore) -> bool:
        trovato, valore = leggi(self.campo)
        if not trovato:
            return False
        testo = _come_testo(valore)
        return testo in self.in_ if self.in_ else testo not in self.non_in

    def descrizione(self) -> str:
        if self.in_:
            return f"se {self.campo} è {' o '.join(self.in_)}"
        return f"se {self.campo} non è {' né '.join(self.non_in)}"


def _condizioni(dati: Any, nome: str, chiave: str) -> tuple[Condizione, ...]:
    if dati is None:
        return ()
    if not isinstance(dati, list):
        raise ProfiloNonValido(f"campo {nome!r}: «{chiave}» deve essere un elenco di condizioni")
    return tuple(Condizione.da_dict(c) for c in dati)


def _intero_o_none(dati: Mapping[str, Any], chiave: str, nome: str) -> int | None:
    valore = dati.get(chiave)
    if valore is None:
        return None
    if isinstance(valore, bool) or not isinstance(valore, int):
        raise ProfiloNonValido(f"campo {nome!r}: «{chiave}» deve essere un intero")
    return valore


@dataclass(frozen=True)
class CampoProfilo:
    """Un metadato del profilo. ``sensibile``: mai in chiaro nei log né nei messaggi."""

    nome: str
    tipo: TipoCampo
    obbligatorio: bool = False
    sensibile: bool = False
    descrizione: str = ""
    valori_ammessi: tuple[str, ...] = ()
    #: nome del metadato nell'Allegato 5 (es. «ChiaveDescrittiva.Oggetto»)
    allegato5: str | None = None
    #: valorizzato dal servizio (§8.15): le regole non lo chiedono al produttore
    calcolato: bool = False
    #: da verificare sul testo ufficiale (VD-C-088)
    da_riscontrare: bool = False
    # ── R11 (D07) ──
    #: obbligatorio se almeno una condizione vale (sui campi fratelli)
    obbligatorio_se: tuple[Condizione, ...] = ()
    #: sottocampi di ``struttura`` ed ``elenco_strutture``
    sottocampi: tuple[CampoProfilo, ...] = ()
    #: per un enumerato: valori ammessi quando vale la condizione (la prima che vale)
    valori_ammessi_se: tuple[tuple[Condizione, tuple[str, ...]], ...] = ()
    #: per ``elenco_strutture``: ogni condizione la soddisfa almeno un elemento
    almeno_uno: tuple[Condizione, ...] = ()
    minimo: int | None = None
    massimo: int | None = None
    min_elementi: int | None = None
    max_elementi: int | None = None
    #: allegato5 (testo ufficiale), servizio (metadato di VerumDoc), conservatore
    #: (profilo proprio del conservatore, non disciplinato dall'Allegato 5)
    origine: str | None = None
    #: riferimento al testo (paragrafo, pagina o tabella)
    rif: str | None = None

    @classmethod
    def da_dict(cls, dati: Mapping[str, Any], _profondita: int = 1) -> CampoProfilo:
        try:
            nome = str(dati["nome"]).strip()
            tipo = TipoCampo(str(dati["tipo"]))
        except (KeyError, ValueError) as exc:
            raise ProfiloNonValido(f"campo di profilo non valido: {exc}") from exc
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", nome):
            raise ProfiloNonValido(f"nome di campo non ammesso: {nome!r}")
        valori = tuple(str(v) for v in (dati.get("valori_ammessi") or ()))
        if tipo is TipoCampo.ENUM and not valori:
            raise ProfiloNonValido(f"il campo enumerato {nome!r} non ha valori ammessi")
        grezzi = dati.get("sottocampi") or []
        if tipo in TIPI_STRUTTURATI:
            if not isinstance(grezzi, list) or not grezzi:
                raise ProfiloNonValido(f"il campo strutturato {nome!r} non ha sottocampi")
            if _profondita >= _PROFONDITA_MAX:
                raise ProfiloNonValido(f"campo {nome!r}: strutture annidate oltre "
                                       f"{_PROFONDITA_MAX} livelli")
            sottocampi = tuple(cls.da_dict(c if isinstance(c, Mapping) else {}, _profondita + 1)
                               for c in grezzi)
        elif grezzi:
            raise ProfiloNonValido(f"campo {nome!r}: sottocampi solo per struttura ed "
                                   "elenco_strutture")
        else:
            sottocampi = ()
        se_valori = []
        for voce in dati.get("valori_ammessi_se") or ():
            ammessi = voce.get("valori") if isinstance(voce, Mapping) else None
            if tipo is not TipoCampo.ENUM or not isinstance(ammessi, list) or not ammessi:
                raise ProfiloNonValido(f"campo {nome!r}: valori_ammessi_se solo per un "
                                       "enumerato, con «valori» non vuoto")
            fuori = sorted({str(v) for v in ammessi} - set(valori))
            if fuori:
                raise ProfiloNonValido(f"campo {nome!r}: valori_ammessi_se fuori dai valori "
                                       f"ammessi: {', '.join(fuori)}")
            se_valori.append((Condizione.da_dict(voce), tuple(str(v) for v in ammessi)))
        almeno_uno = _condizioni(dati.get("almeno_uno"), nome, "almeno_uno")
        if almeno_uno and tipo is not TipoCampo.ELENCO_STRUTTURE:
            raise ProfiloNonValido(f"campo {nome!r}: almeno_uno solo per elenco_strutture")
        origine = dati.get("origine")
        if origine is not None and origine not in ORIGINI_CAMPO:
            raise ProfiloNonValido(f"campo {nome!r}: origine non ammessa ({origine!r})")
        allegato5 = dati.get("allegato5")
        campo = cls(
            nome=nome,
            tipo=tipo,
            obbligatorio=bool(dati.get("obbligatorio", False)),
            sensibile=bool(dati.get("sensibile", False)),
            descrizione=str(dati.get("descrizione") or ""),
            valori_ammessi=valori,
            allegato5=str(allegato5) if allegato5 else None,
            calcolato=bool(dati.get("calcolato", False)),
            da_riscontrare=bool(dati.get("da_riscontrare", False)),
            obbligatorio_se=_condizioni(dati.get("obbligatorio_se"), nome, "obbligatorio_se"),
            sottocampi=sottocampi,
            valori_ammessi_se=tuple(se_valori),
            almeno_uno=almeno_uno,
            minimo=_intero_o_none(dati, "minimo", nome),
            massimo=_intero_o_none(dati, "massimo", nome),
            min_elementi=_intero_o_none(dati, "min_elementi", nome),
            max_elementi=_intero_o_none(dati, "max_elementi", nome),
            origine=str(origine) if origine else None,
            rif=str(dati["rif"]) if dati.get("rif") else None,
        )
        campo._controlla_riferimenti()
        return campo

    def _controlla_riferimenti(self) -> None:
        """Le condizioni dei sottocampi citano solo campi fratelli (errore di dati)."""
        if not self.sottocampi:
            return
        nomi = [c.nome for c in self.sottocampi]
        doppi = sorted({n for n in nomi if nomi.count(n) > 1})
        if doppi:
            raise ProfiloNonValido(f"campo {self.nome!r}: sottocampi ripetuti: {', '.join(doppi)}")
        citati = {c.campo for s in self.sottocampi for c in s.obbligatorio_se}
        citati |= {c.campo for s in self.sottocampi for c, _ in s.valori_ammessi_se}
        citati |= {c.campo for c in self.almeno_uno}
        ignoti = sorted(citati - set(nomi))
        if ignoti:
            raise ProfiloNonValido(f"campo {self.nome!r}: condizioni su sottocampi inesistenti: "
                                   f"{', '.join(ignoti)}")

    def come_dict(self) -> dict[str, Any]:
        forma: dict[str, Any] = {
            "nome": self.nome,
            "tipo": self.tipo.value,
            "obbligatorio": self.obbligatorio,
            "sensibile": self.sensibile,
            "descrizione": self.descrizione,
            "valori_ammessi": list(self.valori_ammessi),
            "allegato5": self.allegato5,
            "calcolato": self.calcolato,
            "da_riscontrare": self.da_riscontrare,
        }
        # R11: le chiavi nuove solo se valorizzate (forma dei profili R05 invariata)
        if self.obbligatorio_se:
            forma["obbligatorio_se"] = [c.come_dict() for c in self.obbligatorio_se]
        if self.sottocampi:
            forma["sottocampi"] = [c.come_dict() for c in self.sottocampi]
        if self.valori_ammessi_se:
            forma["valori_ammessi_se"] = [{**c.come_dict(), "valori": list(v)}
                                          for c, v in self.valori_ammessi_se]
        if self.almeno_uno:
            forma["almeno_uno"] = [c.come_dict() for c in self.almeno_uno]
        for chiave in ("minimo", "massimo", "min_elementi", "max_elementi", "origine", "rif"):
            if getattr(self, chiave) is not None:
                forma[chiave] = getattr(self, chiave)
        return forma

    @property
    def richiesto_al_produttore(self) -> bool:
        """Obbligatorio e non calcolato dal servizio (RGL-102)."""
        return self.obbligatorio and not self.calcolato

    def richiesto(self, leggi: Lettore) -> bool:
        """R11: richiesto al produttore per QUESTI metadati (obblighi a condizione)."""
        if self.calcolato:
            return False
        return self.obbligatorio or any(c.vale(leggi) for c in self.obbligatorio_se)

    def ammessi(self, leggi: Lettore) -> tuple[str, ...]:
        """Valori ammessi di un enumerato per questi metadati (``valori_ammessi_se``)."""
        for condizione, valori in self.valori_ammessi_se:
            if condizione.vale(leggi):
                return valori
        return self.valori_ammessi


@dataclass(frozen=True)
class RegolaIncrociata:
    """R16 (VD-C-158): condizione fra un campo di primo livello e gli elementi di un elenco
    di strutture (vedi modulo). ``richiede``: se vale ``se`` (o ``se_presente`` è valorizzato)
    almeno un elemento di ``elenco`` soddisfa ``richiede``; ``elemento`` + ``ammesso_se``: gli
    elementi che soddisfano ``elemento`` sono ammessi solo se vale ``ammesso_se``."""

    codice: str
    elenco: str
    descrizione: str
    se: Condizione | None = None
    se_presente: str | None = None
    richiede: Condizione | None = None
    elemento: Condizione | None = None
    ammesso_se: Condizione | None = None
    rif: str | None = None

    @classmethod
    def da_dict(cls, dati: Any) -> RegolaIncrociata:
        if not isinstance(dati, Mapping):
            raise ProfiloNonValido("regola incrociata non valida: atteso un oggetto")
        codice = str(dati.get("codice") or "")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,59}", codice):
            raise ProfiloNonValido(f"regola incrociata: codice non ammesso ({codice!r})")

        def cond(chiave: str) -> Condizione | None:
            return Condizione.da_dict(dati[chiave]) if dati.get(chiave) is not None else None

        regola = cls(codice=codice, elenco=str(dati.get("elenco") or ""),
                     descrizione=str(dati.get("descrizione") or ""), se=cond("se"),
                     se_presente=str(dati["se_presente"]) if dati.get("se_presente") else None,
                     richiede=cond("richiede"), elemento=cond("elemento"),
                     ammesso_se=cond("ammesso_se"),
                     rif=str(dati["rif"]) if dati.get("rif") else None)
        obbligo = regola.richiede is not None and (regola.se is not None) != (
            regola.se_presente is not None) and regola.elemento is None \
            and regola.ammesso_se is None
        ammissione = regola.elemento is not None and regola.ammesso_se is not None \
            and regola.richiede is None and regola.se is None and regola.se_presente is None
        if not (obbligo or ammissione) or not regola.descrizione:
            raise ProfiloNonValido(f"regola incrociata {codice}: «se» o «se_presente» con "
                                   "«richiede», oppure «elemento» con «ammesso_se»; descrizione")
        return regola

    def come_dict(self) -> dict[str, Any]:
        forma: dict[str, Any] = {"codice": self.codice, "elenco": self.elenco,
                                 "descrizione": self.descrizione}
        for chiave in ("se", "richiede", "elemento", "ammesso_se"):
            if getattr(self, chiave) is not None:
                forma[chiave] = getattr(self, chiave).come_dict()
        if self.se_presente:
            forma["se_presente"] = self.se_presente
        if self.rif:
            forma["rif"] = self.rif
        return forma

    def campi_primo_livello(self) -> set[str]:
        return {c.campo for c in (self.se, self.ammesso_se) if c is not None} | (
            {self.se_presente} if self.se_presente else set())

    def campi_elemento(self) -> set[str]:
        return {c.campo for c in (self.richiede, self.elemento) if c is not None}

    def problema(self, leggi: Lettore) -> str | None:
        """Motivo (senza valori) se i metadati non la rispettano."""
        trovato, valore = leggi(self.elenco)
        elementi = [e for e in valore if isinstance(e, Mapping)] \
            if trovato and isinstance(valore, list) else []
        lettori = [_lettore(e) for e in elementi]
        if self.richiede is not None:
            attiva = self.se.vale(leggi) if self.se is not None \
                else leggi(str(self.se_presente))[0]
            if attiva and not any(self.richiede.vale(le) for le in lettori):
                return f"{self.elenco}: {self.descrizione}"
            return None
        assert self.elemento is not None and self.ammesso_se is not None
        if any(self.elemento.vale(le) for le in lettori) and not self.ammesso_se.vale(leggi):
            return f"{self.elenco}: {self.descrizione}"
        return None


def problemi_regole_incrociate(profilo: ProfiloMetadati, leggi: Lettore) -> list[str]:
    """R16 (VD-C-158): motivi delle regole incrociate non rispettate dai metadati."""
    return [m for r in profilo.regole_incrociate if (m := r.problema(leggi))]


@dataclass(frozen=True)
class ProfiloMetadati:
    """Versione di un profilo di metadati, valida da ``valido_dal`` a ``valido_al`` inclusi."""

    codice: str
    versione: int
    valido_dal: date
    campi: tuple[CampoProfilo, ...]
    valido_al: date | None = None
    id: str | None = None
    #: R05: tipologia dell'Allegato 5 del profilo (None = profilo di classe)
    tipologia: str | None = None
    norm_ref: str | None = None
    #: R05: profili da cui è composto (riferimenti fotografati), vuoto se semplice
    composto_da: tuple[dict[str, Any], ...] = ()
    #: R11: ``automatica`` (vale la più alta valida nel giorno, come in R05) o
    #: ``esplicita`` (la sceglie la classe con PROFILO_TIPOLOGIA_VERSIONE)
    adozione: str = ADOZIONE_AUTOMATICA
    #: R16 (VD-C-158): condizioni fra campi diversi (vuoto nelle versioni precedenti)
    regole_incrociate: tuple[RegolaIncrociata, ...] = ()

    def __post_init__(self) -> None:
        nomi = [c.nome for c in self.campi]
        doppi = sorted({n for n in nomi if nomi.count(n) > 1})
        if doppi:
            raise ProfiloNonValido(f"campi ripetuti nel profilo {self.codice}: {', '.join(doppi)}")
        if self.adozione not in ADOZIONI:
            raise ProfiloNonValido(f"profilo {self.codice}: adozione non ammessa "
                                   f"({self.adozione!r})")
        citati = {c.campo for campo in self.campi for c in campo.obbligatorio_se}
        ignoti = sorted(citati - set(nomi))
        if ignoti:
            raise ProfiloNonValido(f"profilo {self.codice}: condizioni su campi inesistenti: "
                                   f"{', '.join(ignoti)}")
        if self.valido_al is not None and self.valido_al < self.valido_dal:
            raise ProfiloNonValido(
                f"profilo {self.codice} v{self.versione}: valido_al precede valido_dal"
            )
        codici = [r.codice for r in self.regole_incrociate]
        if len(set(codici)) != len(codici):
            raise ProfiloNonValido(f"profilo {self.codice}: regole incrociate ripetute")
        for regola in self.regole_incrociate:
            elenco = self.campo(regola.elenco)
            if elenco is None or elenco.tipo is not TipoCampo.ELENCO_STRUTTURE:
                raise ProfiloNonValido(f"regola {regola.codice}: {regola.elenco!r} non è un "
                                       "elenco di strutture del profilo")
            ignoti = sorted((regola.campi_primo_livello() - set(nomi))
                            | (regola.campi_elemento() - {s.nome for s in elenco.sottocampi}))
            if ignoti:
                raise ProfiloNonValido(f"regola {regola.codice}: campi inesistenti: "
                                       f"{', '.join(ignoti)}")

    def campo(self, nome: str) -> CampoProfilo | None:
        return next((c for c in self.campi if c.nome == nome), None)

    def nomi(self) -> frozenset[str]:
        return frozenset(c.nome for c in self.campi)

    def nomi_sensibili(self) -> frozenset[str]:
        return frozenset(c.nome for c in self.campi if c.sensibile)

    def valido_il(self, giorno: date) -> bool:
        return self.valido_dal <= giorno and (self.valido_al is None or giorno <= self.valido_al)

    def riferimento(self) -> dict[str, Any]:
        """Ciò che il PdV fotografa del profilo applicato."""
        rif: dict[str, Any] = {
            "id": self.id,
            "codice": self.codice,
            "versione": self.versione,
            "valido_dal": self.valido_dal.isoformat(),
            "valido_al": self.valido_al.isoformat() if self.valido_al else None,
        }
        if self.tipologia is not None:
            rif["tipologia"] = self.tipologia
        if self.composto_da:
            rif["composto_da"] = [dict(r) for r in self.composto_da]
        return rif

    def come_dict(self) -> dict[str, Any]:
        """Forma completa (API ed esportazione per il validatore offline)."""
        forma = {**self.riferimento(), "norm_ref": self.norm_ref,
                 "campi": [c.come_dict() for c in self.campi]}
        if self.adozione != ADOZIONE_AUTOMATICA:
            forma["adozione"] = self.adozione
        if self.regole_incrociate:  # R16: solo se valorizzate (impronte precedenti invariate)
            forma["regole_incrociate"] = [r.come_dict() for r in self.regole_incrociate]
        return forma

    @classmethod
    def da_dict(cls, dati: Mapping[str, Any]) -> ProfiloMetadati:
        """Inverso di ``come_dict`` (profilo esportato, file di dati)."""
        try:
            campi = dati["campi"]
            if not isinstance(campi, list):
                raise ProfiloNonValido("`campi` deve essere un elenco")
            return cls(
                codice=str(dati["codice"]),
                versione=int(dati["versione"]),
                valido_dal=date.fromisoformat(str(dati["valido_dal"])),
                valido_al=(date.fromisoformat(str(dati["valido_al"]))
                           if dati.get("valido_al") else None),
                campi=tuple(CampoProfilo.da_dict(c) for c in campi),
                id=str(dati["id"]) if dati.get("id") else None,
                tipologia=str(dati["tipologia"]) if dati.get("tipologia") else None,
                norm_ref=str(dati["norm_ref"]) if dati.get("norm_ref") else None,
                composto_da=tuple(dict(r) for r in dati.get("composto_da") or ()),
                adozione=str(dati.get("adozione") or ADOZIONE_AUTOMATICA),
                regole_incrociate=tuple(RegolaIncrociata.da_dict(r)
                                        for r in dati.get("regole_incrociate") or ()),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ProfiloNonValido):
                raise
            raise ProfiloNonValido(f"profilo non valido: {exc}") from exc


def seleziona_profilo(versioni: Iterable[ProfiloMetadati], giorno: date) -> ProfiloMetadati | None:
    """Versione valida alla data; con più versioni valide vince la più alta (ordine totale)."""
    valide = [p for p in versioni if p.valido_il(giorno)]
    if not valide:
        return None
    return max(valide, key=lambda p: (p.versione, p.valido_dal))


#: Tipi che accettano un SOTTOINSIEME dei valori di ``stringa`` (revisione R11, T2).
TIPI_PIU_STRETTI_DI_STRINGA = frozenset({
    TipoCampo.ENUM, TipoCampo.CODICE_FISCALE, TipoCampo.PARTITA_IVA, TipoCampo.CODICE_IPA,
    TipoCampo.DATA, TipoCampo.DATA_ORA})


def tipo_piu_stretto(classe: TipoCampo, base: TipoCampo) -> bool:
    """Il tipo della classe accetta un sottoinsieme stretto dei valori di quello della
    tipologia (oggi: una forma o un elenco chiuso sopra un testo libero)."""
    return base is TipoCampo.STRINGA and classe in TIPI_PIU_STRETTI_DI_STRINGA


def obblighi(profilo: ProfiloMetadati | None) -> frozenset[str]:
    """Nomi dei campi obbligatori, anche a condizione (revisione R11, T1)."""
    if profilo is None:
        return frozenset()
    return frozenset(c.nome for c in profilo.campi if c.obbligatorio or c.obbligatorio_se)


def almeno_severo(nuovo: ProfiloMetadati | None, vecchio: ProfiloMetadati | None) -> bool:
    """Il profilo ``nuovo`` chiede almeno tutti gli obblighi di ``vecchio`` (verso
    «più sicuro» della tipologia e della sua versione: T1, VD-P-114). R16 (VD-C-158): e
    almeno tutte le sue regole incrociate (per codice)."""
    def regole(p: ProfiloMetadati | None) -> frozenset[str]:
        return frozenset(r.codice for r in p.regole_incrociate) if p else frozenset()
    return obblighi(vecchio) <= obblighi(nuovo) and regole(vecchio) <= regole(nuovo)


def restringi_campo(base: CampoProfilo, classe: CampoProfilo) -> CampoProfilo:
    """R11 (D07): il campo della classe con lo stesso nome di un campo della
    tipologia può solo RESTRINGERLO: obbligatorio se uno dei due lo è, sensibile se
    uno dei due lo è. Revisione T2 (VD-P-113), sempre il comportamento più severo:
      - stesso tipo enumerato: valori comuni (intersezione); se è vuota valgono i
        valori della tipologia (conflitto segnalato da ``conflitti_composizione``);
      - tipo della classe più stretto (elenco chiuso o forma sopra un testo libero
        della tipologia): si adotta il tipo della classe con i suoi valori;
      - tipo incompatibile o più largo: vale il tipo della tipologia (la norma),
        segnalato nel rapporto pre-deploy.
    Calcolo, condizioni e sottocampi restano quelli della tipologia: un ente non rende
    facoltativo un campo obbligatorio dell'Allegato 5."""
    obbligatorio = base.obbligatorio or classe.obbligatorio
    sensibile = base.sensibile or classe.sensibile
    descrizione = classe.descrizione or base.descrizione
    if tipo_piu_stretto(classe.tipo, base.tipo):
        return replace(base, tipo=classe.tipo, valori_ammessi=classe.valori_ammessi,
                       obbligatorio=obbligatorio, sensibile=sensibile, descrizione=descrizione)
    valori = base.valori_ammessi
    if base.tipo is TipoCampo.ENUM and classe.tipo is TipoCampo.ENUM and classe.valori_ammessi:
        valori = tuple(v for v in base.valori_ammessi if v in classe.valori_ammessi) or valori
    return replace(base, valori_ammessi=valori, obbligatorio=obbligatorio, sensibile=sensibile,
                   descrizione=descrizione)


def conflitti_composizione(tipologia: ProfiloMetadati,
                           classe: ProfiloMetadati) -> list[dict[str, Any]]:
    """Campi della classe omonimi di campi della tipologia e cosa succede con la
    composizione di R11 rispetto a quella di R05 (dove la classe sostituiva il campo).
    ``cambia_esito``: il versamento può essere accettato o rifiutato diversamente.
    Usata dal rapporto pre-deploy ``tools/profili/conflitti_classi.py``."""
    voci = []
    per_nome = {c.nome: c for c in tipologia.campi}
    for campo in classe.campi:
        base = per_nome.get(campo.nome)
        if base is None:
            continue
        motivi = []
        if base.obbligatorio and not campo.obbligatorio and not base.calcolato:
            motivi.append("obbligatorio per la tipologia, facoltativo per la classe: resta "
                          "obbligatorio")
        if campo.tipo is base.tipo:
            if base.tipo is TipoCampo.ENUM and campo.valori_ammessi:
                comuni = [v for v in base.valori_ammessi if v in campo.valori_ammessi]
                if not comuni:
                    motivi.append("valori della classe tutti fuori da quelli della tipologia: "
                                  "valgono i valori della tipologia")
                elif set(campo.valori_ammessi) - set(base.valori_ammessi):
                    motivi.append("valori della classe non ammessi dalla tipologia: tolti")
        elif tipo_piu_stretto(campo.tipo, base.tipo):
            pass                                  # restrizione mantenuta, come in R05
        else:
            motivi.append(f"tipo {campo.tipo.value} della classe incompatibile con "
                          f"{base.tipo.value} della tipologia: vale il tipo della tipologia")
        if base.calcolato or base.obbligatorio_se or base.sottocampi:
            if campo.tipo is not base.tipo or campo.obbligatorio != base.obbligatorio:
                motivi.append("campo calcolato, condizionato o strutturato della tipologia: "
                              "valgono le sue regole")
        voci.append({"campo": campo.nome, "tipo_tipologia": base.tipo.value,
                     "tipo_classe": campo.tipo.value, "motivi": motivi,
                     "cambia_esito": bool(motivi)})
    return voci


def componi_profili_r05(tipologia: ProfiloMetadati | None,
                        classe: ProfiloMetadati | None) -> ProfiloMetadati | None:
    """Composizione di R05 (la classe sostituiva il campo omonimo), SOLO per il rapporto
    pre-deploy che confronta l'esito prima e dopo R11; mai per versare."""
    if tipologia is None or classe is None:
        return componi_profili(tipologia, classe)
    per_nome = {c.nome: c for c in tipologia.campi}
    per_nome.update({c.nome: c for c in classe.campi})
    composto = componi_profili(tipologia, classe)
    assert composto is not None  # entrambi presenti
    return replace(composto, campi=tuple(per_nome.values()))


def componi_profili(tipologia: ProfiloMetadati | None,
                    classe: ProfiloMetadati | None) -> ProfiloMetadati | None:
    """Profilo applicato: campi della tipologia + campi propri della classe.
    Uno solo dei due: quello. Nessuno: None (§8.14).

    R11 (D07): un campo della classe con il nome di un campo della tipologia
    lo restringe soltanto (``restringi_campo``); prima di R11 lo sostituiva e
    poteva renderlo facoltativo."""
    if tipologia is None:
        return classe
    if classe is None:
        return tipologia
    per_nome = {c.nome: c for c in tipologia.campi}
    for campo in classe.campi:
        base = per_nome.get(campo.nome)
        per_nome[campo.nome] = campo if base is None else restringi_campo(base, campo)
    return ProfiloMetadati(
        codice=classe.codice,
        versione=classe.versione,
        valido_dal=max(classe.valido_dal, tipologia.valido_dal),
        campi=tuple(per_nome.values()),
        valido_al=classe.valido_al,
        id=classe.id,
        tipologia=tipologia.tipologia,
        norm_ref=tipologia.norm_ref,
        composto_da=(tipologia.riferimento(), classe.riferimento()),
        adozione=tipologia.adozione,
        # R16 (VD-C-158): le regole incrociate della tipologia valgono anche composte
        regole_incrociate=tipologia.regole_incrociate,
    )


# ── Validazione dei tipi ────────────────────────────────────────────
# Ogni funzione restituisce None se il valore è ammesso, altrimenti il motivo.
# I motivi NON contengono mai il valore (può essere un dato sensibile).

_OMOCODIA = "LMNPQRSTUV"
_FORMA_CF_PERSONA = re.compile(
    r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$"
)
_DISPARI_CF = dict(
    zip(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        (
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            2,
            4,
            18,
            20,
            11,
            3,
            6,
            8,
            12,
            14,
            16,
            10,
            22,
            25,
            24,
            23,
        ),
        strict=True,
    )
)
#: Codice IPA (Indice dei domicili digitali della PA): lettere, cifre e «_».
_FORMA_IPA = re.compile(r"^[A-Za-z0-9_]{2,20}$")


def _controllo_cf_persona(cf: str) -> bool:
    somma = 0
    for posizione, carattere in enumerate(cf[:15]):
        if posizione % 2 == 0:
            somma += _DISPARI_CF[carattere]
        else:
            somma += int(carattere) if carattere.isdigit() else ord(carattere) - ord("A")
    return chr(ord("A") + somma % 26) == cf[15]


def _controllo_partita_iva(piva: str) -> bool:
    """Cifra di controllo della partita IVA (11 cifre, algoritmo di Luhn modificato)."""
    somma = 0
    for i, cifra in enumerate(piva[:10]):
        n = int(cifra)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        somma += n
    return (10 - somma % 10) % 10 == int(piva[10])


def motivo_partita_iva(valore: Any) -> str | None:
    if not isinstance(valore, str) or not re.fullmatch(r"\d{11}", valore.strip()):
        return "attesa una partita IVA di 11 cifre"
    if not _controllo_partita_iva(valore.strip()):
        return "la cifra di controllo della partita IVA non corrisponde"
    return None


def motivo_codice_fiscale(valore: Any) -> str | None:
    """Codice fiscale di persona fisica (16 caratteri) o di soggetto diverso (11 cifre)."""
    if not isinstance(valore, str):
        return "atteso un codice fiscale"
    cf = re.sub(r"\s+", "", valore).upper()
    if len(cf) == 11 and cf.isdigit():
        return (
            None
            if _controllo_partita_iva(cf)
            else "la cifra di controllo del codice fiscale numerico non corrisponde"
        )
    if len(cf) != 16 or not _FORMA_CF_PERSONA.match(cf):
        return "atteso un codice fiscale di 16 caratteri o di 11 cifre"
    giorno = int(cf[9:11].translate(str.maketrans(_OMOCODIA, "0123456789")))
    if not (1 <= giorno <= 31 or 41 <= giorno <= 71):
        return "il codice fiscale indica un giorno di nascita impossibile"
    if not _controllo_cf_persona(cf):
        return "il carattere di controllo del codice fiscale non corrisponde"
    return None


_FORMA_DATA_ORA = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?$")


def _motivo_elementi(campo: CampoProfilo, n: int) -> str | None:
    if campo.min_elementi is not None and n < campo.min_elementi:
        return f"attesi almeno {campo.min_elementi} elementi"
    if campo.max_elementi is not None and n > campo.max_elementi:
        return f"attesi al più {campo.max_elementi} elementi"
    return None


def _lettore(oggetto: Mapping[str, Any]) -> Lettore:
    def leggi(nome: str) -> tuple[bool, Any]:
        valore = oggetto.get(nome)
        return (not vuoto(valore), valore)
    return leggi


def problemi_struttura(sottocampi: Iterable[CampoProfilo], oggetto: Mapping[str, Any],
                       prefisso: str = "") -> list[str]:
    """R11: problemi di un oggetto rispetto ai sottocampi (obblighi, anche a
    condizione sui fratelli, e tipi). Mai i valori nei messaggi."""
    leggi = _lettore(oggetto)
    problemi: list[str] = []
    for campo in sottocampi:
        trovato, valore = leggi(campo.nome)
        percorso = f"{prefisso}{campo.nome}"
        if not trovato:
            if campo.richiesto(leggi):
                condizione = next((c for c in campo.obbligatorio_se if c.vale(leggi)), None)
                problemi.append(f"{percorso}: obbligatorio"
                                + (f" {condizione.descrizione()}" if condizione and
                                   not campo.obbligatorio else ""))
            continue
        motivo = motivo_tipo(campo, valore, leggi)
        if motivo:
            problemi.append(f"{percorso}: {motivo}")
    return problemi


def _motivo_strutturato(campo: CampoProfilo, valore: Any) -> str | None:
    problemi: list[str] = []
    if campo.tipo is TipoCampo.STRUTTURA:
        if not isinstance(valore, Mapping):
            return "atteso un oggetto"
        problemi = problemi_struttura(campo.sottocampi, valore)
    else:
        if not isinstance(valore, list) or not all(isinstance(v, Mapping) for v in valore):
            return "atteso un elenco di oggetti"
        motivo = _motivo_elementi(campo, len(valore))
        if motivo:
            return motivo
        for i, elemento in enumerate(valore, start=1):
            problemi += problemi_struttura(campo.sottocampi, elemento, f"[{i}].")
        lettori = [_lettore(e) for e in valore]
        for condizione in campo.almeno_uno:
            if not any(condizione.vale(leggi) for leggi in lettori):
                problemi.append(f"serve almeno un elemento con {condizione.descrizione()[3:]}")
    if not problemi:
        return None
    altri = len(problemi) - _PROBLEMI_MAX
    return "; ".join(problemi[:_PROBLEMI_MAX]) + (f" (e altri {altri})" if altri > 0 else "")


def motivo_tipo(campo: CampoProfilo, valore: Any, leggi: Lettore | None = None) -> str | None:
    """None se il valore rispetta il tipo del campo, altrimenti il motivo (senza il valore).

    ``leggi`` (R11) legge i campi fratelli per i valori ammessi a condizione."""
    tipo = campo.tipo
    if tipo in TIPI_STRUTTURATI:
        return _motivo_strutturato(campo, valore)
    if tipo is TipoCampo.STRINGA:
        return None if isinstance(valore, str) else "atteso un testo"
    if tipo is TipoCampo.DATA_ORA:
        if isinstance(valore, str) and _FORMA_DATA_ORA.match(valore):
            try:
                datetime.fromisoformat(valore.replace("Z", "+00:00"))
                return None
            except ValueError:
                pass
        return "attese data e ora nel formato AAAA-MM-GGThh:mm[:ss][fuso]"
    if tipo is TipoCampo.DATA:
        if isinstance(valore, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valore):
            try:
                date.fromisoformat(valore)
                return None
            except ValueError:
                pass
        return "attesa una data nel formato AAAA-MM-GG"
    if tipo is TipoCampo.INTERO:
        if isinstance(valore, bool):
            return "atteso un numero intero"
        if isinstance(valore, int) or (isinstance(valore, str) and re.fullmatch(r"-?\d+", valore)):
            numero = int(valore)
            if campo.minimo is not None and numero < campo.minimo:
                return f"atteso un intero non minore di {campo.minimo}"
            if campo.massimo is not None and numero > campo.massimo:
                return f"atteso un intero non maggiore di {campo.massimo}"
            return None
        return "atteso un numero intero"
    if tipo is TipoCampo.DECIMALE:
        if isinstance(valore, bool):
            return "atteso un numero decimale"
        if isinstance(valore, int | float):
            return None if Decimal(str(valore)).is_finite() else "atteso un numero decimale"
        if isinstance(valore, str) and re.fullmatch(r"-?\d+(\.\d+)?", valore):
            try:
                Decimal(valore)
                return None
            except InvalidOperation:  # pragma: no cover — esclusa dall'espressione
                pass
        return "atteso un numero decimale (separatore «.»)"
    if tipo is TipoCampo.BOOLEANO:
        return None if isinstance(valore, bool) else "atteso un valore booleano (true/false)"
    if tipo is TipoCampo.ENUM:
        ammessi = campo.ammessi(leggi) if leggi is not None else campo.valori_ammessi
        if isinstance(valore, str) and valore in ammessi:
            return None
        return f"valore non fra quelli ammessi ({', '.join(ammessi)})"
    if tipo is TipoCampo.CODICE_FISCALE:
        return motivo_codice_fiscale(valore)
    if tipo is TipoCampo.PARTITA_IVA:
        return motivo_partita_iva(valore)
    if tipo is TipoCampo.ELENCO:
        if isinstance(valore, list) and all(isinstance(v, str) for v in valore):
            return _motivo_elementi(campo, len(valore))
        return "atteso un elenco di testi"
    if tipo is TipoCampo.CODICE_IPA:
        if isinstance(valore, str) and _FORMA_IPA.match(valore):
            return None
        return "atteso un codice IPA (da 2 a 20 fra lettere, cifre e «_»)"
    return f"tipo non gestito: {tipo}"  # pragma: no cover
