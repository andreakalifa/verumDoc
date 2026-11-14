# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R07 — parametri del servizio portati in console (D13-D19) e regole nuove del
motore: «il livello del cliente può solo ridurre» (D14) e formato dei codici (D16)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from libs.verumdoc_rules.catalogo import catalogo_impostazioni
from libs.verumdoc_rules.catalogo_v2 import REGISTRO
from libs.verumdoc_rules.configurazione import Ambito, Attore, ModificaValore, piano_valori
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
    ValoreImpostazioneNonValido,
)
from libs.verumdoc_rules.parametri_servizio import (
    INATTIVITA_PREDEFINITA,
    REGISTRO_SERVIZIO,
    codice_inattivita,
    codice_piano_pdv,
    codice_piano_richieste,
)

CTX = ContestoRisoluzione(adesso=datetime(2026, 10, 30, tzinfo=UTC), tenant_id="t1")
CODICE = codice_inattivita("OPERATORE")


def _effettiva(livelli: dict, codice: str = CODICE):
    return Risolutore(REGISTRO_SERVIZIO, FornitoreDaDizionari(livelli)).effettiva(CTX, codice)


def test_predefiniti_delle_decisioni() -> None:
    assert INATTIVITA_PREDEFINITA == {"SUPER_ADMIN": 15, "RSC_CONSERVATORE": 15,
                                      "ADMIN_TENANT": 15, "OPERATORE": 30, "VIEWER": 30}
    attesi = {codice_piano_pdv("starter"): 1000, codice_piano_richieste("starter"): 120,
              codice_piano_pdv("business"): 20000, codice_piano_richieste("business"): 600,
              codice_piano_pdv("enterprise"): 200000,
              codice_piano_richieste("enterprise"): 6000,
              "STAGING_SCARTATI_GIORNI": 90, "PDD_VALIDITA_ORE": 72,
              "AUDIT_EXPORT_PERIODO_MINUTI": 15, "QUOTA_SUPERAMENTO": "RIFIUTA",
              "WEBHOOK_PORTE_AGGIUNTIVE": ()}
    for codice, valore in attesi.items():
        assert REGISTRO_SERVIZIO[codice].predefinito == valore, codice
    limiti = {"STAGING_SCARTATI_GIORNI": (30, 180), "PDD_VALIDITA_ORE": (1, 720),
              "AUDIT_EXPORT_PERIODO_MINUTI": (5, 60), CODICE: (5, 120)}
    for codice, (minimo, massimo) in limiti.items():
        voce = REGISTRO_SERVIZIO[codice]
        assert (voce.minimo, voce.massimo) == (minimo, massimo), codice
    # piani, rallentamento, porte e audit li decide solo il conservatore
    for codice in (codice_piano_pdv("starter"), "QUOTA_RALLENTAMENTO_SECONDI",
                   "WEBHOOK_PORTE_AGGIUNTIVE", "AUDIT_EXPORT_PERIODO_MINUTI"):
        assert REGISTRO_SERVIZIO[codice].riservata_conservatore, codice
    assert REGISTRO["IDEMPOTENZA_TRANSIZIONE_FINO_AL"].riservata_conservatore   # D17


def test_catalogo_della_console_li_contiene_tutti() -> None:
    catalogo = catalogo_impostazioni()
    assert set(REGISTRO_SERVIZIO.codici()) <= set(catalogo.codici())
    gruppi = {v.gruppo for v in catalogo}
    assert {"quote", "consumi", "sessione", "staging", "webhook", "restituzione",
            "audit"} <= gruppi


def test_solo_riduzione_vince_il_minimo() -> None:
    assert _effettiva({}) == (30, Livello.PREDEFINITO)
    assert _effettiva({Livello.CONSERVATORE: {CODICE: 20}}) == (20, Livello.CONSERVATORE)
    assert _effettiva({Livello.CONSERVATORE: {CODICE: 20},
                       Livello.ENTE: {CODICE: 10}}) == (10, Livello.ENTE)
    # un valore dell'ente più alto (salvato prima che il conservatore abbassasse) non vale
    assert _effettiva({Livello.CONSERVATORE: {CODICE: 20},
                       Livello.ENTE: {CODICE: 60}}) == (20, Livello.CONSERVATORE)
    # senza valore del conservatore il tetto è il predefinito
    assert _effettiva({Livello.ENTE: {CODICE: 60}}) == (30, Livello.PREDEFINITO)
    # il conservatore può alzare il massimo oltre il predefinito
    assert _effettiva({Livello.CONSERVATORE: {CODICE: 90}}) == (90, Livello.CONSERVATORE)


def test_piano_blocca_un_aumento_dell_ente() -> None:
    attore = Attore("ADMIN_TENANT", "t1", "u1")
    ambito = Ambito(livello=Livello.ENTE, ente="t1")
    livelli = {Livello.CONSERVATORE: {CODICE: 20}, Livello.ENTE: {}}
    piano = piano_valori(registro=REGISTRO_SERVIZIO, attore=attore, ambito=ambito,
                         modifiche=[ModificaValore(CODICE, 25)], livelli=livelli, contesto=CTX)
    assert piano.bloccati == [{"codice": CODICE, "motivo": (
        "a questo livello il valore si può solo ridurre: massimo 20 (livello CONSERVATORE)")}]
    piano = piano_valori(registro=REGISTRO_SERVIZIO, attore=attore, ambito=ambito,
                         modifiche=[ModificaValore(CODICE, 20)], livelli=livelli, contesto=CTX)
    assert piano.bloccati == [] and piano.scritture == {CODICE: 20}
    # il conservatore al suo livello non ha tetto
    sopra = piano_valori(registro=REGISTRO_SERVIZIO, attore=Attore("SUPER_ADMIN", "c"),
                         ambito=Ambito(livello=Livello.CONSERVATORE),
                         modifiche=[ModificaValore(CODICE, 100)], livelli=livelli, contesto=CTX)
    assert sopra.bloccati == []


def test_formato_delle_porte_dei_webhook() -> None:
    voce = REGISTRO_SERVIZIO["WEBHOOK_PORTE_AGGIUNTIVE"]
    assert voce.converti(["8443", " 9443 "]) == ("8443", "9443")
    for errata in (["0"], ["65536"], ["08443"], ["porta"], ["-1"]):
        with pytest.raises(ValoreImpostazioneNonValido):
            voce.converti(errata)
