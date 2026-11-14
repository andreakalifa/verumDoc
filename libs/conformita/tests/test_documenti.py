# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R15 (VD-C-177) — documenti di conformità generati dalla configurazione.

Integrità del contenuto (REGOLE §0: il manuale non dichiara ciò che il sistema non sa):
  - con il database VUOTO e senza variabili del conservatore ogni dato che verrebbe da lì è
    un blocco «DA COMPILARE» con chi lo fornisce (nessun valore predefinito inventato);
  - con dati veri i valori compaiono e il campo non è più da compilare;
  - un dato di prova (dominio riservato) o non valido resta da compilare;
  - i requisiti normativi non riscontrati sono «da verificare sul testo ufficiale»;
  - il manuale copre tutti i contenuti minimi (lettere a–l) e RO7;
  - versioni: stessa configurazione = stessa impronta e nessuna versione nuova; un cambio
    di configurazione cambia solo le sezioni che lo usano (differenze);
  - HTML autonomo (nessuno script, nessuna risorsa esterna) e testo con escape;
  - edizione del repository e fotografia allineate (anche in ``make verifiche``).
"""
from __future__ import annotations

import copy
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from libs.conformita import conservatore, database, repository
from libs.conformita.documenti import Fatti, genera_tutti, manuale, piano_sicurezza
from libs.conformita.generazione import genera
from libs.conformita.modello import (
    DA_COMPILARE,
    MANUALE,
    PIANO_SICUREZZA,
    DaCompilare,
    DaVerificare,
    Tabella,
)
from libs.conformita.requisiti import REQUISITI
from libs.conformita.versioni import Edizione, componi, contenuto_da_firmare

RADICE = Path(__file__).resolve().parents[3]

#: dati che vengono dal database: con il database vuoto devono risultare da compilare
DATI_DAL_DATABASE = {"db.classi_documentali", "db.fornitori_firma", "db.fornitori_marca",
                     "db.fornitori_identita", "db.canali_pec"}
#: dati che non stanno nel sistema: sempre da compilare (chi li fornisce è dichiarato)
DATI_FUORI_DAL_SISTEMA = {"manuale.storico_responsabili", "manuale.deleghe",
                          "manuale.modello_organizzativo", "manuale.pubblico_ufficiale",
                          "manuale.normative_luoghi", "continuita.rpo", "continuita.rto",
                          "sicurezza.analisi_rischi", "sicurezza.violazioni_dati",
                          "sicurezza.certificazione_27001", "sicurezza.prove_owasp"}

AMBIENTE_VERO = {
    "CONSERVATORE_DENOMINAZIONE": "Conservatore Dimostrativo S.p.A.",
    "CONSERVATORE_CODICE_FISCALE": "12345678903",
    "CONSERVATORE_PARTITA_IVA": "12345678903",
    "CONSERVATORE_SEDE_LEGALE": "Via del Collaudo 1, Borgo Immaginario",
    "CONSERVATORE_PEC": "conservazione@pec.conservatore-dimostrativo.it",
    "CONSERVATORE_EMAIL_CONTATTO": "info@conservatore-dimostrativo.it",
    "CONSERVATORE_RESPONSABILE_SERVIZIO_NOME": "Responsabile Sentinella Uno",
    "CONSERVATORE_RESPONSABILE_FUNZIONE_ARCHIVISTICA_NOME": "Archivista Sentinella Due",
    "CONSERVATORE_RESPONSABILE_SICUREZZA_NOME": "Sicurezza Sentinella Tre",
    "CONSERVATORE_RESPONSABILE_PROTEZIONE_DATI_NOME": "Protezione Sentinella Quattro",
    "CONSERVATORE_UBICAZIONE_DATA_CENTER": "Unione europea (Italia)",
}


@pytest.fixture(scope="module")
def repo() -> dict[str, Any]:
    return repository.raccogli(RADICE)


def _db_pieno() -> dict[str, Any]:
    db = database.vuoti()
    db.update({
        "disponibile": True,
        "classi_globali": [{"codice": "classe-sentinella", "nome": "Classe Sentinella 7f3",
                            "tipologia": "DAI", "anni_conservazione": 17,
                            "conservazione_permanente": False,
                            "formati_ammessi": ["application/pdf"],
                            "metadati_obbligatori": ["oggetto"], "max_dimensione_mb": 64}],
        "classi_enti": 2,
        "fornitori_firma": [
            {"codice": "csc-sentinella", "ruolo": "FIRMA", "tipo": "CSC", "nome":
             "Firma Sentinella", "attivo": True, "priorita": 1},
            {"codice": "tsa-sentinella", "ruolo": "MARCA", "tipo": "TSA", "nome":
             "Marca Sentinella", "attivo": True, "priorita": 1}],
        "fornitori_identita": [{"tipo": "PASSWORD_TOTP", "nome": "Accesso Sentinella",
                                "attivo": True}],
        "canali_pec": [{"codice": "pec-sentinella", "tipo": "SMTP_IMAP", "nome":
                        "Canale Sentinella", "attivo": True, "priorita": 1}],
        "impostazioni": {"VERIFICA_CICLO_GIORNI": 123},
    })
    return db


def _campi(doc) -> set[str]:  # type: ignore[no-untyped-def]
    return {d.campo for d in doc.da_compilare()}


def test_db_vuoto_ogni_dato_mancante_da_compilare(repo) -> None:  # type: ignore[no-untyped-def]
    fatti = Fatti(repo, database.vuoti(), {})
    docs = genera_tutti(fatti)
    campi = set().union(*(_campi(d) for d in docs.values()))
    attesi = DATI_DAL_DATABASE | DATI_FUORI_DAL_SISTEMA | {
        f"conservatore.{c.chiave}" for c in conservatore.CAMPI if c.chiave != "email_contatto"}
    assert attesi - campi == set(), f"dati mancanti non dichiarati: {sorted(attesi - campi)}"
    for d in docs.values():
        for blocco in d.da_compilare():
            assert blocco.chi.strip() and blocco.descrizione.strip(), blocco
    # nessuna tabella di dati del database con il database vuoto
    md = componi(docs[MANUALE], Edizione()).markdown
    assert "Classi documentali attive del servizio (database)" not in md
    assert "Fornitori attivi" not in md and "Canali PEC attivi" not in md
    # ogni valore mancante in tabella rimanda al blocco dichiarato
    for t in (b for s in docs[MANUALE].tutte() for b in s.blocchi if isinstance(b, Tabella)):
        for riga in t.righe:
            assert all(c != "" for c in riga)


def test_dati_veri_compaiono_e_non_restano_da_compilare(repo) -> None:  # type: ignore[no-untyped-def]
    fatti = Fatti(repo, _db_pieno(), AMBIENTE_VERO)
    docs = genera_tutti(fatti)
    md = componi(docs[MANUALE], Edizione()).markdown + componi(
        docs[PIANO_SICUREZZA], Edizione()).markdown
    for atteso in ("Classe Sentinella 7f3", "17 anni", "csc-sentinella", "tsa-sentinella",
                   "Accesso Sentinella", "pec-sentinella", "Responsabile Sentinella Uno",
                   "Conservatore Dimostrativo S.p.A.", "Unione europea (Italia)",
                   "Gli enti hanno inoltre 2 classi"):
        assert atteso in md, atteso
    campi = _campi(docs[MANUALE]) | _campi(docs[PIANO_SICUREZZA])
    assert campi & DATI_DAL_DATABASE == set()
    assert not any(c.startswith("conservatore.") for c in campi)
    # impostazione del conservatore letta dal database, con l'origine
    assert re.search(r"`VERIFICA_CICLO_GIORNI` \| [^|]+ \| 123 \| conservatore \|", md)


@pytest.mark.parametrize("variabile,valore", [
    ("CONSERVATORE_PEC", "conservazione@pec.conservatore.example"),
    ("CONSERVATORE_PEC", "non-un-indirizzo"),
    ("CONSERVATORE_PARTITA_IVA", "12345678901"),          # cifra di controllo sbagliata
    ("CONSERVATORE_DENOMINAZIONE", "da definire"),
    ("CONSERVATORE_DENOMINAZIONE", "DA_IMPOSTARE"),
])
def test_dato_di_prova_o_non_valido_resta_da_compilare(variabile, valore) -> None:  # type: ignore[no-untyped-def]
    chiave = variabile.removeprefix("CONSERVATORE_").lower()
    assert isinstance(conservatore.dato({variabile: valore}, chiave), DaCompilare)
    assert conservatore.dato(AMBIENTE_VERO, chiave) == AMBIENTE_VERO[variabile]


def test_requisiti_non_riscontrati_da_verificare_e_lettere_coperte(repo) -> None:  # type: ignore[no-untyped-def]
    docs = genera_tutti(Fatti(repo, database.vuoti(), {}))
    coperti = {r for d in docs.values() for s in d.tutte() for r in s.requisiti}
    lettere = {c for c in REQUISITI if c.startswith("LG-MAN-") and c != "LG-MAN-NUM"}
    assert lettere == {f"LG-MAN-{x}" for x in "ABCDEFGHIJKL"}
    assert lettere | {"RO7", "RO1", "RO5"} <= coperti
    for d in docs.values():
        for s in d.tutte():
            verificare = {b.requisito for b in s.blocchi if isinstance(b, DaVerificare)}
            for codice in s.requisiti:
                r = REQUISITI[codice]
                dichiarato = any(r.sintesi in v for v in verificare)
                assert dichiarato == (not r.riscontrato), (s.id, codice)


def test_stessa_configurazione_stessa_impronta_e_nessuna_versione(repo) -> None:  # type: ignore[no-untyped-def]
    fatti = Fatti(repo, _db_pieno(), AMBIENTE_VERO)
    prima = genera(fatti, generata_il="2026-11-01T10:00:00+00:00")
    assert {g.versione for g in prima.values()} == {1}
    precedenti = {t: {"versione": 1, "impronta_contenuto": g.impronta_contenuto,
                      "sezioni": g.sezioni} for t, g in prima.items()}
    seconda = genera(fatti, generata_il="2026-11-02T10:00:00+00:00", precedenti=precedenti)
    assert all(g.versione is None for g in seconda.values())
    assert {t: g.impronta_contenuto for t, g in seconda.items()} == \
        {t: g.impronta_contenuto for t, g in prima.items()}
    # un fornitore di firma in più cambia solo le sezioni che usano i fornitori
    db = _db_pieno()
    db["fornitori_firma"].append({"codice": "csc-riserva", "ruolo": "FIRMA", "tipo": "CSC",
                                  "nome": "Riserva", "attivo": True, "priorita": 2})
    terza = genera(Fatti(repo, db, AMBIENTE_VERO), generata_il="2026-11-03T10:00:00+00:00",
                   precedenti=precedenti)
    m = terza[MANUALE]
    assert m.versione == 2
    assert {s["id"] for s in m.differenze["cambiate"]} == {"man.d", "man.all7"}
    assert m.differenze["aggiunte"] == [] and m.differenze["tolte"] == []
    assert "Differenze rispetto alla precedente" in m.markdown and "Allegato 7" in m.markdown
    assert terza["GUIDA_FUNZIONALITA"].versione is None
    assert terza[PIANO_SICUREZZA].versione == 2


def test_intestazione_versione_impronta_fonti(repo) -> None:  # type: ignore[no-untyped-def]
    g = genera(Fatti(repo, _db_pieno(), AMBIENTE_VERO),
               generata_il="2026-11-01T10:00:00+00:00")[MANUALE]
    assert "| Versione | 1 |" in g.markdown and "2026-11-01T10:00:00+00:00" in g.markdown
    assert g.impronta_contenuto in g.markdown
    assert "repo:docs/registro/criticita.md" not in g.fonti     # il manuale non lo usa
    assert g.fonti["db:classi_documentali"] == "letta in sola lettura"
    assert any(k.startswith("repo:libs/verumdoc_rules/permessi.py") for k in g.fonti)
    assert "CONSERVATORE_DENOMINAZIONE" in g.fonti["config:CONSERVATORE_*"]
    piano = genera(Fatti(repo, _db_pieno(), AMBIENTE_VERO),
                   generata_il="2026-11-01T10:00:00+00:00")[PIANO_SICUREZZA]
    assert any(k == "repo:docs/registro/criticita.md" for k in piano.fonti)


def test_rischi_residui_sono_le_criticita_aperte(repo) -> None:  # type: ignore[no-untyped-def]
    fatti = Fatti(repo, database.vuoti(), {})
    doc = piano_sicurezza(fatti)
    sezione = next(s for s in doc.sezioni if s.id == "sic.11")
    tabella = next(b for b in sezione.blocchi if isinstance(b, Tabella))
    aperte = {c["codice"] for c in repo["criticita"]
              if c["stato"] in ("Aperta", "Parzialmente chiusa")}
    assert {r[0] for r in tabella.righe} == aperte
    # una criticità chiusa non compare; una che si riapre sì
    finto = copy.deepcopy(repo)
    finto["criticita"][0]["stato"] = "Aperta"
    tabella2 = next(b for b in next(s for s in piano_sicurezza(Fatti(
        finto, database.vuoti(), {})).sezioni if s.id == "sic.11").blocchi
        if isinstance(b, Tabella))
    assert finto["criticita"][0]["codice"] in {r[0] for r in tabella2.righe}


def test_matrice_documentazione_mai_delegabile(repo) -> None:  # type: ignore[no-untyped-def]
    doc = manuale(Fatti(repo, database.vuoti(), {}))
    tabella = next(b for b in next(s for s in doc.sezioni if s.id == "man.all4").blocchi
                   if isinstance(b, Tabella))
    riga = next(r for r in tabella.righe if r[0] == "`documentazione:genera`")
    intestazioni = tabella.intestazioni
    valori = dict(zip(intestazioni, riga, strict=True))
    assert valori["Amministrazione del servizio"] == "sì"
    assert valori["Responsabile del servizio"] == "sì"
    assert valori["Amministrazione dell'ente"] == "—" and valori["Operatore"] == "—"
    assert valori["Delegabile a un ruolo personalizzato"] == "mai"


def test_html_autonomo_e_con_escape(repo) -> None:  # type: ignore[no-untyped-def]
    ambiente = {**AMBIENTE_VERO, "CONSERVATORE_DENOMINAZIONE": "<script>alert(1)</script> & C."}
    g = genera(Fatti(repo, _db_pieno(), ambiente),
               generata_il="2026-11-01T10:00:00+00:00")[MANUALE]
    assert "<script>alert(1)</script>" not in g.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; C." in g.html
    assert "<script" not in g.html.lower()
    assert not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", g.html)
    assert "@media print" in g.html and "@page" in g.html
    firma = contenuto_da_firmare(MANUALE, 3, g.html.encode())
    assert firma["nome"] == "manuale_conservazione_v3.html"
    assert firma["impronta_sha256"] == g.impronta_html


def test_fotografia_stabile_senza_date(repo) -> None:  # type: ignore[no-untyped-def]
    a = repository.serializza(repo)
    b = repository.serializza(repository.raccogli(RADICE))
    assert a == b
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", a)
    assert repo["fonti"]["docs/registro/criticita.md"]


def test_route_e_menu_dal_frontend(repo) -> None:  # type: ignore[no-untyped-def]
    route = {r["percorso"]: r for r in repo["frontend"]["route"]}
    assert route["/manuale"]["ruoli"] == ["SUPER_ADMIN", "RSC_CONSERVATORE"]
    doc = route["/documentazione-servizio"]
    assert doc["pagina"] == "DocumentazioneServizioPage"
    assert set(doc["ruoli"]) == {"SUPER_ADMIN", "RSC_CONSERVATORE"}
    assert doc["menu"] == {"area": "Conformità", "voce": "Documentazione del servizio",
                           "ruoli": ["SUPER_ADMIN", "RSC_CONSERVATORE"], "scope": None,
                           "permesso": None}
    assert route["/configurazione/quote"]["menu"]["area"] == "Configurazione"


def _strumento():  # type: ignore[no-untyped-def]
    percorso = RADICE / "tools" / "conformita" / "genera_documenti.py"
    spec = importlib.util.spec_from_file_location("genera_documenti", percorso)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def test_edizione_del_repository_allineata() -> None:
    vecchi = _strumento().scrivi_o_controlla(True)
    assert vecchi == [], f"rigenerare con python tools/conformita/genera_documenti.py: {vecchi}"


def test_controlla_esce_con_errore_se_vecchia(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    g = _strumento()
    attesi = g.edizione_repository()
    monkeypatch.setattr(g, "edizione_repository", lambda radice=None: attesi)
    for rel, testo in attesi.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(testo, encoding="utf-8")
    assert g.scrivi_o_controlla(True, tmp_path) == []
    (tmp_path / "GUIDA_FUNZIONALITA.md").write_text("vecchia\n", encoding="utf-8")
    assert g.scrivi_o_controlla(True, tmp_path) == ["GUIDA_FUNZIONALITA.md"]
    assert (tmp_path / "GUIDA_FUNZIONALITA.md").read_text(encoding="utf-8") == "vecchia\n"


def test_nessun_segnaposto_non_dichiarato_nei_generati(repo) -> None:  # type: ignore[no-untyped-def]
    """Il controllo della documentazione (R14, esteso in R15) non trova nulla nei generati
    con il database vuoto né con il database pieno."""
    spec = importlib.util.spec_from_file_location(
        "verifica_documentazione", RADICE / "tools" / "docs" / "verifica_documentazione.py")
    assert spec is not None and spec.loader is not None
    v = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = v
    spec.loader.exec_module(v)
    for db, amb in ((database.vuoti(), {}), (_db_pieno(), AMBIENTE_VERO)):
        for g in genera(Fatti(repo, db, amb), generata_il="2026-11-01T10:00:00+00:00").values():
            testo = v.MARCA_GENERATO + " -->\n\n" + g.markdown
            assert v.controlla_generato(g.nome_file, testo) == [], g.tipo
            assert DA_COMPILARE not in g.markdown.replace(
                "> **DA COMPILARE**", "").replace("DA COMPILARE (vedi sotto)", "")


def test_r17_pagine_e_menu_coerenti_con_i_permessi() -> None:
    """Revisione R17 (R17-12/13): la guida deriva i ruoli dal PERMESSO della pagina
    (versare: non la Consultazione; esibizioni: anche l'operatore) e la generazione fallisce
    se pagina e voce di menu ammettono ruoli diversi."""
    from pathlib import Path

    from libs.conformita import repository as r

    radice = Path(__file__).resolve().parents[3]
    rotte = {x["percorso"]: x for x in r._route(r._Lettore(radice))["route"]}
    assert "VIEWER" not in rotte["/versamento"]["ruoli"]
    assert "OPERATORE" in rotte["/versamento"]["ruoli"]
    assert {"OPERATORE", "VIEWER"} <= set(rotte["/distribution"]["ruoli"])
    nomi = {"SUPER_ADMIN": "", "OPERATORE": "", "VIEWER": ""}
    pagina = {"percorso": "/x", "ruoli": ["SUPER_ADMIN"],
              "menu": {"ruoli": ["SUPER_ADMIN", "OPERATORE"]}}
    assert r._divergenze([pagina], nomi) == [
        "/x: pagina ['SUPER_ADMIN'] ≠ menu ['OPERATORE', 'SUPER_ADMIN']"]
    assert r._divergenze([{**pagina, "ruoli": None, "menu": {"ruoli": None}}], nomi) == []
