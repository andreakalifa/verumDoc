# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R07, revisione W1/W5 — verso «più sicuro» e limite del conservatore per ente.

W1: prima l'ADMIN_TENANT poteva alzare validità del PdD e giorni di staging oltre
il valore del conservatore e scegliere RALLENTA; il conservatore non aveva modo di
imporre un tetto per l'ente (una sua riga ENTE l'ente la sovrascriveva). Ora ogni
voce dichiara il verso più sicuro; sulle voci che l'ente personalizza il motore lo
fa valere rispetto al TETTO: ``LIMITE.<codice>`` (riservato al conservatore, per
ente) o il valore del conservatore.
W5: inattività minima 5 minuti (avviso a 2 minuti)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from libs.verumdoc_rules.catalogo import catalogo_impostazioni
from libs.verumdoc_rules.configurazione import Ambito, Attore, ModificaValore, piano_valori
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Impostazione,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    TipoImpostazione,
    ValoreImpostazioneNonValido,
    VersoSicuro,
    codice_limite,
)
from libs.verumdoc_rules.parametri_servizio import (
    IMPOSTAZIONI_SERVIZIO,
    PDD_VALIDITA_ORE,
    QUOTA_SUPERAMENTO,
    REGISTRO_SERVIZIO,
    STAGING_SCARTATI_GIORNI,
    codice_inattivita,
)

CTX = ContestoRisoluzione(adesso=datetime(2026, 10, 30, tzinfo=UTC), tenant_id="t1")
ENTE = Attore("ADMIN_TENANT", "t1", "u1")
CONS = Attore("RSC_CONSERVATORE", None, "c1")
AMBITO_ENTE = Ambito(livello=Livello.ENTE, ente="t1")
CONSERVATORE = {PDD_VALIDITA_ORE: 72, STAGING_SCARTATI_GIORNI: 90, QUOTA_SUPERAMENTO: "RIFIUTA"}


def _piano(attore, modifiche, livelli):
    return piano_valori(registro=REGISTRO_SERVIZIO, attore=attore, ambito=AMBITO_ENTE,
                        modifiche=modifiche, livelli=livelli, contesto=CTX)


def _effettiva(livelli, codice):
    return Risolutore(REGISTRO_SERVIZIO, FornitoreDaDizionari(livelli)).effettiva(CTX, codice)


def test_ogni_parametro_r07_dichiara_il_verso_sicuro() -> None:
    for voce in IMPOSTAZIONI_SERVIZIO:
        assert voce.verso_sicuro is not None, voce.codice
    assert catalogo_impostazioni()["IDEMPOTENZA_TRANSIZIONE_FINO_AL"].verso_sicuro \
        is VersoSicuro.MINORE
    personalizzabili = {v.codice for v in IMPOSTAZIONI_SERVIZIO if v.solo_riduzione}
    assert personalizzabili == {PDD_VALIDITA_ORE, STAGING_SCARTATI_GIORNI, QUOTA_SUPERAMENTO,
                                *(codice_inattivita(r) for r in ("SUPER_ADMIN",
                                  "RSC_CONSERVATORE", "ADMIN_TENANT", "OPERATORE", "VIEWER")),
                                # R08 (D22): firme del ciclo di vita, l'ente va solo verso
                                # la scelta più sicura
                                "NULLA_OSTA_FIRMA", "CICLO_VITA_FIRME_LIVELLO",
                                "ANNULLAMENTO_RICHIESTA_FIRMATA",
                                # R09 (D21, D23): proposta di scarto (attivarla e farla più
                                # spesso è il verso sicuro), doppia approvazione
                                "SCARTO_PROPOSTA_AUTOMATICA", "SCARTO_PROPOSTA_FREQUENZA_GIORNI",
                                "ANNULLAMENTO_DOPPIA_APPROVAZIONE",
                                # R09 (D05): verifica più frequente per ente o classe
                                "VERIFICA_CICLO_GIORNI",
                                # R10 (D08, D12, D32): metodi d'accesso (meno), validità
                                # degli inviti (più breve), password (più lunga), elenco
                                # SFTP obbligatorio, avvisi obbligatori (più)
                                "ACCESSO_METODI_AMMESSI", "INVITO_VALIDITA_ORE",
                                "PASSWORD_LUNGHEZZA_MINIMA", "SFTP_ALLOWLIST_OBBLIGATORIA",
                                "NOTIFICHE_OBBLIGATORIE",
                                # R12b (VD-C-156): riautenticazione (finestra più breve)
                                "ACCESSO_RIAUTENTICAZIONE_MINUTI",
                                # R13b: casi d'uso della PEC (più). R17 (VD-P-155):
                                # l'autorizzazione del produttore al riversamento in
                                # uscita è riservata al conservatore anche per ente
                                "PEC_CASI_USO"}
    for codice in personalizzabili:   # per ognuna il registro genera il limite per ente
        limite = REGISTRO_SERVIZIO[codice_limite(codice)]
        assert limite.riservata_conservatore and limite.livelli_ammessi() == {Livello.ENTE}
        assert limite.limite_di == codice


def test_l_ente_non_alza_oltre_il_conservatore() -> None:
    """La prova della revisione (prova_w1.py): 720 h / 180 g / RALLENTA ora bloccati."""
    livelli = {Livello.CONSERVATORE: dict(CONSERVATORE), Livello.ENTE: {}}
    piano = _piano(ENTE, [ModificaValore(PDD_VALIDITA_ORE, 720),
                          ModificaValore(STAGING_SCARTATI_GIORNI, 180),
                          ModificaValore(QUOTA_SUPERAMENTO, "RALLENTA")], livelli)
    assert [b["codice"] for b in piano.bloccati] == [PDD_VALIDITA_ORE, STAGING_SCARTATI_GIORNI,
                                                      QUOTA_SUPERAMENTO]
    assert piano.scritture == {}
    # ridurre si può
    piano = _piano(ENTE, [ModificaValore(PDD_VALIDITA_ORE, 24),
                          ModificaValore(STAGING_SCARTATI_GIORNI, 30)], livelli)
    assert piano.bloccati == [] and piano.scritture == {PDD_VALIDITA_ORE: 24,
                                                        STAGING_SCARTATI_GIORNI: 30}


def test_righe_dell_ente_oltre_il_tetto_non_valgono() -> None:
    """Righe salvate prima (o a mano): la risoluzione le riporta al tetto."""
    livelli = {Livello.CONSERVATORE: dict(CONSERVATORE),
               Livello.ENTE: {PDD_VALIDITA_ORE: 720, STAGING_SCARTATI_GIORNI: 180,
                              QUOTA_SUPERAMENTO: "RALLENTA"}}
    assert _effettiva(livelli, PDD_VALIDITA_ORE) == (72, Livello.CONSERVATORE)
    assert _effettiva(livelli, STAGING_SCARTATI_GIORNI) == (90, Livello.CONSERVATORE)
    assert _effettiva(livelli, QUOTA_SUPERAMENTO) == ("RIFIUTA", Livello.CONSERVATORE)


def test_limite_per_ente_fissato_dal_conservatore() -> None:
    """Valore e limite sono distinti: il conservatore consente a un ente più del
    valore generale (168 h, RALLENTA) o gli impone meno (48 h); l'ente sta sotto."""
    piano = _piano(CONS, [ModificaValore(codice_limite(PDD_VALIDITA_ORE), 168),
                          ModificaValore(codice_limite(QUOTA_SUPERAMENTO), "RALLENTA")],
                   {Livello.CONSERVATORE: dict(CONSERVATORE), Livello.ENTE: {}})
    assert piano.bloccati == []
    ente = dict(piano.scritture)
    livelli = {Livello.CONSERVATORE: dict(CONSERVATORE), Livello.ENTE: ente}
    # senza scelta dell'ente vale il valore generale (il limite non lo alza da solo)
    assert _effettiva(livelli, PDD_VALIDITA_ORE) == (72, Livello.CONSERVATORE)
    piano = _piano(ENTE, [ModificaValore(PDD_VALIDITA_ORE, 168),
                          ModificaValore(QUOTA_SUPERAMENTO, "RALLENTA")], livelli)
    assert piano.bloccati == []
    piano = _piano(ENTE, [ModificaValore(PDD_VALIDITA_ORE, 169)], livelli)
    assert piano.bloccati == [{"codice": PDD_VALIDITA_ORE, "motivo": (
        "a questo livello il valore si può solo ridurre: massimo 168 (limite del "
        "conservatore per l'ente)")}]
    livelli[Livello.ENTE] = {**ente, PDD_VALIDITA_ORE: 168, QUOTA_SUPERAMENTO: "RALLENTA"}
    assert _effettiva(livelli, PDD_VALIDITA_ORE) == (168, Livello.ENTE)
    assert _effettiva(livelli, QUOTA_SUPERAMENTO) == ("RALLENTA", Livello.ENTE)
    # il conservatore abbassa il limite: vale subito anche per chi aveva di più
    livelli[Livello.ENTE][codice_limite(PDD_VALIDITA_ORE)] = 48
    del livelli[Livello.ENTE][codice_limite(QUOTA_SUPERAMENTO)]
    assert _effettiva(livelli, PDD_VALIDITA_ORE) == (48, Livello.ENTE)
    assert _effettiva(livelli, QUOTA_SUPERAMENTO) == ("RIFIUTA", Livello.CONSERVATORE)


def test_l_ente_non_scrive_ne_annulla_il_limite() -> None:
    livelli = {Livello.CONSERVATORE: dict(CONSERVATORE),
               Livello.ENTE: {codice_limite(PDD_VALIDITA_ORE): 48}}
    for modifica in (ModificaValore(codice_limite(PDD_VALIDITA_ORE), 720),
                     ModificaValore(codice_limite(PDD_VALIDITA_ORE), eredita=True)):
        piano = _piano(ENTE, [modifica], livelli)
        assert piano.bloccati == [{"codice": codice_limite(PDD_VALIDITA_ORE),
                                   "motivo": "valore stabilito dal conservatore"}]
    # una riga ENTE del conservatore (valore, non limite) l'ente può solo ridurla
    livelli[Livello.ENTE][PDD_VALIDITA_ORE] = 24
    assert _piano(ENTE, [ModificaValore(PDD_VALIDITA_ORE, 48)], livelli).bloccati == []
    assert _piano(ENTE, [ModificaValore(PDD_VALIDITA_ORE, 49)], livelli).bloccati != []


def test_verso_coerente_con_il_tipo() -> None:
    with pytest.raises(ValueError, match="verso"):
        RegistroImpostazioni([Impostazione("X", TipoImpostazione.SCELTA, "a", "x", scelte=("a",),
                                           verso_sicuro=VersoSicuro.MINORE)])
    with pytest.raises(ValueError, match="verso"):
        RegistroImpostazioni([Impostazione("Y", TipoImpostazione.INTERO, 1, "y",
                                           verso_sicuro=VersoSicuro.SOTTOINSIEME)])
    # R10 (D08): «sottoinsieme» ammesso anche per le voci del cliente (confronto
    # per inclusione, test in test_r10_accesso_parametri.py)
    RegistroImpostazioni([Impostazione("Y", TipoImpostazione.CODICI, (), "y",
                                       verso_sicuro=VersoSicuro.SOTTOINSIEME)])
    # un registro ricostruito da un altro (limiti già generati) non dà errore
    assert set(RegistroImpostazioni(REGISTRO_SERVIZIO).codici()) == set(REGISTRO_SERVIZIO.codici())


def test_w5_inattivita_minima_cinque_minuti() -> None:
    voce = REGISTRO_SERVIZIO[codice_inattivita("OPERATORE")]
    with pytest.raises(ValoreImpostazioneNonValido):
        voce.converti(4)
    assert voce.converti(5) == 5
