# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/catalogo_v1.py — impostazioni e regole del versamento, versione 1
(R03; REGOLE_SVILUPPO §8.1, §8.6, §8.14; criticità VD-C-094, VD-C-083).

Impostazioni (codice → tipo, predefinito, riferimento normativo, locked):

  METADATI_MINIMI_PRINCIPALE      codici  7 metadati minimi        Allegato 5 LG AgID   locked
  METADATI_OBBLIGATORI            codici  nessuno                  (scelta dell'ente/classe)
  FORMATI_AMMESSI                 codici  nessuna restrizione      Allegato 2 LG AgID
  CLASSE_DIMENSIONE_MAX_MB        intero  100                      (scelta di servizio)
  IDEMPOTENZA_OBBLIGATORIA        bool    vero                     (VD-P-27)            locked
  IDEMPOTENZA_TRANSIZIONE_FINO_AL data    2027-03-31               (VD-P-27, da confermare)

I predefiniti riproducono il comportamento di R02 (§8.14): nessun formato
imposto se la classe non ne elenca, 100 MB per file, nessun metadato
obbligatorio oltre a quelli minimi. Fa eccezione l'idempotenza, resa obbligatoria
con un periodo di transizione per decisione VD-P-27.

Regole (codice stabile → fase, effetto predefinito):

  RGL-101 metadati minimi Allegato 5 sul documento principale   INDICE  BLOCCA (locked)
  RGL-102 metadati obbligatori della classe e del profilo        INDICE  BLOCCA
  RGL-103 metadati non previsti (conservati come versati)        INDICE  AVVISA
  RGL-104 dimensione massima per file della classe               FILE    BLOCCA
  RGL-105 formati ammessi dalla classe                           FILE    BLOCCA
  RGL-106 tipi dei metadati del profilo                          INDICE  BLOCCA
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from datetime import date
from typing import Any

from libs.verumdoc_rules.impostazioni import Impostazione, TipoImpostazione
from libs.verumdoc_rules.profili import motivo_tipo
from libs.verumdoc_rules.regole import (
    AmbienteRegole,
    CatalogoRegole,
    DatiVersamento,
    DocumentoDaValutare,
    Effetto,
    Fase,
    Regola,
    Riscontro,
    crea_registro,
)

VERSIONE_CATALOGO = "1"

# ── Impostazioni ────────────────────────────────────────────────────

METADATI_MINIMI_PRINCIPALE = "METADATI_MINIMI_PRINCIPALE"
METADATI_OBBLIGATORI = "METADATI_OBBLIGATORI"
FORMATI_AMMESSI = "FORMATI_AMMESSI"
CLASSE_DIMENSIONE_MAX_MB = "CLASSE_DIMENSIONE_MAX_MB"
IDEMPOTENZA_OBBLIGATORIA = "IDEMPOTENZA_OBBLIGATORIA"
IDEMPOTENZA_TRANSIZIONE_FINO_AL = "IDEMPOTENZA_TRANSIZIONE_FINO_AL"

IMPOSTAZIONI_V1: tuple[Impostazione, ...] = (
    Impostazione(
        METADATI_MINIMI_PRINCIPALE,
        TipoImpostazione.CODICI,
        predefinito=(
            "identificativo_documento",
            "modalita_formazione",
            "tipologia_documentale",
            "data_documento",
            "soggetto_produttore_id",
            "soggetto_produttore_nome",
            "oggetto",
        ),
        descrizione="Metadati minimi del documento principale",
        norm_ref="Linee guida AgID sul documento informatico (2021), Allegato 5",
        locked=True,
    ),
    Impostazione(
        METADATI_OBBLIGATORI,
        TipoImpostazione.CODICI,
        predefinito=(),
        descrizione="Metadati obbligatori aggiuntivi della classe documentale",
    ),
    Impostazione(
        FORMATI_AMMESSI,
        TipoImpostazione.CODICI,
        predefinito=(),
        descrizione=(
            "Formati (MIME) ammessi; elenco vuoto = ogni formato riconosciuto dal contenuto"
        ),
        norm_ref="Linee guida AgID sul documento informatico (2021), Allegato 2",
    ),
    Impostazione(
        CLASSE_DIMENSIONE_MAX_MB,
        TipoImpostazione.INTERO,
        predefinito=100,
        descrizione="Dimensione massima di un file della classe, in MB",
        minimo=1,
        # 5 GiB: stesso tetto delle sessioni di caricamento (CARICAMENTI_MAX_DIMENSIONE_MB)
        # e del predefinito della verifica (VALIDAZIONE_MAX_FILE_MB); R03 i1
        massimo=5120,
    ),
    Impostazione(
        IDEMPOTENZA_OBBLIGATORIA,
        TipoImpostazione.BOOLEANO,
        predefinito=True,
        descrizione="Intestazione Idempotency-Key obbligatoria su ogni versamento (VD-P-27)",
        locked=True,
    ),
    Impostazione(
        IDEMPOTENZA_TRANSIZIONE_FINO_AL,
        TipoImpostazione.DATA,
        predefinito=date(2027, 3, 31),
        descrizione=(
            "Ultimo giorno (ora italiana) in cui un versamento senza Idempotency-Key è "
            "accettato con una chiave derivata dal contenuto e un avviso (VD-P-27)"
        ),
    ),
)


# ── Funzioni d'appoggio (pure) ──────────────────────────────────────


def cerca_metadato(
    metadati: Mapping[str, Any] | None, nome: str, contenitore: str = "metadati_extra"
) -> tuple[bool, Any]:
    """Valore del metadato ``nome``: prima al primo livello di ``parametri``, poi nel
    contenitore dei metadati liberi. Restituisce (trovato, valore).

    Vale per i metadati della classe e del profilo (RGL-102, RGL-106), che lo
    schema dell'indice non prevede; NON per i minimi (``metadati_minimi_mancanti``)."""
    if not metadati:
        return False, None
    if nome in metadati and not _vuoto(metadati[nome]):
        return True, metadati[nome]
    liberi = metadati.get(contenitore)
    if isinstance(liberi, Mapping) and nome in liberi and not _vuoto(liberi[nome]):
        return True, liberi[nome]
    return False, None


def metadati_minimi_mancanti(
    metadati: Mapping[str, Any] | None, minimi: Iterable[str]
) -> list[str]:
    """Metadati minimi (``METADATI_MINIMI_PRINCIPALE``) mancanti o vuoti nel documento.

    DEFINIZIONE UNICA del controllo (R03 f1, difetto 3 del rapporto c2): la usano
    la regola RGL-101 nel gateway e il passo 6 della verifica in validation, con
    l'elenco fotografato nel PdV. I minimi sono campi dello schema dell'indice:
    si cercano SOLO al primo livello di ``parametri``; un valore dentro
    ``metadati_extra`` non li sostituisce (lì restano metadati liberi)."""
    return [nome for nome in minimi
            if not metadati or nome not in metadati or _vuoto(metadati[nome])]


def minimi_fotografati(fotografia: Any) -> tuple[str, ...] | None:
    """Elenco ``METADATI_MINIMI_PRINCIPALE`` della fotografia delle regole di un PdV
    (``pdv.regole_applicate``); None se la fotografia non lo contiene."""
    if not isinstance(fotografia, Mapping):
        return None
    impostazioni = fotografia.get("impostazioni")
    voce = impostazioni.get(METADATI_MINIMI_PRINCIPALE) if isinstance(impostazioni, Mapping) \
        else None
    valore = voce.get("valore") if isinstance(voce, Mapping) else None
    if not isinstance(valore, list | tuple) or not all(isinstance(v, str) for v in valore):
        return None
    return tuple(valore)


def minimi_predefiniti() -> tuple[str, ...]:
    """Predefinito normativo dei metadati minimi (per i PdV senza fotografia)."""
    voce = next(i for i in IMPOSTAZIONI_V1 if i.codice == METADATI_MINIMI_PRINCIPALE)
    return tuple(voce.predefinito)


def _vuoto(valore: Any) -> bool:
    if valore is None:
        return True
    if isinstance(valore, str):
        return not valore.strip()
    if isinstance(valore, list | tuple | dict):
        return len(valore) == 0
    return False


def limite_byte_classe(ambiente: AmbienteRegole) -> int:
    """Dimensione massima per file della classe, in byte (serve anche a interrompere
    un trasferimento in corso quando RGL-104 blocca)."""
    return int(ambiente.valore(CLASSE_DIMENSIONE_MAX_MB)) * 1024 * 1024


# ── Regole ──────────────────────────────────────────────────────────


def _metadati_minimi(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    minimi = ambiente.valore(METADATI_MINIMI_PRINCIPALE)
    for doc in dati.principali():
        for nome in metadati_minimi_mancanti(doc.metadati, minimi):
            yield Riscontro(
                    f"Metadato minimo dell'Allegato 5 mancante: {nome}",
                    campo=nome,
                    documento=doc.id,
                )


def _metadati_obbligatori(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    di_classe = set(ambiente.valore(METADATI_OBBLIGATORI))
    for doc in dati.principali():
        obbligatori = set(di_classe)
        if ambiente.profilo is not None:
            # R05: i campi «calcolati» (impronta, formato, numero allegati…) li valorizza
            # il servizio e non si chiedono al produttore (§8.15). R11: anche gli
            # obblighi a condizione (``obbligatorio_se``) sui metadati di QUESTO documento
            def leggi(nome: str, doc: DocumentoDaValutare = doc) -> tuple[bool, Any]:
                return cerca_metadato(doc.metadati, nome, dati.contenitore_libero)
            obbligatori |= {c.nome for c in ambiente.profilo.campi if c.richiesto(leggi)}
        for nome in sorted(obbligatori):
            trovato, _ = cerca_metadato(doc.metadati, nome, dati.contenitore_libero)
            if not trovato:
                yield Riscontro(
                    f"Metadato obbligatorio per la classe {dati.classe} mancante: {nome}",
                    campo=nome,
                    documento=doc.id,
                )


def _metadati_non_previsti(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    previsti = set(dati.campi_noti) | {dati.contenitore_libero}
    if ambiente.profilo is not None:
        previsti |= ambiente.profilo.nomi()
    for doc in dati.documenti:
        for nome in sorted(set(doc.metadati or {}) - previsti):
            yield Riscontro(
                f"Metadato {nome} non previsto dallo schema né dal profilo della classe: "
                "conservato così come versato",
                campo=nome,
                documento=doc.id,
            )


def _dimensione_massima(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    limite = limite_byte_classe(ambiente)
    mb = ambiente.valore(CLASSE_DIMENSIONE_MAX_MB)
    for doc in dati.documenti:
        if doc.dimensione_byte is not None and doc.dimensione_byte > limite:
            yield Riscontro(
                f"Il file {doc.nome_file} supera la dimensione massima della classe ({mb} MB)",
                campo="dimensione",
                documento=doc.id,
            )


def _formati_ammessi(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    ammessi = {dati.normalizza_formato(f) for f in ambiente.valore(FORMATI_AMMESSI)}
    if not ammessi:
        return
    for doc in dati.documenti:
        if doc.formato_rilevato is None or doc.atto_di_sistema:   # R09 (D24)
            continue
        rilevato = dati.normalizza_formato(doc.formato_rilevato)
        if rilevato not in ammessi:
            yield Riscontro(
                f"Formato {rilevato} del file {doc.nome_file} non ammesso dalla classe "
                f"(ammessi: {', '.join(sorted(ammessi))})",
                campo="formato",
                documento=doc.id,
            )


def _tipi_profilo(dati: DatiVersamento, ambiente: AmbienteRegole) -> Iterator[Riscontro]:
    profilo = ambiente.profilo
    if profilo is None:
        return
    for doc in dati.documenti:
        def leggi(nome: str, doc: DocumentoDaValutare = doc) -> tuple[bool, Any]:
            return cerca_metadato(doc.metadati, nome, dati.contenitore_libero)
        for campo in profilo.campi:
            trovato, valore = leggi(campo.nome)
            if not trovato:
                continue
            motivo = motivo_tipo(campo, valore, leggi)
            if motivo:
                yield Riscontro(
                    f"Metadato {campo.nome}: {motivo}", campo=campo.nome, documento=doc.id
                )
        # R16 (VD-C-158): condizioni fra campi diversi (solo i profili che le dichiarano)
        for regola in profilo.regole_incrociate:
            motivo = regola.problema(leggi)
            if motivo:
                yield Riscontro(f"Metadato {motivo}", campo=regola.elenco, documento=doc.id)


_ALLEGATO5 = "Linee guida AgID sul documento informatico (2021), Allegato 5"

CATALOGO_V1 = CatalogoRegole(
    VERSIONE_CATALOGO,
    [
        Regola(
            "RGL-101",
            "Metadati minimi dell'Allegato 5 sul documento principale",
            Fase.INDICE,
            Effetto.BLOCCA,
            _metadati_minimi,
            locked=True,
            norm_ref=_ALLEGATO5,
        ),
        Regola(
            "RGL-102",
            "Metadati obbligatori della classe documentale",
            Fase.INDICE,
            Effetto.BLOCCA,
            _metadati_obbligatori,
        ),
        Regola(
            "RGL-103",
            "Metadati non previsti (conservati come versati)",
            Fase.INDICE,
            Effetto.AVVISA,
            _metadati_non_previsti,
        ),
        Regola(
            "RGL-104",
            "Dimensione massima per file della classe",
            Fase.FILE,
            Effetto.BLOCCA,
            _dimensione_massima,
        ),
        Regola(
            "RGL-105",
            "Formati ammessi dalla classe documentale",
            Fase.FILE,
            Effetto.BLOCCA,
            _formati_ammessi,
            norm_ref="Linee guida AgID sul documento informatico (2021), Allegato 2",
        ),
        Regola(
            "RGL-106",
            "Tipi dei metadati del profilo della classe",
            Fase.INDICE,
            Effetto.BLOCCA,
            _tipi_profilo,
        ),
    ],
)

REGISTRO_V1 = crea_registro(IMPOSTAZIONI_V1, CATALOGO_V1)

__all__ = [
    "CATALOGO_V1",
    "CLASSE_DIMENSIONE_MAX_MB",
    "FORMATI_AMMESSI",
    "IDEMPOTENZA_OBBLIGATORIA",
    "IDEMPOTENZA_TRANSIZIONE_FINO_AL",
    "IMPOSTAZIONI_V1",
    "METADATI_MINIMI_PRINCIPALE",
    "METADATI_OBBLIGATORI",
    "REGISTRO_V1",
    "VERSIONE_CATALOGO",
    "cerca_metadato",
    "limite_byte_classe",
    "metadati_minimi_mancanti",
    "minimi_fotografati",
    "minimi_predefiniti",
]
