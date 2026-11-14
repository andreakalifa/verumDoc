# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Verificatore indipendente (CLI): dati costruiti con libs/catena (dati_prova.py)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI))
sys.path.insert(0, str(QUI.parent))

import dati_prova  # noqa: E402
import verifica_ricevuta as v  # noqa: E402


@pytest.fixture
def dati(tmp_path: Path) -> dict[str, str]:
    return dati_prova.costruisci(tmp_path)


def _base(d: dict[str, str]) -> list[str]:
    return ["--chiave-registro", d["chiave_registro_file"],
            "--chiave-testimone", d["chiave_testimone_file"]]


def _riga(testo: str, nome: str) -> str:
    return next(r for r in testo.splitlines() if r.strip().endswith(nome))


def test_esegui_ricevuta_completa_e_consistenza_verificata(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"], "--documento",
                              dati["documento"], "--checkpoint-vecchio", dati["checkpoint_vecchio"],
                              "--checkpoint-nuovo", dati["checkpoint_nuovo"],
                              "--prova-consistenza", dati["consistenza"]])
    assert codice == 0, testo
    assert "ESITO: VERIFICATA" in testo
    assert _riga(testo, "Inclusione nel registro").strip().startswith("OK")


def test_esegui_chiavi_scritte_direttamente_senza_documento_verificata(dati) -> None:
    """Il file originale è facoltativo: il controllo risulta «NON RICHIESTO»."""
    codice, testo = v.esegui(["--chiave-registro", dati["chiave_registro"],
                              "--chiave-testimone", dati["chiave_testimone"],
                              "--prova", dati["prova"]])
    assert codice == 0, testo
    assert _riga(testo, "Impronta del file").strip().startswith("NON RICHIESTO")


def test_esegui_documento_diverso_impronta_fallita(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--documento", dati["documento_altro"]])
    assert codice == 1
    assert _riga(testo, "Impronta del file").strip().startswith("FALLITO")


def test_esegui_documento_modificato_di_un_byte_fallita(dati, tmp_path) -> None:
    modificato = tmp_path / "modificato.xml"
    dati_file = bytearray(Path(dati["documento"]).read_bytes())
    dati_file[-2] ^= 1
    modificato.write_bytes(bytes(dati_file))
    codice, _ = v.esegui([*_base(dati), "--prova", dati["prova"], "--documento", str(modificato)])
    assert codice == 1


@pytest.mark.parametrize("campo, alterazione", [
    ("prova_pda", lambda p: p["percorso"].reverse()),
    ("prova_registro", lambda p: p.update(foglia_indice=p["foglia_indice"] + 1)),
    ("prova_registro", lambda p: p.update(dimensione_albero=p["dimensione_albero"] - 1)),
])
def test_verifica_prova_alterata_fallita(dati, tmp_path, campo, alterazione) -> None:
    prova = json.loads(Path(dati["prova"]).read_text(encoding="utf-8"))
    alterazione(prova[campo])
    file = tmp_path / "alterata.json"
    file.write_text(json.dumps(prova), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--prova", str(file)])
    assert codice == 1, testo


def test_verifica_foglia_non_canonica_fallita(dati, tmp_path) -> None:
    prova = json.loads(Path(dati["prova"]).read_text(encoding="utf-8"))
    prova["foglia_dati"] = prova["foglia_dati"].replace(",", ", ", 1)
    file = tmp_path / "spazi.json"
    file.write_text(json.dumps(prova), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--prova", str(file)])
    assert codice == 1 and "forma canonica" in testo


def test_verifica_checkpoint_senza_testimone_fallita_salvo_richiesta(dati, tmp_path) -> None:
    prova = json.loads(Path(dati["prova"]).read_text(encoding="utf-8"))
    prova["checkpoint"] = Path(dati["checkpoint_solo_registro"]).read_text(encoding="utf-8")
    file = tmp_path / "solo_registro.json"
    file.write_text(json.dumps(prova), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--prova", str(file)])
    assert codice == 1
    assert _riga(testo, "controfirma del testimone").strip().startswith("FALLITO")
    codice, testo = v.esegui(["--chiave-registro", dati["chiave_registro"], "--senza-testimone",
                              "--prova", str(file)])
    assert codice == 0, testo
    assert _riga(testo, "controfirma del testimone").strip().startswith("NON RICHIESTO")


def test_verifica_chiave_registro_sbagliata_fallita(dati) -> None:
    from libs.catena import nota as nota_lib

    _, altra = nota_lib.genera_chiave("verumdoc-registro")
    codice, testo = v.esegui(["--chiave-registro", altra, "--chiave-testimone",
                              dati["chiave_testimone"], "--prova", dati["prova"]])
    assert codice == 1
    assert _riga(testo, "firma del registro").strip().startswith("FALLITO")


def test_consistenza_registro_riscritto_fallita(dati) -> None:
    """Registro che riscrive una foglia già coperta dal checkpoint conservato
    dal cliente: anche con firme valide, la consistenza non torna."""
    codice, testo = v.esegui([*_base(dati), "--checkpoint-vecchio", dati["checkpoint_vecchio"],
                              "--checkpoint-nuovo", dati["checkpoint_riscritto"],
                              "--prova-consistenza", dati["consistenza_riscritta"]])
    assert codice == 1
    assert _riga(testo, "Consistenza fra i checkpoint").strip().startswith("FALLITO")
    assert _riga(testo, "Checkpoint recente 12: firma del registro").strip().startswith("OK")


@pytest.mark.parametrize("argomenti", [
    [],                                                   # manca --chiave-registro
    ["--chiave-registro", "non-una-chiave"],              # file inesistente
    ["--chiave-registro", "a+b+c", "--senza-testimone"],  # chiave malformata, nessuna prova
])
def test_main_ingressi_non_validi_uscita_1(argomenti, capsys) -> None:
    with pytest.raises(SystemExit) as uscita:
        raise SystemExit(v.main(argomenti))
    assert uscita.value.code == 1


def test_main_consistenza_incompleta_uscita_1(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--checkpoint-vecchio", dati["checkpoint_vecchio"]])
    assert codice == 1 and "servono" in testo


def test_script_eseguibile_come_processo_codici_uscita(dati) -> None:
    """Lo script gira da solo (python verifica_ricevuta.py) con codici 0/1."""
    script = QUI.parent / "verifica_ricevuta.py"
    ok = subprocess.run([sys.executable  # noqa: S603 — interprete corrente e script del repository
                        , str(script), *_base(dati), "--prova", dati["prova"]],
                        capture_output=True, text=True, check=False)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    ko = subprocess.run([sys.executable  # noqa: S603 — come sopra
                        , str(script), *_base(dati), "--prova", dati["prova"],
                         "--documento", dati["documento_altro"]],
                        capture_output=True, text=True, check=False)
    assert ko.returncode == 1


def test_impronta_file_grande_a_blocchi(tmp_path) -> None:
    import hashlib

    file = tmp_path / "grande.bin"
    blocco = bytes(range(256)) * 4096
    with open(file, "wb") as f:
        for _ in range(5):
            f.write(blocco)
    assert v.impronta_file(str(file)) == hashlib.sha256(blocco * 5).hexdigest()


# ── Rapporto di versamento definitivo v2 (K5, generato dalla hashchain) ──

def test_esegui_rdv_v2_con_documento_verificata(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--rdv", dati["rdv"],
                              "--documento", dati["documento_allegato"]])
    assert codice == 0, testo
    assert _riga(testo, "allegato.txt: Impronta del file").strip().startswith("OK")
    assert _riga(testo, "fattura.xml: Inclusione nel PdA").strip().startswith("OK")
    # Le firme dello stesso checkpoint compaiono una volta sola
    assert testo.count("controfirma del testimone") == 1


def test_esegui_rdv_v2_documento_estraneo_fallita(dati, tmp_path) -> None:
    estraneo = tmp_path / "estraneo.bin"
    estraneo.write_bytes(b"non conservato")
    codice, testo = v.esegui([*_base(dati), "--rdv", dati["rdv"], "--documento", str(estraneo)])
    assert codice == 1 and "nessun documento della ricevuta" in testo


def test_esegui_rdv_v2_impronta_dichiarata_alterata_fallita(dati, tmp_path) -> None:
    """Chi altera l'impronta scritta nel RdV non riesce a farla tornare con la foglia."""
    xml = Path(dati["rdv"]).read_text(encoding="utf-8")
    import hashlib

    vera = hashlib.sha256(Path(dati["documento"]).read_bytes()).hexdigest()
    alterato = tmp_path / "rdv_alterato.xml"
    alterato.write_text(xml.replace(f'algoritmo="SHA-256">{vera}<', 'algoritmo="SHA-256">'
                                    + "0" * 64 + "<", 1), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--rdv", str(alterato)])
    assert codice == 1
    assert _riga(testo, "fattura.xml: Impronta dichiarata nel rapporto = impronta della foglia"
                 ).strip().startswith("FALLITO")


def test_esegui_rdv_con_dtd_rifiutato(dati, tmp_path) -> None:
    file = tmp_path / "dtd.xml"
    file.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>', encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--rdv", str(file)])
    assert codice == 1 and "DTD" in testo


# ── R04 correzioni (rev4 rilievo 9): origine attesa e chiavi distinte ──

def test_checkpoint_di_sviluppo_non_vale_come_produzione(dati) -> None:
    """La prova del revisore: checkpoint con origine di sviluppo e chiavi valide
    → prima «VERIFICATA», ora fallita (origine diversa da quella attesa)."""
    codice, testo = v.esegui(["--prova", dati["prova_sviluppo"], *_base(dati)])
    assert codice == 1 and "FALLITO" in _riga(testo, "origine").upper()
    assert "verumdoc.it/registro/v1/development" in testo
    # con l'origine dichiarata esplicitamente (ambiente di sviluppo) è verificata
    codice, testo = v.esegui(["--prova", dati["prova_sviluppo"], *_base(dati),
                              "--origine", "verumdoc.it/registro/v1/development"])
    assert codice == 0, testo
    # e una ricevuta di produzione non vale per un ambiente di sviluppo
    codice, _ = v.esegui(["--prova", dati["prova"], *_base(dati),
                          "--origine", "verumdoc.it/registro/v1/development"])
    assert codice == 1


def test_consistenza_con_checkpoint_di_sviluppo_fallita(dati) -> None:
    codice, testo = v.esegui([
        "--checkpoint-vecchio", dati["checkpoint_sviluppo_vecchio"],
        "--checkpoint-nuovo", dati["checkpoint_sviluppo"],
        "--prova-consistenza", dati["consistenza"], *_base(dati)])
    assert codice == 1 and "origine" in testo


def test_stessa_chiave_come_registro_e_testimone_rifiutata(dati) -> None:
    """La prova del revisore: la chiave del registro anche nel campo del testimone
    faceva risultare OK la «controfirma» con la sola firma del registro."""
    codice, testo = v.esegui(["--prova", dati["prova_solo_registro"],
                              "--chiave-registro", dati["chiave_registro_file"],
                              "--chiave-testimone", dati["chiave_registro_file"]])
    assert codice == 1 and "coincide con quella del registro" in testo


def test_nomi_delle_chiavi_nei_loro_ruoli(dati, tmp_path) -> None:
    from libs.catena import nota

    altra = nota.genera_chiave("altro-testimone")[1]
    codice, testo = v.esegui(["--prova", dati["prova"], "--chiave-registro",
                              dati["chiave_registro_file"], "--chiave-testimone", altra])
    assert codice == 1 and "attesa 'verumdoc-testimone'" in testo
    codice, testo = v.esegui(["--prova", dati["prova"], "--chiave-registro",
                              dati["chiave_testimone_file"], "--chiave-testimone",
                              dati["chiave_registro_file"]])
    assert codice == 1 and "attesa 'verumdoc-registro'" in testo


# ── R05 (contratto Q3): foglie di formato 2 e stato del documento ─────────

def test_r05_prova_con_ciclo_vita_vuoto_stato_conservato(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"], "--documento",
                              dati["documento"]])
    assert codice == 0, testo
    assert _riga(testo, "Stato del documento").strip().startswith("OK")


def test_r05_rettifica_verificata_con_nota(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_rettificata"],
                              "--documento", dati["documento"]])
    assert codice == 0, testo
    assert "metadati rettificati il 2026-11-02T09:30:00.000000Z con pratica PR-2026-0007" in testo
    assert _riga(testo, "Ciclo di vita: rettifica (foglia 12)").strip().startswith("OK")


def test_r05_documento_scartato_mai_verificato(dati) -> None:
    """La ricevuta è autentica e il file è quello giusto, ma il PdA è stato scartato:
    esito «DOCUMENTO SCARTATO» con data e pratica, codice 2, MAI «VERIFICATA»."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_scartata"],
                              "--documento", dati["documento"]])
    assert codice == 2, testo
    assert "VERIFICATA —" not in testo.replace("NON VERIFICATA", "")
    assert "ESITO: DOCUMENTO SCARTATO" in testo
    assert "scartato il 2026-11-02T09:30:00.000000Z con pratica PS-2026-0042" in testo
    assert _riga(testo, "Impronta del file").strip().startswith("OK")
    assert _riga(testo, "Stato del documento").strip().startswith("SCARTATO")


def test_r05_foglia_di_scarto_alterata_non_verificata(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_scarto_alterato"]])
    assert codice == 1, testo
    assert _riga(testo, "Ciclo di vita: scarto (foglia 14)").strip().startswith("FALLITO")
    assert "ESITO: NON VERIFICATA" in testo


def test_r05_foglia_di_scarto_come_foglia_del_documento_rifiutata(dati) -> None:
    """Un tombstone incluso nel registro non prova la conservazione di nulla."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_scarto_come_documento"],
                              "--documento", dati["documento"]])
    assert codice == 1, testo
    riga = _riga(testo, "Formato della foglia del registro")
    assert riga.strip().startswith("FALLITO")
    assert "non prova la conservazione" in testo


def test_r05_formato_sconosciuto_mai_verificato(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_formato_sconosciuto"]])
    assert codice == 1, testo
    assert "formato di foglia sconosciuto" in testo


def test_r05_rdv_senza_ciclo_vita_dichiarato_non_applicabile(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--rdv", dati["rdv"]])
    assert codice == 0, testo
    assert _riga(testo, "Stato del documento").strip().startswith("NON APPLICABILE")


# ── Revisione R05 (R4): elenco del ciclo di vita troncato dal server ────────

def test_r05_ciclo_vita_troncato_mai_conservato(dati) -> None:
    """Il server dichiara 51 foglie e ne restituisce una (troncamento): lo stato
    del documento NON è «conservato» ma non determinabile, e la ricevuta non
    risulta verificata."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_troncata"],
                              "--documento", dati["documento"]])
    assert codice == 1, testo
    riga = _riga(testo, "Stato del documento")
    assert riga.strip().startswith("NON ESEGUITO")
    assert "incompleto (1 di 51 foglie)" in testo
    assert "ESITO: NON VERIFICATA" in testo


def test_r05_ciclo_vita_troncato_con_scarto_lo_scarto_prevale(dati) -> None:
    """Scarto e annullamento hanno sempre la precedenza: anche su un elenco
    troncato l'esito è «DOCUMENTO SCARTATO»."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_scartata_troncata"],
                              "--documento", dati["documento"]])
    assert codice == 2, testo
    assert "ESITO: DOCUMENTO SCARTATO" in testo


def test_r05_totale_dichiarato_maggiore_vale_come_troncato(dati) -> None:
    """Un totale maggiore delle foglie presenti è un troncamento anche se
    ``ciclo_vita_completo`` dice true."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_totale_maggiore"],
                              "--documento", dati["documento"]])
    assert codice == 1, testo
    assert "incompleto (1 di 2 foglie)" in testo


# ── Revisione 2 di R05 (S8): attestazione del ciclo di vita ─────────────────

def test_r05_rev2_attestazione_valida_nella_prova(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"]])
    assert codice == 0, testo
    assert _riga(testo, "Attestazione del ciclo di vita").strip().startswith("OK")
    assert "elenco attestato dal registro" in testo


def test_r05_rev2_scarto_tolto_dalla_prova_mai_conservato(dati) -> None:
    """S8: chi presenta la prova toglie la foglia di scarto e i campi del
    conteggio: l'attestazione firmata dice 2 foglie, la prova ne porta 1 →
    stato non determinabile, mai «conservato» (prima: «conservato»)."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_scarto_tolto"],
                              "--documento", dati["documento"]])
    assert codice == 1, testo
    assert _riga(testo, "Stato del documento").strip().startswith("NON ESEGUITO")
    assert "incompleto (1 di 2 foglie)" in testo
    assert "ESITO: NON VERIFICATA" in testo


def test_r05_rev2_senza_attestazione_assenza_di_scarto_non_dimostrabile(dati) -> None:
    """Tolta anche l'attestazione: il verificatore lo dice e non conclude."""
    codice, testo = v.esegui([*_base(dati), "--prova",
                              dati["prova_scarto_tolto_senza_attestazione"]])
    assert codice == 1, testo
    assert _riga(testo, "Attestazione del ciclo di vita").strip().startswith("NON ESEGUITO")
    stato = _riga(testo, "Stato del documento")
    assert stato.strip().startswith("NON ESEGUITO")
    assert "assenza di scarto non dimostrabile" in testo


@pytest.mark.parametrize("caso", ["prova_attestazione_altrui", "prova_attestazione_falsa"])
def test_r05_rev2_attestazione_di_altro_checkpoint_o_chiave_fallita(dati, caso) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati[caso]])
    assert codice == 1, testo
    assert _riga(testo, "Attestazione del ciclo di vita").strip().startswith("FALLITO")


def test_r05_rev2_foglia_sostituita_impronta_diversa(dati, tmp_path) -> None:
    """Stesso numero di foglie ma elenco diverso (foglia sostituita): fallito."""
    prova = json.loads(Path(dati["prova_rettificata"]).read_text(encoding="utf-8"))
    altra = json.loads(Path(dati["prova_scartata"]).read_text(encoding="utf-8"))
    prova["attestazione_ciclo_vita"] = altra["attestazione_ciclo_vita"]
    # stesso checkpoint della scartata ma con la sola rettifica presentata due volte
    altra["ciclo_vita"] = [altra["ciclo_vita"][0], altra["ciclo_vita"][0]]
    file = tmp_path / "sostituita.json"
    file.write_text(json.dumps(altra), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--prova", str(file)])
    assert codice == 1, testo
    assert "non sono quelle attestate" in testo


# ── Revisione 3 di R05 (T2, T3): istante, stato del PdA, checkpoint corrente ──

def _dettaglio(testo: str, nome: str) -> str:
    """Riga di dettaglio (quella dopo l'esito) del controllo ``nome``."""
    righe = testo.splitlines()
    i = next(i for i, r in enumerate(righe) if r.strip().endswith(nome))
    return righe[i + 1]


def test_r05_rev3_stato_sempre_con_checkpoint_e_data(dati) -> None:
    """T2: «conservato» dice SEMPRE a quale stato del registro si riferisce."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"]])
    assert codice == 0, testo
    stato = _dettaglio(testo, "Stato del documento")
    assert "stato al checkpoint 12 del 2026-10-23T08:16:30Z" in stato
    assert "--checkpoint-corrente" in stato
    assert "rilasciata il 2026-10-23T08:16:30Z" in _dettaglio(
        testo, "Attestazione del ciclo di vita")


def test_r05_rev3_checkpoint_corrente_piu_recente_mai_conservato(dati) -> None:
    """T2: la prova (attestata al checkpoint 12) è più vecchia del checkpoint
    corrente indicato (15, dopo lo scarto): lo stato attuale non è dimostrato
    (prima: «conservato», codice 0)."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"]])
    assert codice == 1, testo
    assert _riga(testo, "Stato del documento").strip().startswith("NON ESEGUITO")
    stato = _dettaglio(testo, "Stato del documento")
    assert "stato al checkpoint 12 del 2026-10-23T08:16:30Z" in stato
    assert "il registro ha ora 15 foglie" in stato
    assert "ESITO: NON VERIFICATA" in testo
    assert _riga(testo, "Checkpoint corrente 15: firma del registro").strip().startswith("OK")


def test_r05_rev3_checkpoint_corrente_uguale_conservato(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--checkpoint-corrente", dati["checkpoint_nuovo"]])
    assert codice == 0, testo
    assert "è il checkpoint corrente indicato" in _dettaglio(testo, "Stato del documento")


def test_r05_rev3_checkpoint_corrente_di_altra_chiave_fallito(dati, tmp_path) -> None:
    from libs.catena import nota as nota_lib

    estranea = nota_lib.analizza_firmatario(nota_lib.genera_chiave("verumdoc-registro")[0])
    cp = nota_lib.analizza(Path(dati["checkpoint_dopo_scarto"]).read_text(encoding="utf-8"))
    corpo = nota_lib.formatta(cp)
    falso = tmp_path / "corrente.txt"
    falso.write_text(nota_lib.componi(corpo, [estranea.firma(corpo)]), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--checkpoint-corrente", str(falso)])
    assert codice == 1, testo
    assert _riga(testo, "Checkpoint corrente 15: firma del registro").strip().startswith(
        "FALLITO")


def test_r05_rev3_pda_scartato_in_parte_mai_conservato(dati) -> None:
    """T3: PdA ELIMINAZIONE_INCOMPLETA (nessuna foglia di scarto): il registro
    attesta SCARTO_PARZIALE → «DOCUMENTO SCARTATO IN PARTE», codice 2."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_scarto_parziale"],
                              "--documento", dati["documento"]])
    assert codice == 2, testo
    assert _riga(testo, "Stato del documento").strip().startswith("SCARTATO IN PARTE")
    assert "ESITO: DOCUMENTO SCARTATO IN PARTE" in testo
    assert "conservato:" not in testo


def test_r05_rev3_pda_in_scarto_mai_conservato(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova_in_scarto"]])
    assert codice == 2, testo
    assert _riga(testo, "Stato del documento").strip().startswith("SCARTATO")
    assert "cancellazione è stata avviata" in testo


def test_r05_rev3_stato_alterato_firma_non_valida(dati, tmp_path) -> None:
    """Lo stato è firmato: SCARTO_PARZIALE riscritto in CONSERVATO non vale."""
    prova = json.loads(Path(dati["prova_scarto_parziale"]).read_text(encoding="utf-8"))
    prova["attestazione_ciclo_vita"] = prova["attestazione_ciclo_vita"].replace(
        "stato SCARTO_PARZIALE", "stato CONSERVATO")
    file = tmp_path / "alterata.json"
    file.write_text(json.dumps(prova), encoding="utf-8")
    codice, testo = v.esegui([*_base(dati), "--prova", str(file)])
    assert codice == 1, testo
    assert "firma del registro assente o non valida" in testo


# ── Revisione 4 di R05 (U3): checkpoint corrente confrontato con la prova ──

def test_r05_rev4_checkpoint_corrente_piu_vecchio_della_prova_fallito(dati) -> None:
    """U3a: un «corrente» più vecchio (6) della prova (12) non è il corrente
    (prima: «conservato … è il checkpoint corrente indicato», codice 0)."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--checkpoint-corrente", dati["checkpoint_vecchio"]])
    assert codice == 1, testo
    assert _riga(testo, "Checkpoint corrente rispetto alla prova").strip().startswith("FALLITO")
    assert "checkpoint fornito più vecchio della prova" in testo
    assert _riga(testo, "Stato del documento").strip().startswith("NON ESEGUITO")
    assert "è il checkpoint corrente indicato" not in testo
    assert "ESITO: NON VERIFICATA" in testo


def test_r05_rev4_checkpoint_corrente_stessa_dimensione_radice_diversa_biforcato(dati) -> None:
    """U3b: stessa dimensione, radice diversa, entrambi firmati: REGISTRO
    BIFORCATO, mai «VERIFICATA» (prima: codice 0)."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--checkpoint-corrente", dati["checkpoint_riscritto"]])
    assert codice == 1, testo
    assert _riga(testo, "Checkpoint corrente rispetto alla prova").strip().startswith("FALLITO")
    assert "REGISTRO BIFORCATO" in testo
    assert _riga(testo, "Stato del documento").strip().startswith("NON ESEGUITO")
    assert "ESITO: NON VERIFICATA" in testo


def test_r05_rev4_checkpoint_corrente_piu_recente_con_consistenza(dati) -> None:
    """U3c: corrente più recente con la prova di consistenza vera: nessuna
    riscrittura, ma lo stato attuale resta non dimostrato (mai «conservato»)."""
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"],
                              "--consistenza-corrente", dati["consistenza_corrente"]])
    assert codice == 1, testo
    assert _riga(testo, "Consistenza con il checkpoint corrente").strip().startswith("OK")
    assert "nessuna riscrittura" in testo
    assert _riga(testo, "Stato del documento").strip().startswith("NON ESEGUITO")


def test_r05_rev4_checkpoint_corrente_piu_recente_consistenza_falsa_biforcato(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"],
                              "--consistenza-corrente", dati["consistenza_corrente_falsa"]])
    assert codice == 1, testo
    assert _riga(testo, "Consistenza con il checkpoint corrente").strip().startswith("FALLITO")
    assert "REGISTRO BIFORCATO o riscritto" in testo


def test_r05_rev4_checkpoint_corrente_piu_recente_senza_consistenza(dati) -> None:
    codice, testo = v.esegui([*_base(dati), "--prova", dati["prova"],
                              "--checkpoint-corrente", dati["checkpoint_dopo_scarto"]])
    assert codice == 1, testo
    assert _riga(testo, "Consistenza con il checkpoint corrente").strip().startswith(
        "NON RICHIESTO")
