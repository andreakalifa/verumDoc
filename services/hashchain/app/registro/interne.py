# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Foglie INTERNE del registro (R05, contratto Q3): fatti del ciclo di vita di un
PdA (scarto = tombstone, annullamento, rettifica; R09: chiusura con eccezioni,
minimizzazione, rinnovo del blocco WORM) e segmenti dell'audit nel WORM.

Servizio: hashchain. Riferimenti: contratti Q2/Q3 di R05; REGOLE_SVILUPPO §12.5
(idempotenza), §12.8 (ordine totale), §15.1 (audit nella transazione).

Una richiesta:
  1. fissa SUBITO il JSON canonico della foglia di formato 2 (``libs.catena.foglie``)
     e il suo hash: una richiesta ripetuta produce sempre la stessa foglia;
  2. scrive ``catena_foglie_interne`` e una riga in ``catena_coda``: la foglia
     entra nello STESSO ordine totale dei PdA (``sequenza``) e riceve dal
     sequenziatore lo stesso trattamento (indice, nodi, checkpoint, sigillo,
     controfirma del testimone, export nel WORM);
  2b. (revisione 2 di R05, S2) solo se il fatto RISULTA dal database: pratica
     del tipo giusto, dello stesso ente del PdA, nello stato in cui la foglia
     si chiede, e PdA nello stato atteso (``riscontro_ciclo_vita``). Un token
     di servizio rubato non basta a far firmare uno scarto mai avvenuto;
  3. è idempotente per ``(tipo, pda, riferimento.pratica)``: la stessa richiesta
     restituisce la stessa foglia; la stessa chiave con dati diversi è un
     conflitto (``ConflittoFoglia``, nessuna scrittura).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from libs.catena.canonico import json_canonico
from libs.catena.foglie import (
    TIPI_CICLO_VITA,
    controlla_riferimento,
    dati_foglia_ciclo_vita,
)
from libs.catena.rfc6962 import hash_foglia

#: Stati di una foglia interna (GET /interno/foglie/{id})
STATO_IN_CODA = "IN_CODA"
STATO_NEL_REGISTRO = "NEL_REGISTRO"          # indice assegnato, nessun checkpoint ancora
STATO_IN_CHECKPOINT = "IN_CHECKPOINT"        # coperta da un checkpoint emesso
STATO_CONTROFIRMATA = "CONTROFIRMATA"        # coperta da un checkpoint controfirmato


class ConflittoFoglia(ValueError):
    """La stessa chiave d'idempotenza è già stata usata con dati diversi."""

    def __init__(self, foglia_id: str) -> None:
        super().__init__("foglia già richiesta con la stessa pratica e dati diversi")
        self.foglia_id = foglia_id


class PdaNonTrovato(LookupError):
    """Il PdA indicato non esiste."""


class RiscontroMancante(ValueError):
    """Revisione 2 di R05 (S2): la foglia chiesta non corrisponde a un fatto
    registrato nel database (pratica del tipo giusto, dello stesso ente del PdA,
    nello stato in cui la foglia si chiede; PdA nello stato atteso). Nessuna
    scrittura: il registro non firma fatti che il database non conferma."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


#: Riscontro per tipo di foglia (revisione 2 di R05, S2). Una riga se la foglia
#: corrisponde a un fatto del ciclo di vita; i parametri sono il PdA (id), la
#: pratica (id) dal riferimento della foglia. Le pratiche sono di ``ciclo-vita``
#: (migrazione 037): la hashchain le LEGGE soltanto.
_RISCONTRO: dict[str, str] = {
    # Scarto: pratica SCARTO dello stesso ente con l'esecuzione AVVIATA, il PdA
    # fra i suoi oggetti con esito ELIMINATO e il PdA in ELIMINAZIONE (tutte le
    # versioni cancellate: un PdA ELIMINAZIONE_INCOMPLETA non ha tombstone).
    # Revisione 3 di R05 (T5): in più il FATTO dell'esecuzione in
    # ``esiti_esecuzione``, che solo il ruolo del lavoratore
    # (verumdoc_ciclo_vita_lavoratore) può scrivere: le righe precedenti le può
    # scrivere anche il login dell'API esposta, questa no (migrazione 043)
    "scarto": """
        SELECT 1 FROM public.pratiche p
        JOIN public.pratiche_oggetti o ON o.pratica_id = p.id AND o.aip_id = :aip
        JOIN public.aip a ON a.id = o.aip_id
        JOIN public.esiti_esecuzione e ON e.tipo = 'SCARTO' AND e.pratica_id = p.id
             AND e.aip_id = a.id AND e.tenant_id = a.tenant_id AND e.esito = 'ELIMINATO'
        WHERE p.id = :pratica AND p.tipo = 'SCARTO' AND p.tenant_id = a.tenant_id
          AND p.stato IN ('ESEGUIBILE', 'ESEGUITA_CON_ECCEZIONI') AND p.lavoro = 'ESEGUI'
          AND (p.dati -> 'esecuzione' ->> 'avviata_il') IS NOT NULL
          AND o.esito = 'ELIMINATO' AND a.stato::text = 'ELIMINAZIONE'""",
    # Annullamento: pratica ANNULLAMENTO (già ESEGUITA: l'annullamento è logico)
    # del PdV del PdA, stesso ente, PdV ANNULLATO. R06 (VD-C-119): in più il
    # FATTO del lavoratore (ANNULLAMENTO / ATTO_ARCHIVIATO in esiti_esecuzione,
    # scrivibile solo da verumdoc_ciclo_vita_lavoratore) con l'impronta dell'atto
    # archiviato nel WORM uguale a quella della foglia
    "annullamento": """
        SELECT 1 FROM public.pratiche p
        JOIN public.aip a ON a.id = :aip AND a.pdv_id = p.pdv_id AND a.tenant_id = p.tenant_id
        JOIN public.pdv v ON v.id = p.pdv_id
        JOIN public.esiti_esecuzione e ON e.tipo = 'ANNULLAMENTO' AND e.pratica_id = p.id
             AND e.aip_id = a.id AND e.tenant_id = a.tenant_id AND e.esito = 'ATTO_ARCHIVIATO'
             AND e.dettagli ->> 'atto_sha256' = :impronta
        WHERE p.id = :pratica AND p.tipo = 'ANNULLAMENTO' AND p.stato = 'ESEGUITA'
          AND v.stato::text = 'ANNULLATO'""",
    # Rettifica: pratica RETTIFICA del PdV del PdA, stesso ente, in versamento.
    # R06 (VD-C-119): in più il FATTO del lavoratore (RETTIFICA / PDV_VERSATO):
    # il PdV di rettifica che il lavoratore ha versato è quello della pratica e
    # del riferimento della foglia, l'atto versato ha l'impronta della foglia
    "rettifica": """
        SELECT 1 FROM public.pratiche p
        JOIN public.aip a ON a.id = :aip AND a.pdv_id = p.pdv_id AND a.tenant_id = p.tenant_id
        JOIN public.esiti_esecuzione e ON e.tipo = 'RETTIFICA' AND e.pratica_id = p.id
             AND e.aip_id = a.id AND e.tenant_id = a.tenant_id AND e.esito = 'PDV_VERSATO'
             AND e.dettagli ->> 'atto_sha256' = :impronta
             AND e.dettagli ->> 'pdv_rettifica' = p.pdv_risultato_id::text
             AND e.dettagli ->> 'pdv_rettifica' = :pdv_rettifica
        WHERE p.id = :pratica AND p.tipo = 'RETTIFICA' AND p.stato = 'IN_VERSAMENTO'""",
    # R09 (D20): chiusura con eccezioni chiesta dal conservatore e riverificata dal
    # lavoratore: fatto CHIUSO_CON_ECCEZIONI con l'impronta della foglia, oggetto
    # NON_COMPLETATO della pratica, PdA ancora in eliminazione incompleta
    "chiusura_eccezioni": """
        SELECT 1 FROM public.pratiche p
        JOIN public.pratiche_oggetti o ON o.pratica_id = p.id AND o.aip_id = :aip
        JOIN public.aip a ON a.id = o.aip_id
        JOIN public.esiti_esecuzione e ON e.tipo = 'SCARTO' AND e.pratica_id = p.id
             AND e.aip_id = a.id AND e.tenant_id = a.tenant_id
             AND e.esito = 'CHIUSO_CON_ECCEZIONI' AND e.dettagli ->> 'impronta' = :impronta
        WHERE p.id = :pratica AND p.tipo = 'SCARTO' AND p.tenant_id = a.tenant_id
          AND p.stato IN ('ESEGUITA_CON_ECCEZIONI', 'CHIUSA_CON_ECCEZIONI')
          AND o.esito = 'NON_COMPLETATO' AND a.stato::text = 'ELIMINAZIONE_INCOMPLETA'""",
    # R09 (D06): metadati del PdA scartato ridotti all'elenco minimo dal lavoratore
    # (fatto METADATI_MINIMIZZATI), solo per un PdA eliminato per intero
    "minimizzazione": """
        SELECT 1 FROM public.pratiche p
        JOIN public.pratiche_oggetti o ON o.pratica_id = p.id AND o.aip_id = :aip
        JOIN public.aip a ON a.id = o.aip_id
        JOIN public.esiti_esecuzione e ON e.tipo = 'SCARTO' AND e.pratica_id = p.id
             AND e.aip_id = a.id AND e.tenant_id = a.tenant_id
             AND e.esito = 'METADATI_MINIMIZZATI' AND e.dettagli ->> 'impronta' = :impronta
        WHERE p.id = :pratica AND p.tipo = 'SCARTO' AND p.tenant_id = a.tenant_id
          AND p.stato IN ('ESEGUITA', 'ESEGUITA_CON_ECCEZIONI', 'CHIUSA_CON_ECCEZIONI')
          AND o.esito = 'ELIMINATO' AND a.stato::text = 'ELIMINAZIONE'""",
    # R09 (D04): rinnovo del blocco WORM di un PdA (fatto RETENTION / RINNOVATA del
    # lavoratore; «pratica» del riferimento = identificativo del rinnovo)
    "rinnovo_retention": """
        SELECT 1 FROM public.esiti_esecuzione e
        JOIN public.aip a ON a.id = e.aip_id AND a.tenant_id = e.tenant_id
        WHERE e.tipo = 'RETENTION' AND e.esito = 'RINNOVATA' AND e.aip_id = :aip
          AND e.dettagli ->> 'rinnovo' = CAST(:pratica AS text)
          AND e.dettagli ->> 'impronta' = :impronta""",
}

_MOTIVI_RISCONTRO: dict[str, str] = {
    "scarto": "nessuno scarto avviato di questa pratica ha eliminato il PdA (pratica SCARTO "
              "dello stesso ente, oggetto ELIMINATO, PdA in ELIMINAZIONE, esito di "
              "esecuzione scritto dal lavoratore)",
    "annullamento": "nessun annullamento eseguito di questa pratica per il PdV del PdA con "
                    "l'atto archiviato dal lavoratore (esito di esecuzione)",
    "rettifica": "nessuna rettifica in versamento di questa pratica per il PdV del PdA con il "
                 "PdV di rettifica versato dal lavoratore (esito di esecuzione)",
    "chiusura_eccezioni": "nessuna chiusura con eccezioni di questa pratica per il PdA in "
                          "eliminazione incompleta (esito di esecuzione del lavoratore)",
    "minimizzazione": "nessuna minimizzazione dei metadati del PdA scartato da questa pratica "
                      "(esito di esecuzione del lavoratore)",
    "rinnovo_retention": "nessun rinnovo del blocco WORM registrato dal lavoratore per il PdA",
}


async def riscontro_ciclo_vita(session: Any, *, tipo: str, aip_id: Any,
                               riferimento: dict[str, Any], impronta: str = "") -> None:
    """Solleva ``RiscontroMancante`` se il fatto della foglia non risulta dal
    database (vedi ``_RISCONTRO``). Per lo scarto anche ``riferimento.aip``, se
    presente, deve essere il PdA della foglia; per annullamento e rettifica
    (R06, VD-C-119) ``impronta`` è quella dell'atto registrata dal lavoratore."""
    try:
        pratica = uuid.UUID(str(riferimento.get("pratica")))
    except ValueError:
        raise RiscontroMancante("riferimento.pratica non è l'identificativo di una pratica") \
            from None
    if tipo == "scarto" and riferimento.get("aip") not in (None, str(aip_id)):
        raise RiscontroMancante("riferimento.aip diverso dal PdA della foglia")
    parametri: dict[str, Any] = {"aip": aip_id, "pratica": pratica}
    if tipo != "scarto":
        parametri["impronta"] = impronta.lower()
    if tipo == "rettifica":
        parametri["pdv_rettifica"] = str(riferimento.get("pdv_rettifica") or "")
    trovato = (await session.execute(text(_RISCONTRO[tipo]), parametri)).first()
    if trovato is None:
        raise RiscontroMancante(_MOTIVI_RISCONTRO[tipo])


@dataclass(frozen=True)
class EsitoRichiesta:
    foglia_id: str
    nuova: bool


def chiave_ciclo_vita(tipo: str, pda: str, pratica: str) -> str:
    """Chiave d'idempotenza (tipo, pda, pratica): breve e senza ambiguità di
    separatori (impronta del JSON canonico)."""
    return f"{tipo}|" + hashlib.sha256(json_canonico([tipo, pda, pratica])).hexdigest()


def chiave_audit(segmento: str) -> str:
    return f"audit|{segmento}"


_ESISTENTE = text(
    "SELECT id::text AS id, riferimento, impronta FROM public.catena_foglie_interne "
    "WHERE chiave = :chiave")

_INSERISCI = text(
    "INSERT INTO public.catena_foglie_interne (id, tipo, chiave, aip_id, tenant_id, pda, "
    "pratica, riferimento, impronta, dati, hash, richiesta_da, creata_il) VALUES (:id, :tipo, "
    ":chiave, :aip, :ente, :pda, :pratica, CAST(:riferimento AS jsonb), :impronta, :dati, "
    ":hash, :da, :adesso) ON CONFLICT (chiave) DO NOTHING RETURNING id")


async def accoda_foglia(session: Any, *, foglia_id: uuid.UUID, tipo_db: str, chiave: str,
                        dati: bytes, aip_id: Any, tenant_id: Any, pda: str | None,
                        pratica: str | None, riferimento: dict[str, Any],
                        impronta: str | None, richiesta_da: str, adesso: datetime) -> bool:
    """INSERT della foglia interna e della sua riga in coda; False se la chiave
    esiste già (nessuna scrittura)."""
    inserita = (await session.execute(_INSERISCI, {
        "id": foglia_id, "tipo": tipo_db, "chiave": chiave, "aip": aip_id, "ente": tenant_id,
        "pda": pda, "pratica": pratica, "riferimento": json.dumps(riferimento),
        "impronta": impronta, "dati": dati.decode("utf-8"), "hash": hash_foglia(dati),
        "da": richiesta_da[:200], "adesso": adesso})).first()
    if inserita is None:
        return False
    await session.execute(text(
        "INSERT INTO public.catena_coda (foglia_interna_id) VALUES (:id)"), {"id": foglia_id})
    return True


async def richiedi_foglia_ciclo_vita(
    session: Any, *, tipo: str, pda: str, riferimento: dict[str, Any], impronta: str,
    richiesta_da: str, adesso: datetime,
) -> EsitoRichiesta:
    """Richiesta idempotente di una foglia del ciclo di vita (vedi modulo), nella
    transazione del chiamante, con la sua voce d'audit."""
    from libs.shared.security.audit import registra_audit

    if tipo not in TIPI_CICLO_VITA:
        raise ValueError(f"tipo {tipo!r} non ammesso")
    rif = controlla_riferimento(riferimento)
    impronta = impronta.lower()
    pratica = str(rif["pratica"])
    chiave = chiave_ciclo_vita(tipo, pda, pratica)

    async def _esistente() -> EsitoRichiesta | None:
        riga = (await session.execute(_ESISTENTE, {"chiave": chiave})).mappings().first()
        if riga is None:
            return None
        salvato = riga["riferimento"]
        if isinstance(salvato, str):
            salvato = json.loads(salvato)
        if salvato != rif or riga["impronta"] != impronta:
            raise ConflittoFoglia(riga["id"])
        return EsitoRichiesta(riga["id"], False)

    gia = await _esistente()
    if gia is not None:
        return gia
    aip = (await session.execute(text(
        "SELECT id, tenant_id FROM public.aip WHERE urn = :urn"), {"urn": pda})).first()
    if aip is None:
        raise PdaNonTrovato(pda)
    # Revisione 2 di R05 (S2): il fatto deve risultare dal database
    await riscontro_ciclo_vita(session, tipo=tipo, aip_id=aip.id, riferimento=rif,
                               impronta=impronta)
    foglia_id = uuid.uuid4()
    dati = dati_foglia_ciclo_vita(
        tipo, foglia_id=str(foglia_id), pda=pda, ente=str(aip.tenant_id), riferimento=rif,
        impronta_documento_pratica=impronta, registrato_il=adesso)
    if not await accoda_foglia(session, foglia_id=foglia_id, tipo_db=tipo.upper(),
                               chiave=chiave, dati=dati, aip_id=aip.id,
                               tenant_id=aip.tenant_id, pda=pda, pratica=pratica,
                               riferimento=rif, impronta=impronta,
                               richiesta_da=richiesta_da, adesso=adesso):
        # Richiesta concorrente con la stessa chiave: vale quella già scritta
        concorrente = await _esistente()
        if concorrente is None:  # pragma: no cover — vincolo unico appena violato
            raise RuntimeError("foglia interna non scritta né trovata")
        return concorrente
    await registra_audit(session, evento="catena.foglia_interna_richiesta",
                         tenant_id=str(aip.tenant_id), aip_id=str(aip.id), dettagli={
                             "foglia_id": str(foglia_id), "tipo": tipo, "pda": pda,
                             "pratica": pratica, "impronta_documento_pratica": impronta,
                             "richiesta_da": richiesta_da[:200]})
    return EsitoRichiesta(str(foglia_id), True)


_CHECKPOINT_COPRE = (
    "SELECT dimensione, emesso_il, stato_sigillo, stato_testimone "
    "FROM public.catena_checkpoint WHERE dimensione > :i")


async def stato_foglia(session: Any, foglia_id: uuid.UUID) -> dict[str, Any] | None:
    """Stato di una foglia interna: indice e primo checkpoint che la copre (e il
    primo controfirmato) quando ci sono. None se la foglia non esiste."""
    riga = (await session.execute(text(
        "SELECT id::text AS id, tipo, pda, riferimento, impronta, dati, creata_il, indice, "
        "sequenziata_il FROM public.catena_foglie_interne WHERE id = :id"
    ), {"id": foglia_id})).mappings().first()
    if riga is None:
        return None
    risposta: dict[str, Any] = {
        "foglia_id": riga["id"], "tipo": str(riga["tipo"]).lower(), "pda": riga["pda"],
        "riferimento": riga["riferimento"] if not isinstance(riga["riferimento"], str)
        else json.loads(riga["riferimento"]),
        "impronta_documento_pratica": riga["impronta"], "foglia_dati": riga["dati"],
        "richiesta_il": riga["creata_il"].isoformat(), "stato": STATO_IN_CODA,
        "indice": None, "sequenziata_il": None, "checkpoint": None,
        "checkpoint_controfirmato": None,
    }
    if riga["indice"] is None:
        return risposta
    i = int(riga["indice"])
    risposta.update(indice=i, sequenziata_il=riga["sequenziata_il"].isoformat(),
                    stato=STATO_NEL_REGISTRO)

    def _cp(r: Any) -> dict[str, Any] | None:
        return None if r is None else {
            "dimensione": int(r["dimensione"]), "emesso_il": r["emesso_il"].isoformat(),
            "stato_sigillo": r["stato_sigillo"], "stato_testimone": r["stato_testimone"]}

    primo = (await session.execute(text(
        _CHECKPOINT_COPRE + " ORDER BY dimensione LIMIT 1"), {"i": i})).mappings().first()
    controfirmato = (await session.execute(text(
        _CHECKPOINT_COPRE + " AND stato_testimone = 'CONTROFIRMATO' ORDER BY dimensione "
        "LIMIT 1"), {"i": i})).mappings().first()
    risposta.update(checkpoint=_cp(primo), checkpoint_controfirmato=_cp(controfirmato))
    if controfirmato is not None:
        risposta["stato"] = STATO_CONTROFIRMATA
    elif primo is not None:
        risposta["stato"] = STATO_IN_CHECKPOINT
    return risposta
