# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/services/manuale.py
Manuale di conservazione generato dai dati reali — VD-C-013.

Struttura: contenuti minimi del manuale (Linee guida AgID, paragrafo «Manuale di
conservazione», lettere a–l) + piano della sicurezza e modello organizzativo
(Regolamento AgID Det. 455/2021, Allegato A, RO7).

Principi (REGOLE §0, §28.3, «verità prima della rassicurazione»):
  - i dati del conservatore vengono SOLO dalla configurazione (variabili
    CONSERVATORE_*); se mancano o sono segnaposto risultano «da compilare»;
  - le tabelle tecniche (classi documentali, formati, tempi) vengono dal DB;
  - ogni sezione che descrive una funzione non ancora realizzata è marcata
    «da realizzare» nel testo e nel campo strutturato `stato_sezione`;
  - la bozza generata non è firmata: «Pubblica» crea la versione sigillata
    (R13a, `routers/documenti_conservatore.py`); lo dice `meta.stato_firma`.

Lo stato delle funzioni (STATO_FUNZIONI) va aggiornato a ogni rilascio insieme
al registro delle criticità (REGOLE §0.2).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger(__name__)

DA_COMPILARE = "da compilare"
DA_REALIZZARE = "da realizzare"

# Valori di stato di una sezione
GENERATA = "generata"
SEZ_DA_COMPILARE = "da_compilare"
SEZ_DA_REALIZZARE = "da_realizzare"
PARZIALE = "parziale"
NON_DISPONIBILE = "non_disponibile"

RIFERIMENTI_NORMATIVI = [
    "D.Lgs. 7 marzo 2005, n. 82 (CAD), artt. 43, 44 e 71",
    "Linee guida AgID sulla formazione, gestione e conservazione dei documenti "
    "informatici (maggio 2021) e relativi allegati",
    "Regolamento AgID sui criteri per la fornitura dei servizi di conservazione "
    "(Det. 455/2021, modificata dalla Det. 629/2021), Allegati A e B",
    "UNI 11386:2020 (SInCRO)",
    "ISO 14721 (OAIS)",
    "Reg. (UE) 910/2014 (eIDAS), come modificato dal Reg. (UE) 2024/1183",
    "Reg. (UE) 2016/679 (GDPR)",
    "D.Lgs. 42/2004, art. 21 (autorizzazione allo scarto)",
]

# Stato reale delle funzioni descritte dal manuale. Aggiornare a ogni rilascio.
STATO_FUNZIONI: dict[str, dict[str, str]] = {
    "versamento_api": {
        "descrizione": "Versamento di PdV tramite API REST",
        "stato": PARZIALE,
        "nota": "API presente; controlli di formato, firma e file dichiarati incompleti",
        "riferimento": "VD-C-016, VD-C-017",
    },
    "rapporto_versamento": {
        "descrizione": "Rapporto di versamento firmato o sigillato",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "RdV non conforme e non firmato; il PdA non arriva a CONSERVATO",
        "riferimento": "VD-C-012, VD-C-020",
    },
    "ipda_sincro": {
        "descrizione": "Indice del PdA conforme a UNI 11386:2020 (SInCRO)",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "IPdA generato non valido rispetto allo schema ufficiale (XSD non disponibile)",
        "riferimento": "audit funzionale §4 punto 4",
    },
    "firma_marca_qualificate": {
        "descrizione": "Firma/sigillo qualificato e marca temporale su IPdA e RdV",
        "stato": PARZIALE,
        "nota": "R13a: IPdA, RdV, manuale e piano sigillati automaticamente (CAdES B-LT/LTA) "
        "con fornitori CSC v2, PKCS#11 e TSA RFC 3161 registrati in console; prestatore "
        "qualificato non ancora contrattualizzato: oggi i documenti risultano non firmati",
        "riferimento": "VD-C-068, VD-P-05",
    },
    "object_lock": {
        "descrizione": "Immutabilità (S3 Object Lock COMPLIANCE con retention per classe)",
        "stato": PARZIALE,
        "nota": "scrittura con retention per oggetto realizzata nello storage del PdA; "
        "da collegare al flusso di costruzione del PdA e alla hashchain",
        "riferimento": "VD-C-011",
    },
    "hashchain": {
        "descrizione": "Catena di impronte con ancoraggio marcato",
        "stato": PARZIALE,
        "nota": "registro Merkle con checkpoint controfirmati dal testimone; marca RFC 3161 "
        "dell'ultimo checkpoint (R13a) attiva appena è configurata una TSA qualificata",
        "riferimento": "VD-C-018, VD-C-019",
    },
    "esibizione": {
        "descrizione": "Esibizione e produzione dei PdD",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "PdD ed esibizione non funzionanti",
        "riferimento": "VD-C-005, audit C B4/B5",
    },
    "verifiche_periodiche": {
        "descrizione": "Verifiche periodiche di integrità e leggibilità",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "nessun job di verifica periodica",
        "riferimento": "VD-P-06",
    },
    "scarto": {
        "descrizione": "Scarto con elenco, autorizzazione e verbale",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "workflow assente; il vecchio lifecycle_manager è stato rimosso",
        "riferimento": "VD-C-036, VD-P-06",
    },
    "duplicati_copie": {
        "descrizione": "Produzione di duplicati e copie informatiche",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "funzione assente",
        "riferimento": "—",
    },
    "backup_dr": {
        "descrizione": "Backup e replica in altra regione",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "previsti in Terraform, che oggi non supera il parsing; mai provati",
        "riferimento": "VD-C-038",
    },
    "gestione_segreti": {
        "descrizione": "Gestione dei segreti in secret manager",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "nessun secret manager collegato (Vault citato ma mai usato)",
        "riferimento": "VD-C-038",
    },
    "notifiche_email": {
        "descrizione": "Notifiche e-mail ai referenti dei clienti",
        "stato": PARZIALE,
        "nota": "invio SMTP reale con coda e ritentativi; PEC con ricevute non realizzata",
        "riferimento": "VD-C-024",
    },
    "monitoraggio": {
        "descrizione": "Monitoraggio, metriche e allarmi",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "osservabilità mai collegata",
        "riferimento": "VD-C-033",
    },
    "identificazione_formati": {
        "descrizione": "Identificazione dei formati per contenuto (Allegato 2)",
        "stato": SEZ_DA_REALIZZARE,
        "nota": "si controlla solo il MIME dichiarato",
        "riferimento": "VD-P-04",
    },
}

# Segnaposto noti che non devono mai comparire come dato reale
_SEGNAPOSTO = {
    "",
    "00000000000",
    "0000000000000000",
    "da nominare",
    "da definire",
    "n/a",
    "tbd",
    "todo",
    "xxx",
}


class DatiConservatore(BaseSettings):
    """Dati del conservatore, solo da configurazione (prefisso CONSERVATORE_)."""

    model_config = SettingsConfigDict(
        env_prefix="CONSERVATORE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    denominazione: str = ""
    codice_fiscale: str = ""
    partita_iva: str = ""
    sede_legale: str = ""
    pec: str = ""
    email_contatto: str = ""
    responsabile_servizio_nome: str = ""
    responsabile_servizio_cf: str = ""
    responsabile_funzione_archivistica_nome: str = ""
    responsabile_sicurezza_nome: str = ""
    responsabile_protezione_dati_nome: str = ""
    ubicazione_data_center: str = ""
    manuale_versione: str = ""
    manuale_data_adozione: str = ""
    piano_cessazione_versione: str = ""


def _piva_valida(valore: str) -> bool:
    """Partita IVA / CF numerico di 11 cifre con cifra di controllo; esclude gli zeri."""
    if not re.fullmatch(r"\d{11}", valore) or set(valore) == {"0"}:
        return False
    somma = 0
    for i, c in enumerate(valore[:10]):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        somma += n
    return (10 - somma % 10) % 10 == int(valore[10])


def _cf_persona_valido(valore: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]", valore.upper()))


def campo(valore: str, tipo: str = "testo") -> dict[str, Any]:
    """Valore di configurazione con stato: «compilato», «da compilare» o «non valido»."""
    v = (valore or "").strip()
    if v.lower() in _SEGNAPOSTO:
        return {"valore": None, "stato": DA_COMPILARE}
    validatori = {
        "piva": _piva_valida,
        "cf_ente": lambda x: _piva_valida(x) or _cf_persona_valido(x),
        "cf_persona": _cf_persona_valido,
        "email": lambda x: (
            bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", x, re.I))
            and not x.lower().endswith((".local", ".invalid", ".test", ".example"))
        ),
    }
    controllo = validatori.get(tipo)
    if controllo and not controllo(v):
        return {"valore": None, "stato": "non valido", "motivo": f"{tipo} non valido"}
    return {"valore": v, "stato": "compilato"}


def dati_conservatore_strutturati(dati: DatiConservatore) -> dict[str, dict[str, Any]]:
    return {
        "denominazione": campo(dati.denominazione),
        "codice_fiscale": campo(dati.codice_fiscale, "cf_ente"),
        "partita_iva": campo(dati.partita_iva, "piva"),
        "sede_legale": campo(dati.sede_legale),
        "pec": campo(dati.pec, "email"),
        "email_contatto": campo(dati.email_contatto, "email"),
        "responsabile_servizio_nome": campo(dati.responsabile_servizio_nome),
        "responsabile_servizio_cf": campo(dati.responsabile_servizio_cf, "cf_persona"),
        "responsabile_funzione_archivistica_nome": campo(
            dati.responsabile_funzione_archivistica_nome
        ),
        "responsabile_sicurezza_nome": campo(dati.responsabile_sicurezza_nome),
        "responsabile_protezione_dati_nome": campo(dati.responsabile_protezione_dati_nome),
        "ubicazione_data_center": campo(dati.ubicazione_data_center),
    }


def mostra(c: dict[str, Any]) -> str:
    """Testo da inserire nel documento: il valore o «[da compilare]»."""
    return str(c["valore"]) if c.get("valore") else f"[{DA_COMPILARE}]"


def funzione(chiave: str) -> dict[str, str]:
    return {"chiave": chiave, **STATO_FUNZIONI[chiave]}


def sezione(
    numero: str,
    titolo: str,
    testo: str,
    *,
    funzioni: list[str] | None = None,
    campi_mancanti: list[str] | None = None,
    dati: Any = None,
    stato: str | None = None,
) -> dict[str, Any]:
    """Compone una sezione e ne deduce lo stato dalle funzioni e dai campi mancanti.

    Il testo di una sezione con funzioni non realizzate inizia con
    «[DA REALIZZARE]», e ogni funzione è elencata con il suo stato.
    """
    elenco = [funzione(f) for f in (funzioni or [])]
    da_realizzare = [f for f in elenco if f["stato"] == SEZ_DA_REALIZZARE]
    parziali = [f for f in elenco if f["stato"] == PARZIALE]
    mancanti = campi_mancanti or []
    if stato is None:
        if da_realizzare and len(da_realizzare) == len(elenco):
            stato = SEZ_DA_REALIZZARE
        elif da_realizzare or parziali:
            stato = PARZIALE
        elif mancanti:
            stato = SEZ_DA_COMPILARE
        else:
            stato = GENERATA
    righe = [testo]
    for f in elenco:
        if f["stato"] != GENERATA:
            etichetta = DA_REALIZZARE if f["stato"] == SEZ_DA_REALIZZARE else "realizzata in parte"
            righe.append(f"- {f['descrizione']}: {etichetta} ({f['nota']}; {f['riferimento']}).")
    if mancanti:
        righe.append(f"Dati {DA_COMPILARE}: " + ", ".join(mancanti) + ".")
    contenuto = "\n".join(righe)
    if stato == SEZ_DA_REALIZZARE:
        contenuto = "[DA REALIZZARE] " + contenuto
    elif stato == PARZIALE:
        contenuto = "[IN PARTE DA REALIZZARE] " + contenuto
    elif stato == SEZ_DA_COMPILARE:
        contenuto = "[DA COMPILARE] " + contenuto
    elif stato == NON_DISPONIBILE:
        contenuto = "[NON DISPONIBILE] " + contenuto
    return {
        "numero": numero,
        "titolo": titolo,
        "stato_sezione": stato,
        "contenuto": contenuto,
        "funzioni": elenco,
        "dati_da_compilare": mancanti,
        "dati": dati,
    }


def _mancanti(campi: dict[str, dict[str, Any]], nomi: list[str]) -> list[str]:
    return [n for n in nomi if campi[n]["stato"] != "compilato"]


def tabella_classi(classi: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Tabella tecnica generata dalle classi documentali attive (REGOLE §28.3)."""
    righe = []
    for c in classi or []:
        righe.append(
            {
                "codice": c.get("codice"),
                "nome": c.get("nome"),
                "ambito": "globale" if c.get("tenant_id") is None else "cliente",
                "tipologia_sincro": c.get("tipologia_xml"),
                "tempo_conservazione": (
                    "permanente"
                    if c.get("conservazione_permanente")
                    else f"{c.get('anni_conservazione')} anni"
                ),
                "formati_ammessi": list(c.get("formati_ammessi") or []),
                "metadati_obbligatori": list(c.get("metadati_obbligatori") or []),
            }
        )
    return righe


def _impronta(documento: dict[str, Any]) -> str:
    """Impronta del contenuto (esclusa la data di generazione) per riconoscere le versioni."""
    canonico = json.dumps(
        {k: v for k, v in documento.items() if k != "meta"},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def stato_firma_documento() -> dict[str, Any]:
    return {
        "firmato": False,
        "stato": "non firmato",
        "motivo": (
            "bozza generata dai dati attuali, non firmata: «Pubblica» crea la versione "
            "sigillata con il sigillo qualificato del conservatore (R13a), appena un "
            "fornitore qualificato è attivo (decisione VD-P-05)"
        ),
    }


def riepilogo(sezioni: list[dict[str, Any]]) -> dict[str, int]:
    conteggio: dict[str, int] = {}
    for s in sezioni:
        conteggio[s["stato_sezione"]] = conteggio.get(s["stato_sezione"], 0) + 1
    return conteggio


async def leggi_classi_attive() -> tuple[list[dict[str, Any]] | None, str | None]:
    """Classi documentali attive dal DB; in caso di errore (None, motivo), mai dati inventati."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session

    try:
        async with get_async_session() as session:
            righe = (
                (
                    await session.execute(
                        text(
                            "SELECT codice, nome, tenant_id, tipologia_xml, anni_conservazione, "
                            "conservazione_permanente, formati_ammessi, metadati_obbligatori "
                            "FROM public.classi_documentali WHERE attiva ORDER BY codice"
                        )
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in righe], None
    except Exception as exc:  # noqa: BLE001 — la sezione diventa «non_disponibile»
        logger.warning("manuale_classi_non_disponibili", errore=type(exc).__name__)
        return None, f"lettura delle classi documentali non riuscita ({type(exc).__name__})"


def genera_manuale(
    dati: DatiConservatore,
    *,
    adesso: datetime,
    classi: list[dict[str, Any]] | None = None,
    errore_classi: str | None = None,
) -> dict[str, Any]:
    """Genera il Manuale di conservazione dai dati reali.

    `classi` è l'elenco delle classi documentali attive letto dal DB;
    `errore_classi` è valorizzato se la lettura non è riuscita (la sezione
    risulta «non_disponibile», mai inventata).
    """
    c = dati_conservatore_strutturati(dati)
    denominazione = mostra(c["denominazione"])

    # c) oggetti, formati, metadati
    righe_classi = tabella_classi(classi)
    if errore_classi:
        sez_c = sezione(
            "3",
            "Oggetti conservati, formati e metadati (lett. c)",
            f"Elenco delle classi documentali non disponibile: {errore_classi}.",
            stato=NON_DISPONIBILE,
            funzioni=["identificazione_formati"],
        )
    elif not righe_classi:
        sez_c = sezione(
            "3",
            "Oggetti conservati, formati e metadati (lett. c)",
            "Nessuna classe documentale attiva configurata.",
            campi_mancanti=["classi documentali"],
            stato=SEZ_DA_COMPILARE,
        )
    else:
        testo = (
            f"Il sistema gestisce {len(righe_classi)} classi documentali attive. "
            "Per ciascuna sono indicati tipologia SInCRO, tempo di conservazione, "
            "formati ammessi e metadati obbligatori (tabella generata dal sistema)."
        )
        sez_c = sezione(
            "3",
            "Oggetti conservati, formati e metadati (lett. c)",
            testo,
            funzioni=["identificazione_formati"],
            dati={"classi": righe_classi},
        )

    sezioni = [
        sezione(
            "1",
            "Soggetti responsabili del sistema di conservazione (lett. a)",
            f"Il servizio di conservazione è erogato da {denominazione} "
            f"(C.F. {mostra(c['codice_fiscale'])}, P.IVA {mostra(c['partita_iva'])}, "
            f"sede {mostra(c['sede_legale'])}, PEC {mostra(c['pec'])}). "
            "Responsabile del servizio di conservazione: "
            f"{mostra(c['responsabile_servizio_nome'])}. "
            f"Responsabile della funzione archivistica di conservazione: "
            f"{mostra(c['responsabile_funzione_archivistica_nome'])}. "
            "Lo storico delle nomine del conservatore e delle deleghe non è ancora "
            "registrato dal sistema: va riportato a mano.",
            campi_mancanti=_mancanti(
                c,
                [
                    "denominazione",
                    "codice_fiscale",
                    "partita_iva",
                    "sede_legale",
                    "pec",
                    "responsabile_servizio_nome",
                    "responsabile_servizio_cf",
                    "responsabile_funzione_archivistica_nome",
                ],
            ),
            dati={"conservatore": c},
        ),
        sezione(
            "2",
            "Struttura organizzativa, funzioni e responsabilità (lett. b)",
            "Ruoli richiesti dal Regolamento AgID (Allegato A, RO1): responsabile del "
            "servizio di conservazione e responsabile della funzione archivistica. Ruoli "
            f"ulteriori: responsabile della sicurezza {mostra(c['responsabile_sicurezza_nome'])}, "
            "responsabile della protezione dei dati "
            f"{mostra(c['responsabile_protezione_dati_nome'])}. "
            "Il modello organizzativo (RO7) va allegato.",
            campi_mancanti=_mancanti(
                c, ["responsabile_sicurezza_nome", "responsabile_protezione_dati_nome"]
            )
            + ["modello organizzativo"],
        ),
        sez_c,
        sezione(
            "4",
            "Presa in carico dei PdV e rapporto di versamento (lett. d)",
            "Il versamento avviene tramite API REST con autenticazione. Il rapporto di "
            "versamento deve riportare identificativo, data UTC e impronte di ogni "
            "documento ed essere firmato o sigillato.",
            funzioni=["versamento_api", "rapporto_versamento"],
        ),
        sezione(
            "5",
            "Processo di conservazione e trattamento dei PdA (lett. e)",
            "Il PdA è descritto da un indice UNI 11386:2020, firmato o sigillato e "
            "marcato temporalmente, ed è scritto su storage immutabile con retention "
            "pari al tempo di conservazione della classe documentale.",
            funzioni=["ipda_sincro", "firma_marca_qualificate", "object_lock", "hashchain"],
        ),
        sezione(
            "6",
            "Esibizione ed esportazione con produzione del PdD (lett. f)",
            "L'esibizione produce un PdD con attestazione firmata; l'esportazione per "
            "migrazione produce PdD coincidenti con i PdA.",
            funzioni=["esibizione"],
        ),
        sezione(
            "7",
            "Descrizione del sistema di conservazione (lett. g)",
            "Componenti applicativi: gateway (versamento), validation, aip-builder, "
            "hashchain, signature (firma e marca), distribution (PdD), dashboard-api, "
            "onboarding, notification. Database PostgreSQL, storage S3 con Object Lock, "
            f"messaggistica Kafka. Ubicazione dei data center (RO5): "
            f"{mostra(c['ubicazione_data_center'])}.",
            funzioni=["object_lock", "backup_dr", "gestione_segreti"],
            campi_mancanti=_mancanti(c, ["ubicazione_data_center"]),
        ),
        sezione(
            "8",
            "Monitoraggio e verifica dell'integrità degli archivi (lett. h)",
            "Verifiche periodiche di integrità (ricalcolo delle impronte sugli oggetti "
            "conservati) e di leggibilità, con gestione delle anomalie.",
            funzioni=["verifiche_periodiche", "monitoraggio", "hashchain"],
        ),
        sezione(
            "9",
            "Produzione di duplicati e copie (lett. i)",
            "Procedure per duplicati e copie informatiche dei documenti conservati.",
            funzioni=["duplicati_copie"],
        ),
        sezione(
            "10",
            "Tempi di versamento e di scarto (lett. j)",
            "I tempi di conservazione per classe sono riportati nella sezione 3. Lo "
            "scarto richiede elenco, autorizzazione (per la PA, Soprintendenza "
            "archivistica ex D.Lgs. 42/2004 art. 21) e verbale.",
            funzioni=["scarto"],
        ),
        sezione(
            "11",
            "Richiesta della presenza di un pubblico ufficiale (lett. k)",
            "Modalità con cui il titolare richiede l'intervento di un pubblico ufficiale.",
            campi_mancanti=["procedura per il pubblico ufficiale"],
        ),
        sezione(
            "12",
            "Normative in vigore nei luoghi di conservazione (lett. l)",
            f"Luoghi di conservazione: {mostra(c['ubicazione_data_center'])}.",
            campi_mancanti=_mancanti(c, ["ubicazione_data_center"])
            + ["normative applicabili nei luoghi di conservazione"],
        ),
        sezione(
            "13",
            "Piano della sicurezza (Allegato A, RO7)",
            "Autenticazione con token RS256 verificati, isolamento per cliente, "
            "immutabilità degli oggetti conservati, registro delle attività.",
            funzioni=["object_lock", "gestione_segreti", "monitoraggio", "backup_dr"],
            campi_mancanti=["analisi dei rischi", "piano di continuità operativa"],
        ),
    ]

    documento: dict[str, Any] = {
        "conservatore": c,
        "sezioni": sezioni,
        "funzioni": [funzione(k) for k in STATO_FUNZIONI],
        "riepilogo_sezioni": riepilogo(sezioni),
    }
    completo = all(s["stato_sezione"] == GENERATA for s in sezioni)
    documento["meta"] = {
        "titolo": "Manuale di conservazione",
        "versione": dati.manuale_versione.strip() or "bozza non versionata",
        "data_adozione": dati.manuale_data_adozione.strip() or None,
        "stato_documento": "completo" if completo else "bozza",
        "data_generazione": adesso.isoformat(),
        "impronta_contenuto_sha256": _impronta(documento),
        "stato_firma": stato_firma_documento(),
        "riferimenti_normativi": RIFERIMENTI_NORMATIVI,
    }
    return documento
