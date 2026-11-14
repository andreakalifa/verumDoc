# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/requisiti.py — requisiti normativi coperti dai documenti generati (R15).

Fonte interna: ``docs/audit/allegati/ricerca_normativa.md`` (ricerca del 20/10/2026), che
marca ogni contenuto come riscontrato sul testo definitivo [V] o no [NV]. Qui la sintesi è
scritta con parole nostre (nessun testo copiato da documenti di terzi). Un requisito con
``riscontrato=False`` produce nei documenti il blocco «da verificare sul testo ufficiale»
invece di un testo inventato. Cambiare lo stato di un requisito richiede il riscontro sulla
fonte ufficiale annotato in ``nota`` (REGOLE §31, R11 (6)).

Modulo puro.
"""
from __future__ import annotations

from dataclasses import dataclass

LG = ("Linee guida AgID sulla formazione, gestione e conservazione dei documenti "
      "informatici (2021)")
REG_A = ("Regolamento AgID sui criteri per la fornitura dei servizi di conservazione "
         "(Det. 455/2021), Allegato A")


@dataclass(frozen=True)
class Requisito:
    codice: str
    fonte: str
    riferimento: str
    sintesi: str
    riscontrato: bool
    nota: str = ""


_ELENCO: tuple[Requisito, ...] = (
    # ── Manuale di conservazione: contenuti minimi [V] ──
    Requisito("LG-MAN-A", LG, "paragrafo «Manuale di conservazione», lett. a",
              "soggetti che nel tempo hanno avuto la responsabilità del sistema, con le deleghe",
              True),
    Requisito("LG-MAN-B", LG, "paragrafo «Manuale di conservazione», lett. b",
              "struttura organizzativa, funzioni, responsabilità e obblighi dei soggetti", True),
    Requisito("LG-MAN-C", LG, "paragrafo «Manuale di conservazione», lett. c",
              "tipologie degli oggetti conservati, formati gestiti, metadati ed eccezioni", True),
    Requisito("LG-MAN-D", LG, "paragrafo «Manuale di conservazione», lett. d",
              "presa in carico dei PdV e predisposizione del rapporto di versamento", True),
    Requisito("LG-MAN-E", LG, "paragrafo «Manuale di conservazione», lett. e",
              "processo di conservazione e trattamento dei PdA", True),
    Requisito("LG-MAN-F", LG, "paragrafo «Manuale di conservazione», lett. f",
              "esibizione ed esportazione con produzione del PdD", True),
    Requisito("LG-MAN-G", LG, "paragrafo «Manuale di conservazione», lett. g",
              "componenti tecnologiche, fisiche e logiche e loro gestione ed evoluzione", True),
    Requisito("LG-MAN-H", LG, "paragrafo «Manuale di conservazione», lett. h",
              "monitoraggio, verifiche d'integrità degli archivi e gestione delle anomalie", True),
    Requisito("LG-MAN-I", LG, "paragrafo «Manuale di conservazione», lett. i",
              "produzione di duplicati e copie", True),
    Requisito("LG-MAN-J", LG, "paragrafo «Manuale di conservazione», lett. j",
              "tempi di versamento e di scarto per tipologia", True),
    Requisito("LG-MAN-K", LG, "paragrafo «Manuale di conservazione», lett. k",
              "richiesta della presenza di un pubblico ufficiale e casi previsti", True),
    Requisito("LG-MAN-L", LG, "paragrafo «Manuale di conservazione», lett. l",
              "normative in vigore nei luoghi di conservazione", True),
    Requisito("LG-MAN-NUM", LG, "numero del paragrafo «Manuale di conservazione»",
              "numerazione del paragrafo (citata da AgID come 4.6; i rimandi interni del testo "
              "definitivo usano numeri diversi)", False,
              "ricerca_normativa §1.1 [NV]: si citano i titoli dei paragrafi, non i numeri"),
    # ── Processo di conservazione [NV sul testo definitivo] ──
    Requisito("LG-PROC", LG, "paragrafo «Processo di conservazione»",
              "fasi del processo: acquisizione, verifica, rifiuto, rapporto di versamento, PdA, "
              "PdD, duplicati e copie, riversamento, scarto", False,
              "ricerca_normativa §1.5: sostanza confermata, testo definitivo non riscontrato"),
    Requisito("LG-PROC-RIFIUTI", LG, "paragrafo «Processo di conservazione»",
              "numero massimo di rifiuti di un PdV stabilito nel contratto", False,
              "ricerca_normativa §1.5: presente nella bozza, non riscontrato nel testo definitivo"),
    Requisito("LG-ESIB", LG, "paragrafo «Modalità di esibizione»",
              "accesso degli utenti autorizzati agli oggetti conservati tramite PdD", False,
              "ricerca_normativa §1.6 [NV]"),
    Requisito("LG-INFRA", LG, "paragrafo «Infrastrutture»",
              "vincoli di ubicazione e separazione delle componenti del sistema", False,
              "ricerca_normativa §1.6 [NV]: il vincolo sul territorio nazionale è della bozza"),
    Requisito("LG-SIC", LG, "paragrafo «Misure di sicurezza»",
              "piano della sicurezza conforme all'art. 32 GDPR e alle misure minime ICT, "
              "procedura per le violazioni dei dati (artt. 33-34 GDPR)", False,
              "ricerca_normativa §1.6 [NV]: contenuti tratti dalla bozza"),
    Requisito("LG-SCARTO", LG, "paragrafo «Selezione e scarto dei documenti informatici»",
              "scarto alla scadenza dei termini, con autorizzazione dove prevista", False,
              "ricerca_normativa §1.5–1.6 [NV]; per gli archivi pubblici D.Lgs. 42/2004 art. 21"),
    # ── Regolamento, Allegato A [V] ──
    Requisito("RG1", REG_A, "RG1", "API SOAP e/o REST per versamento ed esibizione; le altre "
              "API documentate nel manuale", True),
    Requisito("RG2", REG_A, "RG2", "integrabilità con SPID, CIE o identità UE notificate", True),
    Requisito("RG3", REG_A, "RG3", "indice del PdA conforme a UNI 11386 (SInCRO)", True),
    Requisito("RG8", REG_A, "RG8", "documentazione delle componenti, del monitoraggio, delle "
              "verifiche d'integrità e della gestione delle anomalie", True),
    Requisito("RG9", REG_A, "RG9", "procedure di accesso, ricerca, recupero e uso, con "
              "monitoraggio e nel rispetto della riservatezza", True),
    Requisito("RS1", REG_A, "RS1", "sistema di gestione della sicurezza ISO/IEC 27001 con "
              "campo di applicazione specifico", True),
    Requisito("RS2", REG_A, "RS2", "prove OWASP sulle componenti sviluppate", True),
    Requisito("RS3", REG_A, "RS3", "continuità operativa secondo ISO/IEC 22313", True),
    Requisito("RO1", REG_A, "RO1", "almeno il responsabile del servizio di conservazione e il "
              "responsabile della funzione archivistica", True),
    Requisito("RO4", REG_A, "RO4", "conformità al GDPR", True),
    Requisito("RO5", REG_A, "RO5", "dichiarazione dell'ubicazione dei centri dati (Italia, UE, "
              "extra-UE)", True),
    Requisito("RO6", REG_A, "RO6", "per centri dati extra-UE, consultazione preventiva del "
              "Garante", True),
    Requisito("RO7", REG_A, "RO7", "manuale di conservazione con piano della sicurezza "
              "aggiornato e modello organizzativo", True),
    Requisito("RO8", REG_A, "RO8", "verifica periodica della conformità a norme e standard",
              True),
)

REQUISITI: dict[str, Requisito] = {r.codice: r for r in _ELENCO}


def da_verificare(codice: str) -> bool:
    """True se il testo del requisito non è stato riscontrato sulla fonte ufficiale."""
    return not REQUISITI[codice].riscontrato


__all__ = ["LG", "REG_A", "REQUISITI", "Requisito", "da_verificare"]
