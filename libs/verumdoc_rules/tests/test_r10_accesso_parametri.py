# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R10 — parametri di accesso, ruoli e notifiche nel motore (D08, D12, D30, D32).

  - SOTTOINSIEME per le voci del cliente: confronto per INCLUSIONE (un elenco più
    corto con un codice che il tetto non ha non è «più sicuro»), risultato =
    intersezione, LIMITE per ente del conservatore;
  - SOVRAINSIEME: l'ente può solo aggiungere avvisi obbligatori;
  - catalogo dei permessi: mai delegabili esclusi dal formato del parametro,
    nessuna escalation nella composizione.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from libs.verumdoc_rules.configurazione import Ambito, Attore, ModificaValore, piano_valori
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
    ValoreImpostazioneNonValido,
    codice_limite,
)
from libs.verumdoc_rules.parametri_servizio import (
    ACCESSO_METODI_AMMESSI,
    NOTIFICHE_OBBLIGATORIE,
    REGISTRO_SERVIZIO,
    RUOLI_PERMESSI_CONCEDIBILI,
)
from libs.verumdoc_rules.permessi import (
    CONCEDIBILI_POSSIBILI,
    MAI_DELEGABILI,
    MODELLI,
    motivo_composizione_non_ammessa,
)

ENTE = "5f0c1a2e-8b4d-4c7a-9e21-3d6f8a0b1c11"
CTX = ContestoRisoluzione(adesso=datetime(2026, 11, 2, 9, 0, tzinfo=UTC), tenant_id=ENTE)


def _valore(livelli: dict, codice: str):
    return Risolutore(REGISTRO_SERVIZIO, FornitoreDaDizionari(livelli)).effettiva(CTX, codice)


def _piano(livello: Livello, codice: str, valore, livelli: dict, conservatore: bool = False):
    attore = Attore(ruolo="SUPER_ADMIN" if conservatore else "ADMIN_TENANT", tenant_id=ENTE)
    ambito = Ambito(livello=livello, ente=None if livello is Livello.CONSERVATORE else ENTE)
    return piano_valori(registro=REGISTRO_SERVIZIO, attore=attore, ambito=ambito,
                        modifiche=[ModificaValore(codice, valore)], livelli=livelli,
                        contesto=CTX)


def test_metodi_predefinito_comportamento_di_prima() -> None:
    assert _valore({}, ACCESSO_METODI_AMMESSI).valore == ("SPID_CIE",)


def test_metodi_ente_solo_fra_quelli_del_conservatore() -> None:
    livelli = {Livello.CONSERVATORE: {ACCESSO_METODI_AMMESSI: ["SPID_CIE", "PASSWORD_TOTP"]}}
    # più corto ma con un metodo che il conservatore non abilita: bloccato
    assert _piano(Livello.ENTE, ACCESSO_METODI_AMMESSI, ["PASSKEY"], livelli).bloccati
    assert not _piano(Livello.ENTE, ACCESSO_METODI_AMMESSI, ["SPID_CIE"], livelli).bloccati
    # anche scritto fuori dalla console, in risoluzione vale l'intersezione
    livelli[Livello.ENTE] = {ACCESSO_METODI_AMMESSI: ["PASSKEY", "SPID_CIE"]}
    assert _valore(livelli, ACCESSO_METODI_AMMESSI).valore == ("SPID_CIE",)
    # il LIMITE per ente del conservatore può ammettere un metodo in più a quell'ente
    livelli[Livello.ENTE] = {codice_limite(ACCESSO_METODI_AMMESSI): ["PASSKEY", "SPID_CIE"]}
    assert _valore(livelli, ACCESSO_METODI_AMMESSI).valore == ("PASSKEY", "SPID_CIE")
    with pytest.raises(ValoreImpostazioneNonValido):
        REGISTRO_SERVIZIO[ACCESSO_METODI_AMMESSI].converti(["PASSWORD"])


def test_avvisi_obbligatori_solo_aggiunti_dall_ente() -> None:
    livelli = {Livello.CONSERVATORE: {NOTIFICHE_OBBLIGATORIE: ["ALLARMI.CONSOLE"]}}
    assert _piano(Livello.ENTE, NOTIFICHE_OBBLIGATORIE, ["QUOTE.EMAIL"], livelli).bloccati
    assert not _piano(Livello.ENTE, NOTIFICHE_OBBLIGATORIE,
                      ["ALLARMI.CONSOLE", "QUOTE.EMAIL"], livelli).bloccati
    livelli[Livello.ENTE] = {NOTIFICHE_OBBLIGATORIE: ["QUOTE.EMAIL"]}
    assert set(_valore(livelli, NOTIFICHE_OBBLIGATORIE).valore) == {"ALLARMI.CONSOLE",
                                                                   "QUOTE.EMAIL"}


def test_concedibili_riservati_e_mai_delegabili_esclusi() -> None:
    voce = REGISTRO_SERVIZIO[RUOLI_PERMESSI_CONCEDIBILI]
    assert voce.riservata_conservatore
    assert set(voce.predefinito) == set(CONCEDIBILI_POSSIBILI)
    for vietato in MAI_DELEGABILI:
        with pytest.raises(ValoreImpostazioneNonValido):
            voce.converti([vietato])
    assert _piano(Livello.ENTE, RUOLI_PERMESSI_CONCEDIBILI, ["versamento:esegui"], {}).bloccati


@pytest.mark.parametrize("richiesti,base,motivo", [
    (["scarto:esegui"], "OPERATORE", "solo ai ruoli fissi"),
    (["annullamento:approva"], "OPERATORE", "solo ai ruoli fissi"),
    (["firmatari:gestisci"], "VIEWER", "solo ai ruoli fissi"),
    (["pratica:chiudi_eccezioni"], "VIEWER", "solo ai ruoli fissi"),
    (["versamento:esegui"], "ADMIN_TENANT", "si costruisce su"),
    (["esibizione:richiedi"], "VIEWER", "non consente"),
])
def test_composizione_vietata(richiesti, base, motivo) -> None:
    esito = motivo_composizione_non_ammessa(
        ruolo_chiamante="ADMIN_TENANT", permessi_chiamante=MODELLI["ADMIN_TENANT"],
        ruolo_base=base, richiesti=richiesti, concedibili={"versamento:esegui"})
    assert esito and motivo in esito


def test_nessuna_escalation_permessi_che_non_si_hanno() -> None:
    esito = motivo_composizione_non_ammessa(
        ruolo_chiamante="ADMIN_TENANT", permessi_chiamante={"archivio:consulta"},
        ruolo_base="VIEWER", richiesti=["versamento:esegui"],
        concedibili=CONCEDIBILI_POSSIBILI)
    assert esito and "che non hai" in esito
    assert motivo_composizione_non_ammessa(
        ruolo_chiamante="ADMIN_TENANT", permessi_chiamante=MODELLI["ADMIN_TENANT"],
        ruolo_base="VIEWER", richiesti=["versamento:esegui"],
        concedibili=CONCEDIBILI_POSSIBILI) is None
