# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/domain/eventi.py
Catalogo degli eventi per i clienti e proiezione dagli eventi Kafka — contratto C3.

Dominio puro (REGOLE §7.2): nessun I/O, nessun orologio.

Un evento per i clienti ha un ``tipo`` stabile (al passato, minuscolo, con il
punto), una risorsa (``risorsa_tipo``, ``risorsa_id``) e ``dati`` ridotti al
necessario per l'integrazione. I ``dati`` si costruiscono con un ELENCO
CHIUSO di campi per tipo (mai l'evento Kafka intero): niente chiavi interne di
storage, niente indice del PdV, niente nomi di file né dati personali
(REGOLE §19.9). Il cliente legge il resto dall'API con l'identificativo.

Un tipo nuovo = una voce di ``TIPI_EVENTO`` + una funzione di proiezione +
i test; il nome di un tipo pubblicato non cambia.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

#: Lunghezza massima dei testi copiati nei dati (messaggi d'errore).
LUNGHEZZA_MASSIMA_TESTO = 500
#: Numero massimo di errori riportati in versamento.rifiutato.
ERRORI_MASSIMI = 20


class EventoNonValido(ValueError):
    """L'evento Kafka non si può trasformare in evento per il cliente (motivo
    definitivo: un nuovo tentativo non cambia l'esito)."""


@dataclass(frozen=True)
class TipoEvento:
    tipo: str
    risorsa_tipo: str
    descrizione: str


@dataclass(frozen=True)
class EventoCliente:
    """Evento pronto da salvare in ``public.eventi_cliente``.

    ``tutti_gli_enti``: evento del servizio, non di un ente (``catena.checkpoint``):
    si registra UNA volta come evento globale (``tenant_id`` NULL nel database),
    letto da ogni ente nell'elenco degli eventi, con consegne webhook solo per chi
    ha sottoscritto il tipo (R04 correzioni, rev4 rilievo 10); ``tenant_id`` è vuoto."""

    tenant_id: str
    tipo: str
    risorsa_tipo: str
    risorsa_id: str
    dati: dict[str, Any] = field(default_factory=dict)
    tutti_gli_enti: bool = False


TIPI_EVENTO: dict[str, TipoEvento] = {t.tipo: t for t in (
    TipoEvento("versamento.ricevuto", "versamento",
               "Il PdV è stato preso in carico (stato RICEVUTO)."),
    TipoEvento("versamento.validato", "versamento",
               "La verifica del PdV è terminata senza errori bloccanti."),
    TipoEvento("versamento.rifiutato", "versamento",
               "La verifica del PdV ha trovato errori bloccanti: il PdV non sarà conservato."),
    TipoEvento("versamento.conservato", "versamento",
               "Il PdA del PdV è nel registro delle impronte ed è coperto da un checkpoint "
               "firmato (R04, `fase` = CONSERVATO; gli eventi dei produttori R03 hanno "
               "`fase` = PDA_SU_STORAGE)."),
    TipoEvento("caricamento.completato", "caricamento",
               "Il file della sessione di caricamento è verificato e utilizzabile nel versamento."),
    TipoEvento("caricamento.errore", "caricamento",
               "La verifica del file della sessione di caricamento è fallita."),
    TipoEvento("pdd.pronto", "pdd",
               "Il pacchetto di distribuzione richiesto (anche un'esibizione: tipo "
               "ESIBIZIONE) è pronto per lo scaricamento."),
    # R04 (contratto K1): un evento per OGNI ente attivo a ogni checkpoint
    TipoEvento("catena.checkpoint", "checkpoint",
               "Nuovo checkpoint firmato del registro delle impronte (dimensione, radice, "
               "testo della nota firmata): conservarlo permette di chiedere in seguito "
               "una prova di consistenza."),
    # R04 (VD-C-104): preavviso prima dell'eliminazione delle copie di transito
    # di un PdV rifiutato (stato ERRORE); dopo la scadenza non è più rielaborabile.
    TipoEvento("versamento.staging_scaduto", "versamento",
               "Le copie di transito del PdV rifiutato saranno eliminate alla data "
               "`scade_il`: dopo, il versamento non si potrà più rielaborare."),
    # R04 (verifica continua): un oggetto del PdA dell'ente, riletto dallo storage
    # conservativo, non corrisponde alla sua impronta o alla sua foglia, manca,
    # ha perso la retention o non si legge ripetutamente. Una risorsa per
    # OCCORRENZA (PdA + istante della verifica): due anomalie dello stesso PdA in
    # momenti diversi sono due eventi distinti.
    TipoEvento("integrita.anomalia", "verifica_integrita",
               "La verifica periodica dell'integrità ha trovato un'anomalia su un PdA "
               "dell'ente (esito DIVERSO, MANCANTE, RETENTION_ASSENTE o ERRORE_LETTURA "
               "ripetuto). Il conservatore è già allertato e gestisce il ripristino."),
    # R05 (contratto Q2): ciclo di vita (servizio ciclo-vita, outbox con topic = tipo)
    TipoEvento("pratica.cambio_stato", "pratica",
               "Una pratica del ciclo di vita (rettifica, annullamento, scarto, riversamento, "
               "restituzione) è passata a un nuovo stato. Un evento per ogni passo "
               "(`versione`); dettaglio e azioni consentite dall'API delle pratiche."),
    TipoEvento("versamento.annullato", "versamento",
               "Il PdV è stato annullato (annullamento logico: nessuna cancellazione prima "
               "dello scarto; un PdA conservato resta esibibile)."),
    TipoEvento("versamento.rettificato", "versamento",
               "Una rettifica dei metadati del PdV è conservata come nuovo PdV collegato "
               "(`pdv_rettifica_id`); l'originale resta immutato."),
    TipoEvento("scarto.eseguito", "pratica",
               "Lo scarto autorizzato è stato eseguito: conteggi dei PdA scartati e "
               "bloccati, impronta del verbale di scarto conservato."),
    # R07 (D13, D17): avvisi automatici all'ente (libs.shared.avvisi, outbox con
    # topic = tipo); una risorsa per avviso (id della riga di avvisi_automatici)
    TipoEvento("quota.soglia_raggiunta", "avviso",
               "I versamenti del giorno hanno raggiunto l'80% o il 100% della quota "
               "giornaliera dell'ente (`soglia`); al 100% i successivi sono rifiutati o "
               "rallentati (`effetto`). `piano_suggerito`: piano con un limite adeguato."),
    TipoEvento("idempotenza.chiave_omessa", "avviso",
               "Nell'ultima settimana l'ente ha versato senza intestazione Idempotency-Key "
               "(`versamenti`): dopo `fino_al` quelle richieste saranno rifiutate (428 VRS-125)."),
    # R09 (D20, D21, D23): avvisi del ciclo di vita (``sottotipo``)
    TipoEvento("ciclo_vita.avviso", "avviso",
               "Avviso del ciclo di vita (`sottotipo`): PROPOSTA_SCARTO (proposta di scarto "
               "automatica in bozza da controllare), RIPRESA_SCARTO (scarto incompleto ripreso "
               "da solo dopo la rimozione del blocco), ANNULLAMENTO_DA_APPROVARE (serve una "
               "seconda persona dell'ente), ANNULLAMENTO_RESPINTO (annullamento non applicato "
               "perché qualcosa è cambiato prima dell'applicazione), PRATICA_RIPRESA (pratica "
               "ferma dopo ripetuti errori ripresa dal conservatore). `pratica_id` per i "
               "dettagli."),
    TipoEvento("riversamento.pronto", "pratica",
               "I pacchetti del riversamento in uscita o della restituzione sono pronti: "
               "manifesto e collegamenti dalla pratica."),
)}


def tipi_ammessi() -> list[str]:
    """Tipi di evento in ordine stabile (per messaggi d'errore e documentazione)."""
    return sorted(TIPI_EVENTO)


def tipi_sconosciuti(tipi: list[str] | tuple[str, ...]) -> list[str]:
    """Tipi non presenti nel catalogo, nell'ordine ricevuto e senza ripetizioni."""
    visti: list[str] = []
    for t in tipi:
        if t not in TIPI_EVENTO and t not in visti:
            visti.append(t)
    return visti


# ── Lettura difensiva dell'evento Kafka ─────────────────────────────

def _uuid(evento: Mapping[str, Any], campo: str) -> str:
    try:
        return str(UUID(str(evento.get(campo))))
    except (ValueError, TypeError):
        raise EventoNonValido(f"campo {campo!r} assente o non UUID") from None


def _testo(valore: Any, massimo: int = LUNGHEZZA_MASSIMA_TESTO) -> str | None:
    if valore is None:
        return None
    testo = str(valore).strip()
    return testo[:massimo] if testo else None


def _intero(valore: Any) -> int | None:
    if isinstance(valore, bool):
        return None
    if isinstance(valore, int):
        return valore
    try:
        return int(str(valore))
    except (TypeError, ValueError):
        return None


def _senza_vuoti(dati: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dati.items() if v is not None}


def _chiave_ud(indice: Any) -> dict[str, str] | None:
    if not isinstance(indice, Mapping):
        return None
    chiave = indice.get("chiave")
    if not isinstance(chiave, Mapping):
        return None
    ridotta = _senza_vuoti({k: _testo(chiave.get(k), 100) for k in ("registro", "anno", "numero")})
    return ridotta or None


# ── Proiezioni per tipo ─────────────────────────────────────────────

def _versamento_ricevuto(evento: Mapping[str, Any]) -> dict[str, Any]:
    avvisi = [
        _testo(a.get("codice"), 30) for a in evento.get("avvisi") or []
        if isinstance(a, Mapping) and a.get("codice")
    ]
    return _senza_vuoti({
        "pdv_id": _uuid(evento, "pdv_id"),
        "classe_documentale": _testo(evento.get("classe_documentale"), 100),
        "canale": _testo(evento.get("canale"), 30),
        "num_documenti": _intero(evento.get("num_documenti")),
        "chiave": _chiave_ud(evento.get("indice")),
        "ricevuto_il": _testo(evento.get("received_at"), 40),
        "avvisi": avvisi or None,
    })


def _versamento_validato(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "pdv_id": _uuid(evento, "pdv_id"),
        "classe_documentale": _testo(evento.get("classe_documentale"), 100),
        "chiave": _chiave_ud(evento.get("indice")),
    })


def _versamento_rifiutato(evento: Mapping[str, Any]) -> dict[str, Any]:
    errori: list[dict[str, Any]] = []
    for r in evento.get("risultati") or []:
        if not isinstance(r, Mapping) or r.get("valido") is not False:
            continue
        errori.append(_senza_vuoti({
            "codice": _testo(r.get("codice"), 30),
            "messaggio": _testo(r.get("msg") or r.get("messaggio")),
        }))
        if len(errori) >= ERRORI_MASSIMI:
            break
    return _senza_vuoti({
        "pdv_id": _uuid(evento, "pdv_id"),
        "classe_documentale": _testo(evento.get("classe_documentale"), 100),
        "chiave": _chiave_ud(evento.get("indice")),
        "errori": errori,
    })


def _versamento_conservato(evento: Mapping[str, Any]) -> dict[str, Any]:
    # R04 (contratto K1, VD-C-097): l'evento parte quando il PdA è nel registro
    # ed è coperto da un checkpoint; porta PdA, foglia e checkpoint. Un evento
    # senza ``checkpoint_dimensione`` (produttore R03) resta «PDA_SU_STORAGE».
    checkpoint = _intero(evento.get("checkpoint_dimensione"))
    return _senza_vuoti({
        "pdv_id": _uuid(evento, "pdv_id"),
        "aip_id": _uuid(evento, "aip_id"),
        "urn": _testo(evento.get("urn"), 200),
        "impronta_sha256": _testo(evento.get("hash_sha256"), 64),
        "conservato_il": _testo(evento.get("data_conservazione"), 40),
        "pda": _testo(evento.get("pda"), 200),
        "foglia_indice": _intero(evento.get("foglia_indice")),
        "checkpoint_dimensione": checkpoint,
        "fase": "CONSERVATO" if checkpoint is not None else "PDA_SU_STORAGE",
    })


def _impronte(valore: Any) -> dict[str, str] | None:
    if not isinstance(valore, Mapping):
        return None
    ridotte = {str(k)[:20]: str(v)[:128] for k, v in valore.items()
               if isinstance(v, str) and v}
    return ridotte or None


def _errore_problema(valore: Any) -> dict[str, Any] | None:
    """Solo codice e titolo di un problem+json (il dettaglio può citare nomi di file)."""
    if not isinstance(valore, Mapping):
        return None
    ridotto = _senza_vuoti({
        "codice": _testo(valore.get("codice"), 30),
        "title": _testo(valore.get("title") or valore.get("titolo"), 200),
    })
    return ridotto or None


def _caricamento(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "caricamento_id": _uuid(evento, "caricamento_id"),
        "stato": _testo(evento.get("stato"), 30),
        "dimensione": _intero(evento.get("dimensione")),
        "formato_rilevato": _testo(evento.get("formato_rilevato"), 100),
        "impronte": _impronte(evento.get("impronte")),
        "errore": _errore_problema(evento.get("errore")),
    })


def _pdd_pronto(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "pdd_id": _uuid(evento, "pdd_id"),
        "tipo": _testo(evento.get("tipo"), 30),
        "scade_il": _testo(evento.get("scade_il"), 40),
    })


_RADICE_B64 = 64


def _catena_checkpoint(evento: Mapping[str, Any]) -> dict[str, Any]:
    """Solo dati pubblici del registro (hash, dimensione, nota firmata)."""
    return _senza_vuoti({
        "dimensione": _intero(evento.get("dimensione")),
        "radice": _testo(evento.get("radice"), _RADICE_B64),
        "emesso_il": _testo(evento.get("emesso_il"), 40),
        "testo": _testo(evento.get("testo"), 4000),
    })


def _versamento_staging_scaduto(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "pdv_id": _uuid(evento, "pdv_id"),
        "stato": _testo(evento.get("stato"), 30),
        "scade_il": _testo(evento.get("scade_il"), 40),
        "giorni_conservazione": _intero(evento.get("giorni_conservazione")),
        "preavviso_giorni": _intero(evento.get("preavviso_giorni")),
        "avvisato_il": _testo(evento.get("avvisato_il"), 40),
        # R07 (D15): pagina della console da cui si rielabora
        "rielabora": _testo(evento.get("rielabora"), 500),
    })


#: Oggetti riportati al più in integrita.anomalia (un PdA può avere molti file).
OGGETTI_ANOMALIA_MASSIMI = 50
_COMPONENTI_ANOMALIA = frozenset({"PDA", "IPDA", "FILE", "RDV"})


def _quota_soglia(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "quota": _testo(evento.get("quota"), 30),
        "soglia": _intero(evento.get("soglia")),
        "limite": _intero(evento.get("limite")),
        "utilizzo": _intero(evento.get("utilizzo")),
        "giorno": _testo(evento.get("giorno"), 10),
        "piano": _testo(evento.get("piano"), 50),
        "piano_suggerito": _testo(evento.get("piano_suggerito"), 50),
        "effetto": _testo(evento.get("effetto"), 30),
    })


def _idempotenza_mancante(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "versamenti": _intero(evento.get("versamenti")),
        "dal": _testo(evento.get("dal"), 40),
        "al": _testo(evento.get("al"), 40),
        "fino_al": _testo(evento.get("fino_al"), 10),
        "settimana": _testo(evento.get("settimana"), 10),
    })


def _ciclo_vita_avviso(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "sottotipo": _testo(evento.get("sottotipo"), 40),
        "pratica_id": _testo(evento.get("pratica_id"), 40),
        "pdv_id": _testo(evento.get("pdv_id"), 40),
        "selezionabili": _intero(evento.get("selezionabili")),
        "bloccati": _intero(evento.get("bloccati")),
        "ripresa": _intero(evento.get("ripresa")),
        "pda_sbloccati": _intero(evento.get("pda_sbloccati")),
        "pda_incompleti": _intero(evento.get("pda_incompleti")),
    })


def _integrita_anomalia(evento: Mapping[str, Any]) -> dict[str, Any]:
    """Solo identificativi, esiti e impronte (nessun nome di file né chiave di
    storage, che il produttore già non mette: §19.9). Il motivo tecnico resta nel
    registro delle verifiche, consultabile via API."""
    oggetti: list[dict[str, Any]] = []
    for o in evento.get("oggetti") or []:
        if not isinstance(o, Mapping):
            continue
        componente = _testo(o.get("componente"), 10)
        oggetti.append(_senza_vuoti({
            "componente": componente if componente in _COMPONENTI_ANOMALIA else None,
            "documento_id": _uuid(o, "documento_id") if o.get("documento_id") else None,
            "esito": _testo(o.get("esito"), 30),
            "impronta_attesa": _testo(o.get("impronta_attesa"), 64),
            "impronta_trovata": _testo(o.get("impronta_trovata"), 64),
        }))
        if len(oggetti) >= OGGETTI_ANOMALIA_MASSIMI:
            break
    ripetuto = evento.get("errore_lettura_ripetuto")
    return _senza_vuoti({
        "aip_id": _uuid(evento, "aip_id"),
        "pda": _testo(evento.get("pda"), 200),
        "esito": _testo(evento.get("esito"), 30),
        "verificato_il": _testo(evento.get("verificato_il"), 40),
        "tentativo": _intero(evento.get("tentativo")),
        "errore_lettura_ripetuto": ripetuto if isinstance(ripetuto, bool) else None,
        "oggetti": oggetti or None,
    })


def _id_occorrenza_anomalia(evento: Mapping[str, Any]) -> str:
    """``<aip_id>@<verificato_il>``: una risorsa per occorrenza, così la deduplica
    per (ente, tipo, risorsa) scarta solo le riconsegne Kafka, non le anomalie
    successive dello stesso PdA."""
    aip_id = _uuid(evento, "aip_id")
    istante = _testo(evento.get("verificato_il"), 40)
    if not istante:
        raise EventoNonValido("campo 'verificato_il' assente")
    return f"{aip_id}@{istante}"


def _pratica_cambio_stato(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "pratica_id": _uuid(evento, "pratica_id"),
        "tipo": _testo(evento.get("tipo"), 30),
        "da_stato": _testo(evento.get("da_stato"), 40),
        "a_stato": _testo(evento.get("a_stato"), 40),
        "versione": _intero(evento.get("versione")),
        "il": _testo(evento.get("il"), 40),
    })


def _versamento_annullato(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "pdv_id": _uuid(evento, "pdv_id"),
        "pratica_id": _uuid(evento, "pratica_id"),
        "stato_precedente": _testo(evento.get("stato_precedente"), 30),
        "urn": _testo(evento.get("urn"), 200),
        "annullato_il": _testo(evento.get("annullato_il"), 40),
    })


def _versamento_rettificato(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "pdv_id": _uuid(evento, "pdv_id"),
        "pdv_rettifica_id": _uuid(evento, "pdv_rettifica_id"),
        "pratica_id": _uuid(evento, "pratica_id"),
        "urn": _testo(evento.get("urn"), 200),
        "urn_rettifica": _testo(evento.get("urn_rettifica"), 200),
        "rettificato_il": _testo(evento.get("rettificato_il"), 40),
    })


def _conteggi(valore: Any) -> dict[str, int] | None:
    """Conteggi della pratica: solo chiavi brevi con valori interi."""
    if not isinstance(valore, Mapping):
        return None
    ridotti = {str(k)[:40]: v for k, v in valore.items()
               if isinstance(v, int) and not isinstance(v, bool)}
    return dict(list(ridotti.items())[:20]) or None


def _scarto_eseguito(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "pratica_id": _uuid(evento, "pratica_id"),
        "conteggi": _conteggi(evento.get("conteggi")),
        "verbale_sha256": _testo(evento.get("verbale_sha256"), 64),
        "eseguito_il": _testo(evento.get("eseguito_il"), 40),
    })


def _riversamento_pronto(evento: Mapping[str, Any]) -> dict[str, Any]:
    return _senza_vuoti({
        "pratica_id": _uuid(evento, "pratica_id"),
        "tipo": _testo(evento.get("tipo"), 30),
        "conteggi": _conteggi(evento.get("conteggi")),
        "manifesto_sha256": _testo(evento.get("manifesto_sha256"), 64),
        "manifesto": _testo(evento.get("manifesto"), 200),
    })


def _id_cambio_stato(evento: Mapping[str, Any]) -> str:
    """``<pratica_id>@<versione>``: un evento per ogni passo della pratica."""
    versione = _intero(evento.get("versione"))
    if versione is None or versione < 1:
        raise EventoNonValido("campo 'versione' assente o non valido")
    return f"{_uuid(evento, 'pratica_id')}@{versione}"


def _id_rettifica(evento: Mapping[str, Any]) -> str:
    """``<pdv_id>@<pratica_id>``: più rettifiche dello stesso PdV nel tempo."""
    return f"{_uuid(evento, 'pdv_id')}@{_uuid(evento, 'pratica_id')}"


#: tipo → (campo dell'evento Kafka con l'id della risorsa, proiezione dei dati)
_PROIEZIONI: dict[str, tuple[str, Callable[[Mapping[str, Any]], dict[str, Any]]]] = {
    "versamento.ricevuto": ("pdv_id", _versamento_ricevuto),
    "versamento.validato": ("pdv_id", _versamento_validato),
    "versamento.rifiutato": ("pdv_id", _versamento_rifiutato),
    "versamento.conservato": ("pdv_id", _versamento_conservato),
    "caricamento.completato": ("caricamento_id", _caricamento),
    "caricamento.errore": ("caricamento_id", _caricamento),
    "pdd.pronto": ("pdd_id", _pdd_pronto),
    "catena.checkpoint": ("dimensione", _catena_checkpoint),
    "versamento.staging_scaduto": ("pdv_id", _versamento_staging_scaduto),
    "integrita.anomalia": ("aip_id", _integrita_anomalia),
    # R05 (contratto Q2)
    "pratica.cambio_stato": ("pratica_id", _pratica_cambio_stato),
    "versamento.annullato": ("pdv_id", _versamento_annullato),
    "versamento.rettificato": ("pdv_id", _versamento_rettificato),
    "scarto.eseguito": ("pratica_id", _scarto_eseguito),
    "riversamento.pronto": ("pratica_id", _riversamento_pronto),
    # R07 (D13, D17)
    "quota.soglia_raggiunta": ("avviso_id", _quota_soglia),
    "idempotenza.chiave_omessa": ("avviso_id", _idempotenza_mancante),
    # R09
    "ciclo_vita.avviso": ("avviso_id", _ciclo_vita_avviso),
}

#: Tipi con un identificativo di risorsa calcolato (non un solo campo UUID).
_ID_RISORSA: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "integrita.anomalia": _id_occorrenza_anomalia,
    "pratica.cambio_stato": _id_cambio_stato,
    "versamento.rettificato": _id_rettifica,
}

#: Tipi che riguardano TUTTI gli enti (nessun tenant_id nell'evento Kafka).
_PER_TUTTI_GLI_ENTI = frozenset({"catena.checkpoint"})


def proietta(tipo: str, evento: Any) -> EventoCliente:
    """Evento per il cliente dall'evento Kafka; ``EventoNonValido`` se impossibile.

    Il cliente si prende SOLO dal ``tenant_id`` dell'evento (tutti i produttori
    lo scrivono): un evento senza cliente non diventa visibile a nessuno.
    """
    if tipo not in TIPI_EVENTO or tipo not in _PROIEZIONI:
        raise EventoNonValido(f"tipo di evento sconosciuto: {tipo!r}")
    if not isinstance(evento, Mapping):
        raise EventoNonValido("l'evento non è un oggetto JSON")
    campo_risorsa, proiezione = _PROIEZIONI[tipo]
    if tipo in _PER_TUTTI_GLI_ENTI:
        dimensione = _intero(evento.get(campo_risorsa))
        if dimensione is None or dimensione < 0:
            raise EventoNonValido(f"campo {campo_risorsa!r} assente o non intero")
        return EventoCliente(tenant_id="", tipo=tipo,
                             risorsa_tipo=TIPI_EVENTO[tipo].risorsa_tipo,
                             risorsa_id=str(dimensione), dati=proiezione(evento),
                             tutti_gli_enti=True)
    tenant_id = _uuid(evento, "tenant_id")
    calcolo = _ID_RISORSA.get(tipo)
    risorsa_id = calcolo(evento) if calcolo else _uuid(evento, campo_risorsa)
    return EventoCliente(
        tenant_id=tenant_id,
        tipo=tipo,
        risorsa_tipo=TIPI_EVENTO[tipo].risorsa_tipo,
        risorsa_id=risorsa_id,
        dati=proiezione(evento),
    )
