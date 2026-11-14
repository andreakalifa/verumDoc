# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R17 — migrazione ``20261112_059_conferme_decisione``.

  - VD-P-155: tolti (con lo storico) il valore NON_RICHIESTA del conservatore, i limiti per
    ente e i valori NON_RICHIESTA degli enti senza il limite concesso; resta la deroga di
    un ente a cui il conservatore aveva concesso il limite NON_RICHIESTA; un valore
    RICHIESTA dell'ente resta;
  - transizione: le pratiche aperte con le azioni già decise finiscono in
    ``conferme_transizione_r17``, quelle concluse no; quelle già sospese hanno il fatto
    ``SOSPESA`` (revisione: la ripresa vuole la sua conferma);
  - diritti minimi: nulla a PUBLIC né al gruppo comune; registrazione al solo gruppo
    dell'API di ciclo-vita (colonne d'origine), fatti al solo lavoratore;
  - reversibile (giù alla 058 e di nuovo su).
"""
from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlsplit

import pytest

from tests.infra_reale import PG_TEMPLATE_URL, richiede_pg
from tests.test_r05_revisione2_migrazioni_riprendibili import _crea_db, _elimina_db, _upgrade
from tests.test_r15_migrazione_058 import _privilegio
from tests.test_schema_alembic import _config, _esegui, _url_db

pytestmark = [pytest.mark.integration, richiede_pg]

CODICE = "RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE"


def _sql(url: str, istruzione: str, *argomenti):  # type: ignore[no-untyped-def]
    return asyncio.run(_esegui(url, istruzione, *argomenti))


def _ente(url: str) -> uuid.UUID:
    ente = uuid.uuid4()
    _sql(url, "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
              "email_referente) VALUES ($1, $2, 'Ente di prova', $3, 'e@example.org')",
         ente, ente.hex[:16], f"t_{ente.hex[:20]}")
    return ente


def _valore(url: str, livello: str, ente: uuid.UUID | None, codice: str, valore: str) -> None:
    _sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, valore, "
              "motivo) VALUES ($1, $2, $3, to_jsonb($4::text), 'prova R17')",
         livello, ente, codice, valore)


def _pratica(url: str, ente: uuid.UUID, tipo: str, stato: str, *, lavoro: str | None = None,
             conclusa: bool = False) -> uuid.UUID:
    pid = uuid.uuid4()
    _sql(url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, lavoro, "
              "conclusa_il) VALUES ($1, $2, $3, $4, 'pratica di prova R17', $5, "
              "CASE WHEN $6 THEN now() END)", pid, ente, tipo, stato, lavoro, conclusa)
    return pid


def test_migrazione_059_deroghe_transizione_privilegi(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command

    nome = _crea_db(urlsplit(PG_TEMPLATE_URL).path.lstrip("/"))
    url = _url_db(nome)
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        command.downgrade(_config(), "20261111_058")
        a, b, c = _ente(url), _ente(url), _ente(url)
        _valore(url, "CONSERVATORE", None, CODICE, "NON_RICHIESTA")
        _valore(url, "ENTE", a, CODICE, "NON_RICHIESTA")          # senza limite: tolta
        _valore(url, "ENTE", b, CODICE, "NON_RICHIESTA")          # con limite: deroga
        _valore(url, "ENTE", b, f"LIMITE.{CODICE}", "NON_RICHIESTA")
        _valore(url, "ENTE", c, CODICE, "RICHIESTA")              # resta
        scarto = _pratica(url, a, "SCARTO", "ESEGUIBILE", lavoro="ESEGUI")
        proposta = _pratica(url, a, "SCARTO", "PROPOSTA")
        approvato = _pratica(url, a, "ANNULLAMENTO", "APPROVATA", lavoro="ANNULLA")
        rettifica = _pratica(url, a, "RETTIFICA", "IN_VERSAMENTO", lavoro="ATTENDI")
        riversamento = _pratica(url, a, "RIVERSAMENTO_USCITA", "ESEGUIBILE")
        concluso = _pratica(url, a, "ANNULLAMENTO", "ESEGUITA", conclusa=True)
        sospesa = _pratica(url, a, "SCARTO", "ESEGUIBILE")
        _sql(url, "UPDATE public.pratiche SET ultimo_errore = jsonb_build_object('codice', "
                  "'LC-409-TENTATIVI-ESAURITI', 'lavoro_sospeso', 'ESEGUI') WHERE id = $1",
             sospesa)
        assert _upgrade(url, monkeypatch) is None
        # revisione R17: la pratica già sospesa riparte solo con una ripresa confermata
        assert [r["evento"] for r in _sql(
            url, "SELECT evento FROM public.conferme_lavoratore WHERE pratica_id = $1",
            sospesa)] == ["SOSPESA"]
        righe = {(r["livello"], r["tenant_id"], r["codice"]): r["v"] for r in _sql(
            url, "SELECT livello, tenant_id, codice, valore #>> '{}' AS v "
                 "FROM public.impostazioni_valori WHERE codice LIKE '%' || $1", CODICE)}
        assert righe == {("ENTE", b, CODICE): "NON_RICHIESTA", ("ENTE", c, CODICE): "RICHIESTA"}
        storico = _sql(url, "SELECT livello, tenant_id, codice FROM public.impostazioni_storico "
                            "WHERE motivo LIKE 'R17 (VD-P-155%' ORDER BY codice, livello")
        assert {(s["livello"], s["tenant_id"], s["codice"]) for s in storico} == {
            ("CONSERVATORE", None, CODICE), ("ENTE", a, CODICE),
            ("ENTE", b, f"LIMITE.{CODICE}")}
        transizione = {r["pratica_id"]: sorted(r["azioni"]) for r in _sql(
            url, "SELECT pratica_id, azioni FROM public.conferme_transizione_r17")}
        assert transizione == {
            scarto: ["SCARTO_APPROVA", "SCARTO_ESEGUI"],
            approvato: ["ANNULLAMENTO_APPROVA", "ANNULLAMENTO_RICHIEDI"],
            rettifica: ["RETTIFICA_RICHIEDI"],
            sospesa: ["SCARTO_APPROVA", "SCARTO_ESEGUI"],
        }
        assert proposta not in transizione and riversamento not in transizione
        assert concluso not in transizione
        # diritti minimi
        conf, fatti, trans = ("conferme_decisione", "conferme_lavoratore",
                              "conferme_transizione_r17")
        for tabella in (conf, fatti, trans):
            assert not _privilegio(url, "verumdoc_applicazione", tabella, "SELECT")
            assert not _privilegio(url, "verumdoc_applicazione", tabella, "INSERT")
        reg, lav = "verumdoc_conferme_registra", "verumdoc_ciclo_vita_lavoratore"
        assert _privilegio(url, reg, conf, "INSERT", "payload")
        assert not _privilegio(url, reg, conf, "INSERT", "registrata_il")
        assert not _privilegio(url, reg, conf, "INSERT", "versione_pratica")
        assert not _privilegio(url, reg, conf, "UPDATE")
        assert not _privilegio(url, reg, fatti, "SELECT")
        assert not _privilegio(url, reg, trans, "SELECT")
        assert _privilegio(url, lav, conf, "SELECT")
        assert not _privilegio(url, lav, conf, "INSERT")
        assert _privilegio(url, lav, fatti, "INSERT", "nonce")
        assert not _privilegio(url, lav, fatti, "INSERT", "created_at")
        assert _privilegio(url, lav, trans, "SELECT")
        assert not _privilegio(url, lav, trans, "INSERT")
        trigger = {r["tgname"] for r in _sql(
            url, "SELECT tgname FROM pg_trigger WHERE tgrelid IN ("
                 "'public.conferme_decisione'::regclass, 'public.conferme_lavoratore'::regclass, "
                 "'public.conferme_transizione_r17'::regclass) AND NOT tgisinternal")}
        assert {"trg_conferme_decisione_nuove", "trg_conferme_decisione_protette",
                "trg_conferme_decisione_troncamento", "trg_conferme_lavoratore_nuove",
                "trg_conferme_lavoratore_protette", "trg_conferme_lavoratore_troncamento",
                "trg_conferme_transizione_r17_protetta"} <= trigger
        # reversibile, poi di nuovo su
        command.downgrade(_config(), "20261111_058")
        assert _sql(url, "SELECT to_regclass('public.conferme_decisione') AS t")[0]["t"] is None
        assert _upgrade(url, monkeypatch) is None
    finally:
        _elimina_db(nome)
