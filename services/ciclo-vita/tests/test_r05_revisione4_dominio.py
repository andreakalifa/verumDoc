# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione 4 di R05 (U2) — verifica PURA della catena delle autorizzazioni dello
scarto (``app.domain.autorizzazioni``): la stessa che il lavoratore esegue
prima di cancellare.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.domain import autorizzazioni as aut
from app.domain import stati as st
from app.domain.permessi import RUOLI_AVVIO

ADMIN, RSC = "ADMIN_TENANT", "RSC_CONSERVATORE"
IL = datetime(2026, 10, 24, tzinfo=UTC)
ALLEGATI = {"a1": {"id": "a1", "tipo": st.ALLEGATO_AUTORIZZAZIONE},
            "n1": {"id": "n1", "tipo": st.ALLEGATO_NULLA_OSTA}}


def _p(da, a, ruolo, allegati=()):
    return {"da_stato": da, "a_stato": a, "ruolo": ruolo, "utente_id": "u",
            "allegati": list(allegati), "created_at": IL}


def _pa():
    return [_p(None, "BOZZA", ADMIN), _p("BOZZA", "PROPOSTA", ADMIN),
            _p("PROPOSTA", "AUTORIZZATA_PRODUTTORE", ADMIN, ["a1"]),
            _p("AUTORIZZATA_PRODUTTORE", "NULLA_OSTA", ADMIN, ["n1"]),
            _p("NULLA_OSTA", "ESEGUIBILE", RSC)]


def _difetti(cronologia, *, richiede=True, stato="ESEGUIBILE"):
    return aut.difetti_iter(st.SCARTO, cronologia, ALLEGATI, richiede_nulla_osta=richiede,
                            stato_attuale=stato, ruoli_avvio=RUOLI_AVVIO[st.SCARTO])


def test_catena_completa_pa_e_privato_valide() -> None:
    assert _difetti(_pa()) == []
    privato = [*_pa()[:3], _p("AUTORIZZATA_PRODUTTORE", "ESEGUIBILE", RSC)]
    assert _difetti(privato, richiede=False) == []
    ripresa = [*_pa(), _p("ESEGUIBILE", "ESEGUITA_CON_ECCEZIONI", st.SISTEMA)]
    assert _difetti(ripresa, stato="ESEGUITA_CON_ECCEZIONI") == []
    assert aut.allegati_autorizzativi(st.SCARTO, _pa(), ALLEGATI) == ["a1", "n1"]


def test_difetti_della_catena() -> None:
    assert _difetti([])[0].startswith("CRONOLOGIA_ASSENTE")
    # nulla osta saltato per una PA
    saltato = [*_pa()[:3], _p("AUTORIZZATA_PRODUTTORE", "ESEGUIBILE", RSC)]
    testo = " ".join(_difetti(saltato))
    assert "NULLA_OSTA_SALTATO" in testo and "NULLA_OSTA_ASSENTE" in testo
    # passo mancante (discontinuità)
    buco = [_pa()[0], _pa()[2], *_pa()[3:]]
    assert "CRONOLOGIA_DISCONTINUA" in " ".join(_difetti(buco))
    # allegato obbligatorio non citato / estraneo / riusato
    senza = _pa()
    senza[2] = _p("PROPOSTA", "AUTORIZZATA_PRODUTTORE", ADMIN)
    assert "ALLEGATO_MANCANTE" in " ".join(_difetti(senza))
    estraneo = _pa()
    estraneo[2] = _p("PROPOSTA", "AUTORIZZATA_PRODUTTORE", ADMIN, ["zz"])
    assert "ALLEGATO_ESTRANEO" in " ".join(_difetti(estraneo))
    riusato = _pa()
    riusato[3] = _p("AUTORIZZATA_PRODUTTORE", "NULLA_OSTA", ADMIN, ["a1", "n1"])
    assert "ALLEGATO_RIUSATO" in " ".join(_difetti(riusato))
    # ruolo non ammesso; automatica scritta da un utente
    ruolo = _pa()
    ruolo[4] = _p("NULLA_OSTA", "ESEGUIBILE", ADMIN)
    assert "RUOLO_NON_AMMESSO" in " ".join(_difetti(ruolo))
    finta = [*_pa(), _p("ESEGUIBILE", "ESEGUITA_CON_ECCEZIONI", RSC)]
    assert "RUOLO_NON_AMMESSO" in " ".join(_difetti(finta, stato="ESEGUITA_CON_ECCEZIONI"))
    # stato dichiarato diverso dalla cronologia, stato non eseguibile
    assert "STATO_NON_DALLA_CRONOLOGIA" in " ".join(_difetti(_pa()[:4]))
    assert "STATO_NON_ESEGUIBILE" in " ".join(_difetti(_pa(), stato="RESPINTA"))
    # creazione da un ruolo che non avvia lo scarto
    creata = _pa()
    creata[0] = _p(None, "BOZZA", "OPERATORE")
    assert "CRONOLOGIA_INIZIO" in " ".join(_difetti(creata))
