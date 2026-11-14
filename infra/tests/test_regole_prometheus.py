# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Le regole d'allarme di Prometheus (infra/observability/regole) sono ben formate
e citano solo metriche che il codice registra davvero (R04 infra)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

RADICE = Path(__file__).resolve().parents[2]
REGOLE = sorted((RADICE / "infra" / "observability" / "regole").glob("*.yml"))
GRAVITA = {"pagina", "avviso"}


def _metriche_del_codice() -> set[str]:
    """Nomi passati a Counter/Gauge/Histogram/Summary di prometheus_client nel codice."""
    nomi: set[str] = set()
    modello = re.compile(
        r"\b(?:Counter|Gauge|Histogram|Summary)\(\s*[\"']([a-z_:][a-z0-9_:]*)[\"']")
    for cartella in ("services", "libs"):
        for f in (RADICE / cartella).rglob("*.py"):
            if "tests" in f.parts or "__pycache__" in f.parts:
                continue
            testo = f.read_text(encoding="utf-8")
            nomi |= set(modello.findall(testo))
            # raccoglitori propri (es. libs/shared/db/metriche.py): nome come chiave di un
            # dizionario
            nomi |= set(re.findall(r"[\"'](verumdoc_[a-z0-9_]+)[\"']\s*:", testo))
    # prometheus_client aggiunge _total ai Counter
    return nomi | {n + "_total" for n in nomi}


def test_regole_ben_formate() -> None:
    assert REGOLE, "nessun file di regole"
    for file in REGOLE:
        dati = yaml.safe_load(file.read_text(encoding="utf-8"))
        for gruppo in dati["groups"]:
            assert gruppo["name"] and gruppo["rules"]
            for r in gruppo["rules"]:
                assert re.fullmatch(r"[A-Z][A-Za-z0-9]+", r["alert"]), r
                assert r["expr"].strip() and r["labels"]["severity"] in GRAVITA, r
                assert r["annotations"]["summary"], r


def test_regole_citano_metriche_esistenti() -> None:
    nel_codice = _metriche_del_codice()
    mancanti = []
    for file in REGOLE:
        for gruppo in yaml.safe_load(file.read_text(encoding="utf-8"))["groups"]:
            for r in gruppo["rules"]:
                for nome in re.findall(r"\b(verumdoc_[a-z0-9_]+)\b", r["expr"]):
                    if nome not in nel_codice:
                        mancanti.append(f"{r['alert']}: {nome}")
    assert not mancanti, f"metriche non registrate dal codice: {mancanti}"


def _allarmi() -> dict[str, dict]:
    return {r["alert"]: r for file in REGOLE
            for gruppo in yaml.safe_load(file.read_text(encoding="utf-8"))["groups"]
            for r in gruppo["rules"]}


def test_allarmi_r05_export_dell_audit() -> None:
    """R05 (VD-C-112, rapporto catena §6.3): l'audit non esportato nel WORM e un
    segmento divergente sono allarmi (la finestra non esportata è l'unica in
    cui un superutente può alterare l'audit senza traccia)."""
    allarmi = _allarmi()
    ritardo = allarmi["AuditNonEsportato"]
    assert "verumdoc_catena_audit_ritardo_secondi" in ritardo["expr"]
    assert "7200" in ritardo["expr"] and ritardo["for"] == "15m"
    divergente = allarmi["AuditWormDivergente"]
    assert "verumdoc_catena_audit_worm_divergente" in divergente["expr"]
    assert divergente["labels"]["severity"] == "pagina"


def test_allarmi_revisione2_r05_scarto_e_audit_sospeso() -> None:
    """Revisione 2 di R05: S3 (cancellazione incompleta, lavoro che fallisce) e S7
    (export dell'audit trattenuto o sospeso) hanno un allarme proprio."""
    allarmi = _allarmi()
    incompleta = allarmi["ScartoEliminazioneIncompleta"]
    assert "verumdoc_ciclo_vita_pda_eliminazione_incompleta" in incompleta["expr"]
    assert incompleta["labels"]["severity"] == "pagina" and incompleta["for"] == "0m"
    assert "scarto_incompleto.md" in incompleta["annotations"]["description"]
    assert "verumdoc_ciclo_vita_pratiche_lavoro_in_errore" in \
        allarmi["PraticaLavoroInErrore"]["expr"]
    assert "verumdoc_catena_audit_transazione_aperta_secondi" in \
        allarmi["AuditExportTrattenuto"]["expr"]
    assert "verumdoc_catena_audit_orizzonte_non_verificabile" in \
        allarmi["AuditOrizzonteNonVerificabile"]["expr"]


def test_allarme_revisione4_r05_scarto_autorizzazioni_non_verificate() -> None:
    """Revisione 4 di R05 (U2): uno scarto bloccato dal lavoratore perché la
    catena delle autorizzazioni non regge è un allarme a pagina."""
    allarme = _allarmi()["ScartoAutorizzazioniNonVerificate"]
    assert "verumdoc_ciclo_vita_scarti_autorizzazioni_non_verificate" in allarme["expr"]
    assert allarme["labels"]["severity"] == "pagina" and allarme["for"] == "0m"
    assert "scarto_incompleto.md" in allarme["annotations"]["description"]


def test_allarmi_r06_verifica_audit_sul_codice_d_uscita() -> None:
    """R06 (VD-C-130): il CronJob giornaliero verifica-audit fallisce con codice
    d'uscita diverso da 0; Job fallito o nessuna verifica riuscita = allarme."""
    allarmi = _allarmi()
    fallita = allarmi["VerificaAuditFallita"]
    # revisione R06 (V3): esito definitivo del Job (condizione Failed), non i pod
    # falliti di tentativi intermedi; chiuso dalla verifica riuscita successiva
    assert 'kube_job_failed{job_name=~"hashchain-verifica-audit-.*", condition="true"}' \
        in fallita["expr"]
    assert "kube_job_status_failed" not in fallita["expr"]
    assert "kube_cronjob_status_last_successful_time" in fallita["expr"]
    assert "hashchain-verifica-audit" in fallita["expr"]
    assert fallita["labels"]["severity"] == "pagina" and fallita["for"] == "0m"
    mancante = allarmi["VerificaAuditNonEseguita"]
    assert 'cronjob="hashchain-verifica-audit"' in mancante["expr"]
    assert "93600" in mancante["expr"]  # 26 ore: un giorno più il margine


def test_allarme_revisione_r06_puntuali_autorizzazioni_non_verificate() -> None:
    """Revisione R06 (V2): annullamento o rettifica bloccati dal lavoratore prima
    del fatto sono un allarme a pagina, sulla metrica registrata dal codice."""
    allarme = _allarmi()["PuntualeAutorizzazioniNonVerificate"]
    assert "verumdoc_ciclo_vita_puntuali_autorizzazioni_non_verificate" in allarme["expr"]
    assert allarme["labels"]["severity"] == "pagina" and allarme["for"] == "0m"
