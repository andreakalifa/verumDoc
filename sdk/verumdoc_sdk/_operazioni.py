# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Descrizione delle chiamate dell'API (percorso, metodo, corpo, ripetibilità),
condivisa dai client sincrono e asincrono. Percorsi come li espone l'ingress.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from verumdoc_sdk._comune import (
    FabbricaCorpo,
    Richiesta,
    corpo_per_riferimento,
    intestazione_parte,
    valida_chiave_idempotenza,
)
from verumdoc_sdk.modelli import (
    ConsegnaPdD,
    IndiceUD,
    RispostaVersamento,
    StatoCaricamento,
    TipoPdD,
)

P_VERSAMENTO = "/api/v1/versamento/ud"
P_CARICAMENTI = "/api/v1/caricamenti"
P_EVENTI = "/api/v1/eventi"
P_WEBHOOK = "/api/v1/webhook"
P_DISTRIBUZIONE = "/api/v2/distribution"
P_VERSAMENTI_ELENCO = "/api/dashboard/versamenti"
P_IDENTITA = "/api/v1/me"

#: Stati finali di una sessione di caricamento.
STATI_FINALI_CARICAMENTO = frozenset({"COMPLETO", "ERRORE", "SCADUTO", "ANNULLATO"})
#: Stati finali di un PdD.
STATI_FINALI_PDD = frozenset({"PRONTO", "ERRORE", "SCADUTO"})


def _id(valore: Any) -> str:
    """Identificativo inserito nel percorso (codificato)."""
    return quote(str(valore), safe="")


@dataclass
class EsitoVersamento:
    """Risultato di un versamento con le informazioni del protocollo HTTP."""

    risposta: RispostaVersamento
    chiave_idempotenza: str
    ripetuto: bool = False
    stato_http: int = 202
    location: str | None = None
    deprecazione: str | None = None
    sunset: str | None = None
    caricamenti: dict[str, str] = field(default_factory=dict)

    @property
    def pdv_id(self) -> str:
        return self.risposta.pdv_id


@dataclass
class CaricamentoCompletato:
    """Sessione COMPLETA con l'impronta SHA-256 calcolata dall'SDK sui byte inviati
    (uguale a quella calcolata dal servizio, altrimenti ErroreIntegrita)."""

    caricamento_id: str
    sha256: str
    stato: StatoCaricamento


# ── Versamento ───────────────────────────────────────────────────────

def versamento_multipart(corpo: FabbricaCorpo, chiave: str, ripetibile: bool) -> Richiesta:
    return Richiesta("POST", P_VERSAMENTO, corpo=corpo,
                     intestazioni={"Idempotency-Key": valida_chiave_idempotenza(chiave)},
                     ripetibile=ripetibile)


def versamento_per_riferimento(indice: IndiceUD, caricamenti: Mapping[str, str],
                               chiave: str) -> Richiesta:
    return Richiesta("POST", P_VERSAMENTO, json=corpo_per_riferimento(indice, caricamenti),
                     intestazioni={"Idempotency-Key": valida_chiave_idempotenza(chiave)},
                     ripetibile=True)


def stato_versamento(pdv_id: str) -> Richiesta:
    return Richiesta("GET", f"/api/v1/versamento/{_id(pdv_id)}", ripetibile=True)


def rapporto_versamento(pdv_id: str) -> Richiesta:
    return Richiesta("GET", f"/api/v1/versamento/{_id(pdv_id)}/rdv",
                     intestazioni={"Accept": "application/xml, application/problem+json"},
                     ripetibile=True)


def elenco_versamenti(tenant_id: str | None, stato: str | None, cursore: str | None,
                      limite: int) -> Richiesta:
    """Pagina a cursore di ``GET /api/dashboard/versamenti`` (R03). Senza
    ``tenant_id`` l'ente è quello dell'identità (token o chiave API)."""
    params: dict[str, Any] = {"limit": int(limite)}
    if tenant_id is not None:
        params["tenant_id"] = str(tenant_id)
    if stato:
        params["stato"] = str(stato)
    if cursore:
        params["cursore"] = cursore
    return Richiesta("GET", P_VERSAMENTI_ELENCO, params=params, ripetibile=True)


def identita() -> Richiesta:
    return Richiesta("GET", P_IDENTITA, ripetibile=True)


# ── Sessioni di caricamento ──────────────────────────────────────────

def apri_caricamento(nome_file: str, dimensione: int, mime: str | None,
                     impronta_sha256: str | None, chiave: str) -> Richiesta:
    corpo: dict[str, Any] = {"nome_file": nome_file, "dimensione": int(dimensione)}
    if mime:
        corpo["mime_dichiarato"] = mime
    if impronta_sha256:
        corpo["impronta"] = {"algoritmo": "SHA-256", "valore": impronta_sha256}
    return Richiesta("POST", P_CARICAMENTI, json=corpo,
                     intestazioni={"Idempotency-Key": valida_chiave_idempotenza(chiave)},
                     ripetibile=True)


def stato_caricamento(caricamento_id: str) -> Richiesta:
    return Richiesta("GET", f"{P_CARICAMENTI}/{_id(caricamento_id)}", ripetibile=True)


def invia_parte(caricamento_id: str, numero: int, dati: bytes) -> Richiesta:
    def corpo() -> tuple[dict[str, Any], Any]:
        return {"content": dati}, lambda: None

    return Richiesta("PUT", f"{P_CARICAMENTI}/{_id(caricamento_id)}/parti/{int(numero)}",
                     corpo=corpo, ripetibile=True,
                     intestazioni={"Content-Type": "application/octet-stream",
                                   "X-Impronta-Parte": intestazione_parte(dati)})


def completa_caricamento(caricamento_id: str) -> Richiesta:
    # Ripetere «completa» non ha effetti doppi (200 se già COMPLETO)
    return Richiesta("POST", f"{P_CARICAMENTI}/{_id(caricamento_id)}/completa", ripetibile=True)


def annulla_caricamento(caricamento_id: str) -> Richiesta:
    return Richiesta("DELETE", f"{P_CARICAMENTI}/{_id(caricamento_id)}", ripetibile=True)


# ── Eventi e webhook ─────────────────────────────────────────────────

def elenco_eventi(dopo: int, limite: int | None, tipi: Sequence[str] | None) -> Richiesta:
    params: list[tuple[str, Any]] = [("dopo", int(dopo))]
    if limite is not None:
        params.append(("limite", int(limite)))
    params += [("tipo", t) for t in (tipi or ())]
    return Richiesta("GET", P_EVENTI, params=params, ripetibile=True)


def crea_webhook(url: str, tipi: Sequence[str], descrizione: str | None,
                 attiva: bool, chiave: str) -> Richiesta:
    corpo: dict[str, Any] = {"url": url, "tipi": list(tipi), "attiva": attiva}
    if descrizione is not None:
        corpo["descrizione"] = descrizione
    # R04 (VD-C-106): con la Idempotency-Key un ritentativo restituisce la STESSA
    # sottoscrizione (200, stesso segreto) invece di crearne un'altra: ripetibile
    return Richiesta("POST", P_WEBHOOK, json=corpo,
                     intestazioni={"Idempotency-Key": valida_chiave_idempotenza(chiave)},
                     ripetibile=True)


def elenco_webhook() -> Richiesta:
    return Richiesta("GET", P_WEBHOOK, ripetibile=True)


def leggi_webhook(webhook_id: str) -> Richiesta:
    return Richiesta("GET", f"{P_WEBHOOK}/{_id(webhook_id)}", ripetibile=True)


def modifica_webhook(webhook_id: str, campi: Mapping[str, Any]) -> Richiesta:
    ammessi = {"url", "tipi", "descrizione", "attiva"}
    estranei = set(campi) - ammessi
    if estranei or not campi:
        raise ValueError(f"Campi modificabili: {sorted(ammessi)} (ricevuti {sorted(campi)})")
    return Richiesta("PATCH", f"{P_WEBHOOK}/{_id(webhook_id)}", json=dict(campi),
                     ripetibile=True)


def elimina_webhook(webhook_id: str) -> Richiesta:
    return Richiesta("DELETE", f"{P_WEBHOOK}/{_id(webhook_id)}", ripetibile=True)


def elenco_consegne(webhook_id: str, stato: str | None, dopo: int,
                    limite: int | None) -> Richiesta:
    params: dict[str, Any] = {"dopo": int(dopo)}
    if stato:
        params["stato"] = stato
    if limite is not None:
        params["limite"] = int(limite)
    return Richiesta("GET", f"{P_WEBHOOK}/{_id(webhook_id)}/consegne", params=params,
                     ripetibile=True)


def riprova_consegna(webhook_id: str, consegna_id: int) -> Richiesta:
    # Non ripetuta in automatico: una seconda riprova riuscita darebbe 409 WH-011
    return Richiesta("POST", f"{P_WEBHOOK}/{_id(webhook_id)}/consegne/{int(consegna_id)}/riprova")


# ── Ricerca e distribuzione ──────────────────────────────────────────

def ricerca(query: str | None, classe_documentale: str | None, anno: str | int | None,
            data_da: datetime | None, data_a: datetime | None, limite: int,
            cursore: str | None) -> Richiesta:
    corpo: dict[str, Any] = {"limit": int(limite)}
    for chiave, valore in (("query", query), ("classe_documentale", classe_documentale),
                           ("anno", None if anno is None else str(anno)),
                           ("data_da", data_da.isoformat() if data_da else None),
                           ("data_a", data_a.isoformat() if data_a else None),
                           ("cursore", cursore)):
        if valore is not None:
            corpo[chiave] = valore
    # Ricerca: sola lettura, ripetibile anche se POST
    return Richiesta("POST", f"{P_DISTRIBUZIONE}/search", json=corpo, ripetibile=True)


def richiedi_pdd(aip_id: str, tipo: TipoPdD | str, motivo: str,
                 file_richiesti: Sequence[str] | None, durata_link_secondi: int | None,
                 consegna: ConsegnaPdD | str | None, chiave: str) -> Richiesta:
    corpo: dict[str, Any] = {"aip_id": str(aip_id), "tipo": str(TipoPdD(tipo)),
                             "motivo": motivo}
    if file_richiesti is not None:
        corpo["file_richiesti"] = list(file_richiesti)
    if durata_link_secondi is not None:
        corpo["url_expiry_seconds"] = int(durata_link_secondi)
    if consegna is not None:
        corpo["consegna"] = str(ConsegnaPdD(consegna))
    # R04 (VD-C-106): con la Idempotency-Key un ritentativo restituisce lo STESSO PdD
    return Richiesta("POST", f"{P_DISTRIBUZIONE}/richiedi", json=corpo,
                     intestazioni={"Idempotency-Key": valida_chiave_idempotenza(chiave)},
                     ripetibile=True)


def stato_pdd(pdd_id: str) -> Richiesta:
    return Richiesta("GET", f"{P_DISTRIBUZIONE}/pdd/{_id(pdd_id)}", ripetibile=True)
