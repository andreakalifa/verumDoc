# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R09 (D24): l'atto XML di rettifica è sempre ammesso come tipo di sistema, solo
dal canale CICLO_VITA (formati della classe, catalogo dei formati, estensioni)."""
from __future__ import annotations

from datetime import UTC, datetime

from libs.verumdoc_rules import (
    CATALOGO_V1,
    REGISTRO_V1,
    AmbienteRegole,
    ContestoRisoluzione,
    Fase,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
)
from libs.verumdoc_rules.atti_sistema import e_atto_di_sistema
from libs.verumdoc_rules.regole import DatiVersamento, DocumentoDaValutare

PARAMETRI = {"identificativo_documento": "rettifica-p1", "rettifica_di": "urn:x",
             "pdv_rettificato": "p0", "pratica_rettifica": "p1"}


def test_predicato_solo_canale_ciclo_vita_e_atto_completo() -> None:
    def atto(**kw) -> bool:
        base = {"tipo": "PRINCIPALE", "formato": "application/xml", "parametri": PARAMETRI}
        canale = kw.pop("canale", "CICLO_VITA")
        return e_atto_di_sistema(canale, **{**base, **kw})

    assert atto()
    assert not atto(canale="REST") and not atto(canale="SFTP") and not atto(canale=None)
    assert not atto(tipo="ALLEGATO") and not atto(formato="application/pdf")
    assert not atto(parametri={**PARAMETRI, "pratica_rettifica": ""})
    assert not atto(parametri={**PARAMETRI, "identificativo_documento": "DOC-1"})


def test_rgl105_non_rifiuta_l_atto_di_sistema() -> None:
    ambiente = AmbienteRegole(
        Risolutore(REGISTRO_V1, FornitoreDaDizionari(
            {Livello.CONSERVATORE: {"FORMATI_AMMESSI": ["application/pdf"]}})),
        ContestoRisoluzione(adesso=datetime.now(UTC)))

    def rilievi(atto: bool) -> list[str]:
        dati = DatiVersamento("contratti", (DocumentoDaValutare(
            "d1", "PRINCIPALE", "atto.xml", PARAMETRI, 10, "application/xml",
            atto_di_sistema=atto),))
        return [r.regola for r in CATALOGO_V1.valuta(dati, ambiente, Fase.FILE).rilievi]

    assert "RGL-105" in rilievi(False)
    assert "RGL-105" not in rilievi(True)


# ── Revisione R09 (Z4): vincolo congiunto della retention ─────────────────

def _piano_retention(modifiche, livelli=None):
    from datetime import UTC, datetime

    from libs.verumdoc_rules.configurazione import Ambito, Attore, ModificaValore, piano_valori
    from libs.verumdoc_rules.impostazioni import ContestoRisoluzione, Livello
    from libs.verumdoc_rules.parametri_servizio import REGISTRO_SERVIZIO

    return piano_valori(
        registro=REGISTRO_SERVIZIO, attore=Attore("RSC_CONSERVATORE", None, "c1"),
        ambito=Ambito(Livello.CONSERVATORE),
        modifiche=[ModificaValore(c, v) for c, v in modifiche],
        livelli=livelli or {Livello.CONSERVATORE: {}},
        contesto=ContestoRisoluzione(adesso=datetime(2026, 11, 1, tzinfo=UTC)))


def test_z4_anni_devono_superare_la_soglia_di_rinnovo() -> None:
    from libs.verumdoc_rules.impostazioni import Livello

    bloccati = lambda p: [b["codice"] for b in p.bloccati]  # noqa: E731
    # valori ammessi uno per uno, combinazione no (10/10, 20 con soglia 30 → 30 > 30 no)
    assert bloccati(_piano_retention([("RETENTION_PERMANENTE_ANNI", 10),
                                      ("RETENTION_RINNOVO_SOGLIA_ANNI", 10)])) \
        == ["RETENTION_PERMANENTE_ANNI"]
    assert bloccati(_piano_retention([("RETENTION_RINNOVO_SOGLIA_ANNI", 30)])) \
        == ["RETENTION_RINNOVO_SOGLIA_ANNI"]                     # predefinito 30 anni
    # una sola voce, l'altra già in vigore al livello del conservatore
    assert bloccati(_piano_retention(
        [("RETENTION_PERMANENTE_ANNI", 5)],
        {Livello.CONSERVATORE: {"RETENTION_RINNOVO_SOGLIA_ANNI": 5}})) \
        == ["RETENTION_PERMANENTE_ANNI"]
    assert bloccati(_piano_retention([("RETENTION_PERMANENTE_ANNI", 40),
                                      ("RETENTION_RINNOVO_SOGLIA_ANNI", 10)])) == []
