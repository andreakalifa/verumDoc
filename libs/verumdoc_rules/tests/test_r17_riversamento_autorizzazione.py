# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R17 (VD-P-155, decisione del committente del 01/11/2026) — autorizzazione del produttore al
riversamento in uscita:

  - valore generale RICHIESTA (predefinito del registro, livello del conservatore);
  - deroga NON_RICHIESTA per un singolo ente scritta SOLO dal conservatore al livello ENTE:
    la voce è riservata, l'amministratore dell'ente non scrive né annulla quel valore (non
    può togliersi l'autorizzazione), e non esiste un limite per ente da aggirare;
  - il verso dichiarato resta ORDINE_SCELTE (RICHIESTA è la scelta più sicura).
"""
from __future__ import annotations

from datetime import UTC, datetime

from libs.verumdoc_rules.configurazione import (
    Ambito,
    Attore,
    ModificaValore,
    motivo_blocco_voce,
    piano_valori,
)
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
    VersoSicuro,
    codice_limite,
)
from libs.verumdoc_rules.parametri_servizio import (
    AUTORIZZAZIONE_NON_RICHIESTA,
    AUTORIZZAZIONE_RICHIESTA,
    REGISTRO_SERVIZIO,
)
from libs.verumdoc_rules.parametri_servizio import (
    RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE as CODICE,
)

CTX = ContestoRisoluzione(adesso=datetime(2026, 11, 1, tzinfo=UTC), tenant_id="t1")
ENTE = Attore("ADMIN_TENANT", "t1", "u1")
CONS = Attore("RSC_CONSERVATORE", None, "c1")
AMBITO_ENTE = Ambito(livello=Livello.ENTE, ente="t1")
AMBITO_CONS = Ambito(livello=Livello.CONSERVATORE)


def _effettiva(livelli: dict) -> str:
    return Risolutore(REGISTRO_SERVIZIO, FornitoreDaDizionari(livelli)).effettiva(
        CTX, CODICE).valore


def test_predefinito_richiesta_e_voce_riservata() -> None:
    voce = REGISTRO_SERVIZIO[CODICE]
    assert voce.predefinito == AUTORIZZAZIONE_RICHIESTA
    assert voce.riservata_conservatore and not voce.solo_riduzione
    assert voce.livelli_ammessi() == {Livello.CONSERVATORE, Livello.ENTE}
    assert voce.verso_sicuro is VersoSicuro.ORDINE_SCELTE
    assert voce.scelte[0] == AUTORIZZAZIONE_RICHIESTA   # la più sicura
    # nessun limite per ente: la deroga è il valore del conservatore per quell'ente
    assert codice_limite(CODICE) not in REGISTRO_SERVIZIO
    assert _effettiva({}) == AUTORIZZAZIONE_RICHIESTA


def test_deroga_per_ente_solo_dal_conservatore() -> None:
    # il conservatore concede la deroga al singolo ente: vale per quell'ente
    piano = piano_valori(registro=REGISTRO_SERVIZIO, attore=CONS, ambito=AMBITO_ENTE,
                         modifiche=[ModificaValore(CODICE, AUTORIZZAZIONE_NON_RICHIESTA)],
                         livelli={Livello.CONSERVATORE: {}, Livello.ENTE: {}}, contesto=CTX)
    assert piano.bloccati == [] and piano.scritture == {CODICE: AUTORIZZAZIONE_NON_RICHIESTA}
    assert _effettiva({Livello.ENTE: {CODICE: AUTORIZZAZIONE_NON_RICHIESTA}}) \
        == AUTORIZZAZIONE_NON_RICHIESTA
    # l'amministratore dell'ente non si concede la deroga né la annulla
    voce = REGISTRO_SERVIZIO[CODICE]
    assert motivo_blocco_voce(ENTE, AMBITO_ENTE, voce) == "valore stabilito dal conservatore"
    for modifica in (ModificaValore(CODICE, AUTORIZZAZIONE_NON_RICHIESTA),
                     ModificaValore(CODICE, AUTORIZZAZIONE_RICHIESTA),
                     ModificaValore(CODICE, eredita=True)):
        piano = piano_valori(registro=REGISTRO_SERVIZIO, attore=ENTE, ambito=AMBITO_ENTE,
                             modifiche=[modifica],
                             livelli={Livello.CONSERVATORE: {},
                                      Livello.ENTE: {CODICE: AUTORIZZAZIONE_NON_RICHIESTA}},
                             contesto=CTX)
        assert [b["codice"] for b in piano.bloccati] == [CODICE] and piano.scritture == {}


def test_valore_generale_del_conservatore() -> None:
    piano = piano_valori(registro=REGISTRO_SERVIZIO, attore=CONS, ambito=AMBITO_CONS,
                         modifiche=[ModificaValore(CODICE, AUTORIZZAZIONE_RICHIESTA)],
                         livelli={Livello.CONSERVATORE: {}}, contesto=CTX)
    assert piano.bloccati == []
    # la deroga del singolo ente vince sul valore generale; gli altri enti restano RICHIESTA
    livelli = {Livello.CONSERVATORE: {CODICE: AUTORIZZAZIONE_RICHIESTA},
               Livello.ENTE: {CODICE: AUTORIZZAZIONE_NON_RICHIESTA}}
    assert _effettiva(livelli) == AUTORIZZAZIONE_NON_RICHIESTA
    assert _effettiva({Livello.CONSERVATORE: {CODICE: AUTORIZZAZIONE_RICHIESTA}}) \
        == AUTORIZZAZIONE_RICHIESTA
