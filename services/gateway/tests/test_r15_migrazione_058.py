# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R15 (VD-C-177) — migrazione ``20261111_058_documentazione`` (nata 057 in R15, rinumerata
all'unione con R16).

  - reversibile (giù alla 057 di R16 e di nuovo su); l'assenza di differenze dai modelli la prova
    ``tests/test_schema_alembic.py``;
  - vincolo dei ruoli personalizzati: ``documentazione:genera`` mai delegabile (anche dopo la
    ripresa), tolto dal downgrade che ripristina il vincolo di R14;
  - GRANT minimi: nulla a PUBLIC né a ``verumdoc_applicazione``; dashboard-api (gruppo
    ``verumdoc_documentazione``) inserisce solo le colonne d'origine e non aggiorna né
    cancella; la hashchain (``verumdoc_documentazione_archivio``) aggiorna solo le colonne
    dell'archivio; l'auditor legge solo le colonne di configurazione dichiarate;
  - i trigger d'immutabilità (anche BEFORE INSERT) esistono;
  - revisione R17: downgrade rifiutato se esistono versioni generate (evidenze).
"""
from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import urlsplit

import pytest

from tests.infra_reale import PG_TEMPLATE_URL, richiede_pg
from tests.test_r05_revisione2_migrazioni_riprendibili import _crea_db, _elimina_db, _upgrade
from tests.test_schema_alembic import _config, _esegui, _url_db

pytestmark = [pytest.mark.integration, richiede_pg]


def _vincolo(url: str) -> str:
    righe = asyncio.run(_esegui(url, "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
                                     "WHERE conname = 'ck_ruoli_personalizzati_non_delegabili'"))
    return righe[0]["d"]


def _privilegio(url: str, ruolo: str, tabella: str, diritto: str, colonna: str | None = None
                ) -> bool:
    if colonna:
        sql = (f"SELECT has_column_privilege('{ruolo}', 'public.{tabella}', '{colonna}', "
               f"'{diritto}') AS p")
    else:
        sql = f"SELECT has_table_privilege('{ruolo}', 'public.{tabella}', '{diritto}') AS p"
    return bool(asyncio.run(_esegui(url, sql))[0]["p"])


def test_migrazione_058_privilegi_vincolo_reversibile(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command

    nome = _crea_db(urlsplit(PG_TEMPLATE_URL).path.lstrip("/"))
    url = _url_db(nome)
    try:
        assert "documentazione:genera" in _vincolo(url)
        v = "documentazione_versioni"
        g = "documentazione_generazioni"
        assert not _privilegio(url, "verumdoc_applicazione", v, "SELECT")
        assert not _privilegio(url, "verumdoc_applicazione", g, "INSERT")
        doc, arch, aud = ("verumdoc_documentazione", "verumdoc_documentazione_archivio",
                          "verumdoc_auditor")
        assert _privilegio(url, doc, v, "SELECT")
        assert _privilegio(url, doc, v, "INSERT", "markdown")
        assert not _privilegio(url, doc, v, "INSERT", "archiviata_il")
        assert not _privilegio(url, doc, v, "UPDATE") and not _privilegio(url, doc, v, "DELETE")
        assert _privilegio(url, doc, g, "UPDATE", "stato")
        assert not _privilegio(url, doc, g, "UPDATE", "scade_il")
        assert _privilegio(url, arch, v, "UPDATE", "worm_chiave_md")
        assert not _privilegio(url, arch, v, "UPDATE", "markdown")
        assert not _privilegio(url, arch, v, "INSERT")
        assert _privilegio(url, aud, "classi_documentali", "SELECT", "codice")
        assert _privilegio(url, aud, "fornitori_firma", "SELECT", "nome")
        assert not _privilegio(url, aud, "fornitori_firma", "SELECT", "configurazione")
        assert not _privilegio(url, aud, v, "INSERT")
        trigger = {r["tgname"] for r in asyncio.run(_esegui(
            url, "SELECT tgname FROM pg_trigger WHERE tgrelid IN "
                 "('public.documentazione_versioni'::regclass, "
                 "'public.documentazione_generazioni'::regclass) AND NOT tgisinternal"))}
        assert trigger == {"documentazione_versioni_nuove", "documentazione_versioni_protette",
                           "documentazione_versioni_troncamento",
                           "documentazione_generazioni_protette"}
        # reversibile: giù alla 057 di R16 (tabelle tolte, vincolo di R14) e di nuovo su
        monkeypatch.setenv("DATABASE_URL", url)
        command.downgrade(_config(), "20261110_057")
        assert "documentazione:genera" not in _vincolo(url)
        assert "pratica:riprendi" in _vincolo(url)
        assert asyncio.run(_esegui(url, "SELECT to_regclass('public.documentazione_versioni') "
                                        "AS t"))[0]["t"] is None
        assert _upgrade(url, monkeypatch) is None
        assert "documentazione:genera" in _vincolo(url)
        # revisione R17 (A5): con versioni generate il downgrade è rifiutato
        gen = asyncio.run(_esegui(url, "INSERT INTO public.documentazione_generazioni "
                                       "(scade_il) VALUES (now() + interval '5 minutes') "
                                       "RETURNING id"))[0]["id"]
        md, html = b"# Manuale\n", b"<p>Manuale</p>"
        asyncio.run(_esegui(
            url, "INSERT INTO public.documentazione_versioni (id, tipo, versione, "
                 "generazione_id, titolo, nome_file, markdown, html, impronta_contenuto, "
                 "impronta_sha256, impronta_html, sezioni, differenze, fonti, da_compilare, "
                 "da_verificare, generata_il) VALUES (gen_random_uuid(), "
                 "'MANUALE_CONSERVAZIONE', 1, $1, 'Manuale', 'manuale_conservazione', $2, $3, "
                 "$4, $5, $6, '[]', '{}', '{}', '[]', '[]', now())",
            gen, md, html, hashlib.sha256(b"c").hexdigest(), hashlib.sha256(md).hexdigest(),
            hashlib.sha256(html).hexdigest()))
        with pytest.raises(Exception, match="downgrade rifiutato"):
            command.downgrade(_config(), "20261110_057")
        assert asyncio.run(_esegui(url, "SELECT count(*) AS n FROM "
                                        "public.documentazione_versioni"))[0]["n"] == 1
    finally:
        _elimina_db(nome)
