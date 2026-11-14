# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/funzioni.py — stato reale delle funzioni descritte dai documenti (R15).

Sostituisce, per i documenti generati, l'elenco a mano ``STATO_FUNZIONI`` del manuale di
R02 (``services/dashboard-api/app/services/manuale.py``), che non seguiva i rilasci.
Ogni funzione cita il CODICE che la realizza e i TEST che la provano (REGOLE §0.1: niente
«realizzata» senza un test); ``tools/docs/verifica_documentazione.py`` controlla che ogni
percorso citato esista. Le criticità aperte collegate si leggono dal registro
(``docs/registro/criticita.md``) nella fotografia del repository: lo stato scritto qui è il
punto di partenza, il documento lo abbassa se una criticità collegata è ancora aperta.

Stati:
  REALIZZATA   presente nel codice e provata dai test citati;
  IN_PARTE     presente ma con limiti dichiarati (criticità collegate);
  PREDISPOSTA  codice completo e provato, inattiva finché manca una risorsa esterna
               (contratto, credenziali): la configurazione in esercizio dice se è attiva;
  ASSENTE      non realizzata (si descrive solo in «Funzioni pianificate»).

Modulo puro. Aggiornarlo a ogni rilascio che cambia una funzione (REGOLE §0.2).
"""
from __future__ import annotations

from dataclasses import dataclass

REALIZZATA = "realizzata"
IN_PARTE = "realizzata in parte"
PREDISPOSTA = "predisposta"
ASSENTE = "non realizzata"


@dataclass(frozen=True)
class Funzione:
    chiave: str
    descrizione: str
    stato: str
    codice: tuple[str, ...]
    prove: tuple[str, ...]
    criticita: tuple[str, ...] = ()
    nota: str = ""


_ELENCO: tuple[Funzione, ...] = (
    Funzione("versamento", "Versamento dei PdV da API REST, sessioni di caricamento, SFTP "
             "e PDND", REALIZZATA,
             ("services/gateway/app", "services/sftp-ingest/app"),
             ("services/gateway/tests/test_versamento.py",
              "services/gateway/tests/test_r03_canali_e2e.py")),
    Funzione("verifica_pdv", "Verifica del PdV: impronte, formati riconosciuti per contenuto "
             "(Allegato 2), firme al livello della classe", IN_PARTE,
             ("services/validation/app",),
             ("services/validation/tests/test_r08_formato_firme.py",),
             ("VD-C-143",), "verifica delle firme senza Trusted List UE in esercizio"),
    Funzione("rapporto_versamento", "Rapporto di versamento con identificativo, data UTC, "
             "impronte e prove della catena", REALIZZATA,
             ("services/hashchain/app/registro/conservazione.py",),
             ("services/gateway/tests/test_e2e_pipeline.py",)),
    Funzione("pda_ipda", "PdA con indice SInCRO (UNI 11386) scritto nel WORM", IN_PARTE,
             ("services/aip-builder/app",),
             ("services/aip-builder/tests/test_r11_ipda.py",
              "services/aip-builder/tests/test_sprint2a_sincro_scadenza.py"),
             ("VD-C-071", "VD-C-096"), "indice non validato contro lo schema UNI ufficiale"),
    Funzione("worm", "Immutabilità degli oggetti conservati (S3 Object Lock COMPLIANCE con "
             "retention per classe)", IN_PARTE,
             ("libs/shared/storage/worm.py", "infra/terraform/modules/s3/main.tf"),
             ("services/aip-builder/tests/test_s3_uploader.py",),
             ("VD-C-081",), "provata su storage compatibile, mai su S3 reale"),
    Funzione("catena", "Registro delle impronte con checkpoint firmati e testimone "
             "indipendente, verificabile da terzi", REALIZZATA,
             ("libs/catena", "services/hashchain/app/registro", "services/testimone/app"),
             ("libs/catena/tests", "services/testimone/tests")),
    Funzione("firma_marca", "Sigillo qualificato di IPdA, RdV e documenti del conservatore e "
             "marca temporale RFC 3161", PREDISPOSTA,
             ("services/hashchain/app/registro/firme_documenti.py", "services/signature/app"),
             ("services/hashchain/tests/test_r14_arretrato_ipda.py",
              "services/signature/tests/test_csc_client.py"),
             ("VD-C-068",), "attiva solo con un fornitore qualificato registrato (VD-P-05)"),
    Funzione("esibizione", "Esibizione e produzione dei PdD con i file conservati e le "
             "impronte verificate", REALIZZATA,
             ("services/distribution/app",),
             ("services/distribution/tests/test_b4_pdd.py",)),
    Funzione("ricerca", "Ricerca dei documenti conservati per ente", REALIZZATA,
             ("services/distribution/app",),
             ("services/distribution/tests/test_b4_ricerca.py",)),
    Funzione("verifiche_integrita", "Verifica continua dell'integrità degli oggetti conservati "
             "e della catena, con allarmi", REALIZZATA,
             ("services/hashchain/app/registro",),
             ("services/hashchain/tests/test_r05_revisione3_verifica_continua.py",)),
    Funzione("scarto", "Scarto con proposta, autorizzazione, nulla osta per gli enti pubblici, "
             "verbale nel WORM e tombstone nella catena", REALIZZATA,
             ("services/ciclo-vita/app",),
             ("services/ciclo-vita/tests/test_e2e_scarto.py",)),
    Funzione("riversamento", "Riversamento in uscita e in ingresso e restituzione", REALIZZATA,
             ("services/ciclo-vita/app",),
             ("services/ciclo-vita/tests/test_riversamento.py",)),
    Funzione("duplicati_copie", "Duplicati e copie informatiche dei documenti conservati",
             IN_PARTE, ("services/distribution/app",),
             ("services/distribution/tests/test_b4_pdd.py",), (),
             "le copie si ottengono come PdD; manca una procedura distinta con attestazione"),
    Funzione("accesso", "Accesso con SPID/CIE (aggregatore OIDC), password con codice a tempo "
             "e passkey", PREDISPOSTA,
             ("services/gateway/app",),
             ("services/gateway/tests/test_r10_accesso_locale.py",
              "services/gateway/tests/test_g2_spid_accesso.py"),
             ("VD-C-069",), "SPID e CIE attivi solo con un aggregatore qualificato"),
    Funzione("pec", "Comunicazioni con PEC e ricevute del gestore", PREDISPOSTA,
             ("services/notification/app",),
             ("services/notification/tests/test_r13b_pec.py",),
             ("VD-C-078",), "attiva solo con un canale PEC registrato e un gestore"),
    Funzione("audit", "Registro delle operazioni append-only esportato nel WORM e verificato",
             REALIZZATA, ("libs/shared/security/audit.py", "services/hashchain/app/registro"),
             ("services/hashchain/tests/test_r05_audit_worm.py",)),
    Funzione("isolamento", "Isolamento fra enti con filtri applicativi e Row Level Security",
             REALIZZATA, ("libs/shared/db/ambito.py",),
             ("services/gateway/tests/test_r05_rls_ente.py",), ("VD-C-125",)),
    Funzione("ruoli_permessi", "Ruoli fissi, ruoli personalizzati senza escalation, azioni mai "
             "delegabili", REALIZZATA,
             ("libs/verumdoc_rules/permessi.py", "libs/shared/security/permessi.py"),
             ("services/onboarding/tests/test_r10_ruoli_inviti.py",)),
    Funzione("backup_dr", "Copie di sicurezza del database e replica degli oggetti in un'altra "
             "regione", IN_PARTE,
             ("infra/terraform/modules/rds/main.tf", "infra/terraform/modules/s3/main.tf"),
             ("infra/terraform/modules/iam_servizi/tests",), ("VD-C-038", "VD-C-081"),
             "dichiarati in Terraform, mai provati su infrastruttura vera"),
    Funzione("monitoraggio", "Metriche, allarmi e cruscotto di esercizio", IN_PARTE,
             ("libs/shared/observability", "infra/observability"),
             ("libs/shared/observability/tests/test_metriche_servizi.py",), ("VD-C-176",),
             "tracing distribuito non collegato"),
    Funzione("documentazione", "Manuale, piano della sicurezza, guida e catalogo delle pagine "
             "generati dalla configurazione, con versioni immutabili nel WORM", REALIZZATA,
             ("libs/conformita", "services/dashboard-api/app/routers/documentazione_servizio.py"),
             ("libs/conformita/tests/test_documenti.py",
              "services/dashboard-api/tests/test_r15_documentazione.py")),
)

FUNZIONI: dict[str, Funzione] = {f.chiave: f for f in _ELENCO}


def stato_effettivo(f: Funzione, aperte: set[str]) -> str:
    """Stato da dichiarare: una funzione «realizzata» con una criticità collegata ancora
    aperta (o parzialmente chiusa) si dichiara «realizzata in parte»."""
    if f.stato == REALIZZATA and any(c in aperte for c in f.criticita):
        return IN_PARTE
    return f.stato


__all__ = ["ASSENTE", "FUNZIONI", "IN_PARTE", "PREDISPOSTA", "REALIZZATA", "Funzione",
           "stato_effettivo"]
