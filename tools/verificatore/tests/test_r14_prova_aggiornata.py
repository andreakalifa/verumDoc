# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R14 (VD-C-123) — stato ATTUALE di un documento dimostrato offline con una prova
aggiornata.

Prima di R14 la ricevuta (prova al checkpoint 12) con il checkpoint corrente (15)
dimostrava solo che il registro non era stato riscritto: lo stato attuale restava «non
dimostrato» e non c'era modo di presentare insieme una prova di oggi legata alla ricevuta.
Ora ``--prova-aggiornata`` (scaricata dal cliente con le sue credenziali) dà lo stato al
checkpoint corrente, legata alla ricevuta (stessa foglia del registro, stesso documento,
stessa posizione); una prova di un altro documento non vale.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dati_prova  # noqa: E402
import verifica_ricevuta as v  # noqa: E402


@pytest.fixture
def dati(tmp_path: Path) -> dict[str, str]:
    return dati_prova.costruisci(tmp_path)


def _base(d: dict[str, str]) -> list[str]:
    return ["--chiave-registro", d["chiave_registro"], "--chiave-testimone",
            d["chiave_testimone"]]


def _righe(testo: str, nome: str) -> list[str]:
    return [r.strip() for r in testo.splitlines() if r.strip().endswith(nome)]


def test_prova_aggiornata_dimostra_lo_stato_attuale(dati) -> None:
    """Ricevuta conservata al checkpoint 12, prova di oggi al corrente 15 con lo scarto:
    il documento risulta SCARTATO ora (codice 2), con la ricevuta autentica e il registro
    corrente che estende quello della ricevuta."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--prova-aggiornata", dati["prova_scartata"],
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"],
                              "--consistenza-corrente", dati["consistenza_corrente"]])
    assert codice == 2, testo
    assert _righe(testo, v.NOME_AGGIORNATA)[0].startswith("OK")
    assert all(r.startswith("OK") for r in _righe(testo, v.NOME_CONSISTENZA_CORRENTE))
    assert "DOCUMENTO SCARTATO" in testo


def test_prova_aggiornata_non_al_checkpoint_corrente_non_dimostra(dati) -> None:
    """Una prova «aggiornata» più vecchia del corrente (rettificata al 13, corrente 15)
    non dimostra lo stato attuale: niente «verificata»."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--prova-aggiornata", dati["prova_rettificata"],
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"]])
    assert codice == 1, testo
    assert _righe(testo, v.NOME_AGGIORNATA)[0].startswith("OK")


def test_senza_prova_aggiornata_lo_stato_attuale_resta_non_dimostrato(dati) -> None:
    codice, _ = v.esegui([*_base(dati), "--prova", dati["prova"],
                          "--checkpoint-corrente", dati["checkpoint_dopo_scarto"]])
    assert codice == 1


def test_prova_aggiornata_di_un_altro_documento_fallisce(dati, tmp_path) -> None:
    altra = json.loads(Path(dati["prova_scartata"]).read_text(encoding="utf-8"))
    altra["foglia_dati"] = altra["foglia_dati"].replace('"doc":', '"doc_x":', 1)
    falsa = tmp_path / "aggiornata_altra.json"
    falsa.write_text(json.dumps(altra), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--prova-aggiornata", str(falsa),
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"]])
    assert codice == 1, testo
    riga = _righe(testo, v.NOME_AGGIORNATA)[0]
    assert riga.startswith("FALLITO")
    assert "documento diverso" in testo


def test_prova_aggiornata_di_un_altra_foglia_fallisce(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--prova-aggiornata", dati["prova_scarto_come_documento"],
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"]])
    assert codice == 1, testo
    assert _righe(testo, v.NOME_AGGIORNATA)[0].startswith("FALLITO")
    assert "foglia del registro diversa" in testo


def test_prova_aggiornata_richiede_il_checkpoint_corrente(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--prova-aggiornata", dati["prova_scartata"]])
    assert codice == 1 and "richiede --checkpoint-corrente" in testo


def test_prova_aggiornata_con_foglia_del_registro_diversa_fallisce(dati, tmp_path) -> None:
    """Stesso documento e stessa posizione, ma un'altra foglia del registro (altra radice
    del PdA): non è la prova della ricevuta."""
    altra = json.loads(Path(dati["prova_scartata"]).read_text(encoding="utf-8"))
    foglia = json.loads(altra["foglia_registro_dati"])
    foglia["radice_pda"] = "00" * 32
    altra["foglia_registro_dati"] = json.dumps(foglia, sort_keys=True, separators=(",", ":"))
    falsa = tmp_path / "aggiornata_foglia.json"
    falsa.write_text(json.dumps(altra), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--prova-aggiornata", str(falsa),
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"]])
    assert codice == 1, testo
    riga = _righe(testo, v.NOME_AGGIORNATA)[0]
    assert riga.startswith("FALLITO") and "posizione" not in testo.split(riga)[1].split("\n")[1]


def test_prova_aggiornata_su_registro_biforcato_fallisce(dati) -> None:
    """Revisione R14 (1): ricevuta al checkpoint 12, prova aggiornata e checkpoint corrente
    di 12 foglie con un'altra radice (registro riscritto, firmato): la ricevuta si confronta
    SEMPRE con il corrente, anche a pari dimensione → «REGISTRO BIFORCATO», mai verificata
    (prima: il confronto si faceva solo con un corrente più grande, codice 0)."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--prova-aggiornata", dati["prova_biforcata"],
                              "--checkpoint-corrente", dati["checkpoint_riscritto"]])
    assert codice == 1, testo
    assert "REGISTRO BIFORCATO" in testo
    assert any(r.startswith("FALLITO") for r in _righe(testo, v.NOME_CORRENTE))
