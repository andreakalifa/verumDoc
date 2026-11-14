# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R13a (5) — migrazione 20261107_054: diritti e immutabilità di
``firme_documenti`` su PostgreSQL vero.

  - aip-builder e dashboard-api (gruppo ``verumdoc_firme_documenti``) accodano
    soltanto: SELECT e INSERT delle sole colonne d'origine, mai UPDATE; il solo
    login della hashchain
    (``verumdoc_firme_documenti_sigillo``) aggiorna; nessun DELETE a nessuno;
  - trigger ``firme_documenti_protette``: origine e impronta non cambiano mai; una
    riga FIRMATO resta FIRMATO e cambia solo nelle colonne della rimarcatura con
    ``rinnovi`` crescente e la busta precedente in ``storico_buste``; DELETE vietata
    anche al proprietario;
  - seconda revisione (1): trigger ``firme_documenti_nuove``, chi non è nel gruppo
    della hashchain inserisce solo righe DA_FIRMARE senza dati del sigillo (anche
    con un INSERT su tutta la tabella concesso per errore).
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from libs.shared.db.ruolo_prova import assicura_ruolo_servizio, url_con_utente
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401

pytestmark = [richiede_pg, pytest.mark.asyncio]

#: login di prova SOLO del gruppo che accoda (come aip-builder e dashboard-api)
LOGIN_ACCODA = "vd_prova_firme_accoda"
#: login con INSERT su TUTTA la tabella (configurazione sbagliata): il trigger basta
LOGIN_INSERISCE = "vd_prova_firme_inserisce"

COLONNE_ORIGINE = {"tipo", "tenant_id", "aip_id", "nome", "origine_bucket", "origine_chiave",
                   "origine_version_id", "contenuto", "impronta_sha256"}


def _dsn(url: str, login: str) -> str:
    return url_con_utente(url, login).replace("postgresql+asyncpg://", "postgresql://", 1)


async def _documento(url: str, *, firmato: bool = False) -> uuid.UUID:
    impronta = uuid.uuid4().hex * 2
    riga = await sql(url, "INSERT INTO public.firme_documenti (tipo, nome, contenuto, "
                          "impronta_sha256) VALUES ('MANUALE', 'manuale', $1, $2) RETURNING id",
                     b"manuale", impronta)
    ident = riga[0]["id"]
    if firmato:
        await sql(url, "UPDATE public.firme_documenti SET stato = 'FIRMATO', busta_sha256 = $1, "
                       "busta_chiave = 'k0', firmato_il = now(), livello = 'B-LT', "
                       "algoritmo = 'sha256' WHERE id = $2", "aa" * 32, ident)
    return ident


async def test_diritti_accoda_senza_update_sigillo_con_update(db_url):  # noqa: F811
    diritti = {r["grantee"]: set(r["diritti"]) for r in await sql(
        db_url, "SELECT grantee, array_agg(privilege_type::text) AS diritti FROM "
                "information_schema.role_table_grants WHERE table_schema = 'public' AND "
                "table_name = 'firme_documenti' AND grantee LIKE 'verumdoc%' GROUP BY grantee")}
    assert diritti["verumdoc_firme_documenti"] == {"SELECT"}  # INSERT solo per colonna
    colonne = await sql(db_url, "SELECT column_name FROM information_schema.column_privileges "
                                "WHERE table_name = 'firme_documenti' AND privilege_type = "
                                "'INSERT' AND grantee = 'verumdoc_firme_documenti'")
    assert {r["column_name"] for r in colonne} == COLONNE_ORIGINE
    assert diritti["verumdoc_firme_documenti_sigillo"] == {"SELECT", "INSERT", "UPDATE"}
    assert "verumdoc_applicazione" not in diritti
    assert not any("DELETE" in d for d in diritti.values())
    transizione = await sql(db_url, "SELECT grantee FROM information_schema.role_table_grants "
                                    "WHERE table_name = 'firma_transizione_r13' AND "
                                    "grantee LIKE 'verumdoc%'")
    assert {r["grantee"] for r in transizione} == {"verumdoc_firme_documenti_sigillo"}


async def test_login_che_accoda_non_aggiorna(db_url):  # noqa: F811
    await assicura_ruolo_servizio(db_url, LOGIN_ACCODA, ("verumdoc_firme_documenti",))
    ident = await _documento(db_url)
    dsn = url_con_utente(db_url, LOGIN_ACCODA).replace("postgresql+asyncpg://",
                                                        "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SET app.ambito = 'conservatore'")
        assert await conn.fetchval("SELECT count(*) FROM public.firme_documenti "
                                   "WHERE id = $1", ident) == 1
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("UPDATE public.firme_documenti SET stato = 'FIRMATO' "
                               "WHERE id = $1", ident)
    finally:
        await conn.close()


async def test_origine_e_impronta_immutabili(db_url):  # noqa: F811
    ident = await _documento(db_url)
    with pytest.raises(asyncpg.exceptions.RaiseError, match="origine e impronta"):
        await sql(db_url, "UPDATE public.firme_documenti SET impronta_sha256 = $1 "
                          "WHERE id = $2", "bb" * 32, ident)
    with pytest.raises(asyncpg.exceptions.RaiseError, match="origine e impronta"):
        await sql(db_url, "UPDATE public.firme_documenti SET contenuto = $1 WHERE id = $2",
                  b"altro", ident)
    with pytest.raises(asyncpg.exceptions.RaiseError, match="DELETE vietata"):
        await sql(db_url, "DELETE FROM public.firme_documenti WHERE id = $1", ident)


async def test_riga_firmata_cambia_solo_con_la_rimarcatura(db_url):  # noqa: F811
    ident = await _documento(db_url, firmato=True)
    for colonna, valore in (("stato", "'DA_FIRMARE'"), ("fornitore_firma", "'altro'"),
                            ("certificato_sha256", "repeat('c', 64)"),
                            ("firmato_il", "now() - interval '1 day'")):
        with pytest.raises(asyncpg.exceptions.RaiseError, match="già registrato"):
            await sql(db_url, f"UPDATE public.firme_documenti SET {colonna} = {valore} "  # noqa: S608
                              "WHERE id = $1", ident)
    # nuova busta senza rinnovi crescente: rifiutata
    with pytest.raises(asyncpg.exceptions.RaiseError, match="rinnovi crescente"):
        await sql(db_url, "UPDATE public.firme_documenti SET busta_sha256 = $1, "
                          "busta_chiave = 'k1', storico_buste = storico_buste || "
                          "jsonb_build_array(jsonb_build_object('busta_chiave', busta_chiave, "
                          "'busta_sha256', busta_sha256)) WHERE id = $2", "dd" * 32, ident)
    # nuova busta senza la precedente nello storico: rifiutata
    with pytest.raises(asyncpg.exceptions.RaiseError, match="storico_buste"):
        await sql(db_url, "UPDATE public.firme_documenti SET busta_sha256 = $1, "
                          "busta_chiave = 'k1', rinnovi = 1 WHERE id = $2", "dd" * 32, ident)
    # storico con una busta diversa dalla precedente: rifiutato
    with pytest.raises(asyncpg.exceptions.RaiseError, match="storico delle buste"):
        await sql(db_url, "UPDATE public.firme_documenti SET busta_sha256 = $1, "
                          "busta_chiave = 'k1', rinnovi = 1, storico_buste = storico_buste || "
                          "jsonb_build_array(jsonb_build_object('busta_chiave', 'altra', "
                          "'busta_sha256', repeat('e', 64))) WHERE id = $2", "dd" * 32, ident)
    # rimarcatura regolare (come la hashchain): ammessa
    await sql(db_url, "UPDATE public.firme_documenti SET busta_sha256 = $1, busta_chiave = 'k1', "
                      "livello = 'B-LTA', rinnovi = 1, rinnovato_il = now(), "
                      "rinnovo_motivo = 'prova', storico_buste = storico_buste || "
                      "jsonb_build_array(jsonb_build_object('busta_chiave', busta_chiave, "
                      "'busta_sha256', busta_sha256)) WHERE id = $2", "dd" * 32, ident)
    # lo storico non si riscrive
    with pytest.raises(asyncpg.exceptions.RaiseError, match="storico delle buste"):
        await sql(db_url, "UPDATE public.firme_documenti SET storico_buste = '[]'::jsonb "
                          "WHERE id = $1", ident)
    with pytest.raises(asyncpg.exceptions.RaiseError, match="non decrescono"):
        await sql(db_url, "UPDATE public.firme_documenti SET rinnovi = 0 WHERE id = $1", ident)
    riga = (await sql(db_url, "SELECT stato, rinnovi, livello FROM public.firme_documenti "
                              "WHERE id = $1", ident))[0]
    assert (riga["stato"], riga["rinnovi"], riga["livello"]) == ("FIRMATO", 1, "B-LTA")


async def _inserisci_firmato(conn) -> None:  # type: ignore[no-untyped-def]
    await conn.execute(
        "INSERT INTO public.firme_documenti (tipo, nome, contenuto, impronta_sha256, stato, "
        "busta_sha256, busta_chiave, firmato_il, qualificata) VALUES ('MANUALE', 'falso', "
        "$1, $2, 'FIRMATO', repeat('f', 64), 'inventata', now(), true)",
        b"falso", uuid.uuid4().hex * 2)


async def test_chi_accoda_non_inserisce_righe_gia_firmate(db_url):  # noqa: F811
    """Seconda revisione (1): riga FIRMATO con busta inventata e qualificata=true
    inserita da aip-builder o dashboard-api: rifiutata (diritti di colonna)."""
    await assicura_ruolo_servizio(db_url, LOGIN_ACCODA, ("verumdoc_firme_documenti",))
    conn = await asyncpg.connect(_dsn(db_url, LOGIN_ACCODA))
    try:
        await conn.execute("SET app.ambito = 'conservatore'")
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await _inserisci_firmato(conn)
        # l'accodamento normale funziona
        await conn.execute("INSERT INTO public.firme_documenti (tipo, nome, contenuto, "
                           "impronta_sha256) VALUES ('MANUALE', 'm', $1, $2)",
                           b"m", uuid.uuid4().hex * 2)
    finally:
        await conn.close()


async def test_trigger_rifiuta_righe_firmate_anche_con_insert_su_tutta_la_tabella(db_url):  # noqa: F811
    """Seconda revisione (1): anche con un INSERT su tutta la tabella concesso per
    errore, chi non è nel gruppo della hashchain inserisce solo righe DA_FIRMARE."""
    await assicura_ruolo_servizio(db_url, LOGIN_INSERISCE, ("verumdoc_firme_documenti",))
    await sql(db_url, f"GRANT INSERT ON public.firme_documenti TO {LOGIN_INSERISCE}")
    conn = await asyncpg.connect(_dsn(db_url, LOGIN_INSERISCE))
    try:
        await conn.execute("SET app.ambito = 'conservatore'")
        with pytest.raises(asyncpg.exceptions.RaiseError, match="senza dati del sigillo"):
            await _inserisci_firmato(conn)
        with pytest.raises(asyncpg.exceptions.RaiseError, match="senza dati del sigillo"):
            await conn.execute("INSERT INTO public.firme_documenti (tipo, nome, contenuto, "
                               "impronta_sha256, qualificata) VALUES ('PIANO', 'p', $1, $2, "
                               "true)", b"p", uuid.uuid4().hex * 2)
    finally:
        await conn.close()
        await sql(db_url, f"REVOKE INSERT ON public.firme_documenti FROM {LOGIN_INSERISCE}")
