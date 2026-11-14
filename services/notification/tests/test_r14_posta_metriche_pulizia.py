# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R14 — lavoratore della posta: metriche dal database (VD-C-170) e retention delle notifiche
personali con lease e fencing (VD-C-172). PostgreSQL vero, login non proprietario.

  - le metriche (calcolate dalle repliche API, il lavoratore resta senza HTTP) vedono coda
    ferma, messaggi non partiti, canali PEC non utilizzabili, stato del lavoratore vecchio e
    ricevute non autentiche; sono registrate nel ``/metrics`` di notification;
  - la pulizia cancella SOLO le voci oltre ``NOTIFICHE_PERSONALI_GIORNI`` (predefinito 365),
    e il database non scende mai sotto 30 giorni nemmeno chiamando la funzione con 1;
  - il login dei servizi non ha DELETE: nemmeno impostando da sé la variabile della
    funzione può cancellare (la variabile è solo difesa in profondità);
  - una replica che ha perso il lease (generazione vecchia) non cancella nulla (fencing);
  - il lavoro non è dovuto di nuovo prima dell'intervallo.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from libs.shared.parametri import svuota_cache
from tests.infra_reale import db_url, richiede_pg, sql  # noqa: F401
from tests.test_b3_api import crea_ente

pytestmark = richiede_pg

INTERVALLO = timedelta(hours=1)
DURATA = timedelta(minutes=10)


async def _voce(url: str, ente: dict, giorni_fa: int) -> int:
    riga = await sql(url, "INSERT INTO public.notifiche_personali (user_id, tenant_id, tipo, "
                          "titolo, creato_il) VALUES ($1, $2, 'credenziali.password_cambiata', "
                          "'Le tue credenziali: password cambiata', now() - make_interval("
                          "days => $3)) RETURNING id", ente["ADMIN_TENANT"], ente["tenant"],
                     giorni_fa)
    return int(riga[0]["id"])


async def _rimaste(url: str) -> set[int]:
    return {int(r["id"]) for r in await sql(url, "SELECT id FROM public.notifiche_personali")}


async def test_pulizia_oltre_i_giorni_del_parametro_e_mai_sotto_30(db_url) -> None:  # noqa: F811
    from app.application.pulizia_personali import giro

    svuota_cache()
    ente = await crea_ente(db_url)
    vecchia, media, recente = [await _voce(db_url, ente, g) for g in (400, 100, 10)]
    adesso = datetime.now(UTC)
    esito = await giro("replica-a", adesso, intervallo=INTERVALLO, durata=DURATA, lotto=1,
                       giri_massimi=10)
    assert esito == {"cancellate": 1, "giorni": 365}
    assert await _rimaste(db_url) == {media, recente}
    # non dovuto prima dell'intervallo
    assert await giro("replica-b", adesso + timedelta(minutes=5), intervallo=INTERVALLO,
                      durata=DURATA, lotto=1, giri_massimi=10) is None
    # il conservatore abbassa a 30 giorni: la media va, la recente resta
    await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, codice, valore, motivo) "
                      "VALUES ('CONSERVATORE', 'NOTIFICHE_PERSONALI_GIORNI', '30'::jsonb, "
                      "'prova della pulizia')")
    svuota_cache()
    esito = await giro("replica-b", adesso + 2 * INTERVALLO, intervallo=INTERVALLO,
                       durata=DURATA, lotto=10, giri_massimi=10)
    assert esito == {"cancellate": 1, "giorni": 30}
    assert await _rimaste(db_url) == {recente}
    # la funzione del database non scende sotto 30 giorni, qualunque cosa riceva
    assert (await sql(db_url, "SELECT public.vd_pulisci_notifiche_personali(1, 100) AS n"))[0][
        "n"] == 0
    assert await _rimaste(db_url) == {recente}
    stato = (await sql(db_url, "SELECT ultimo_esito, lease_detentore, generazione FROM "
                               "public.lavori_periodici WHERE nome = $1",
                       "notifiche_personali.pulizia"))[0]
    assert stato["lease_detentore"] is None and stato["generazione"] == 2


async def test_login_dei_servizi_senza_delete_anche_con_la_variabile(db_url) -> None:  # noqa: F811
    from libs.shared.db.ruolo_prova import url_ruolo_servizio

    ente = await crea_ente(db_url)
    vecchia = await _voce(db_url, ente, 400)
    servizio = await url_ruolo_servizio(db_url)
    conn = await asyncpg.connect(servizio.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.ambito = 'conservatore'")
            await conn.execute("SET LOCAL vd.pulizia_notifiche_personali = 'on'")
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute("DELETE FROM public.notifiche_personali WHERE id = $1", vecchia)
    finally:
        await conn.close()
    assert vecchia in await _rimaste(db_url)
    # il superutente stesso non cancella senza la funzione (trigger)
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await sql(db_url, "DELETE FROM public.notifiche_personali WHERE id = $1", vecchia)
    # seconda guardia, indipendente dalla funzione: nemmeno dentro la variabile della
    # funzione una voce recente si cancella (trigger, mai sotto 30 giorni)
    recente = await _voce(db_url, ente, 10)
    conn = await asyncpg.connect(db_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        with pytest.raises(asyncpg.exceptions.RaiseError):
            async with conn.transaction():
                await conn.execute("SET LOCAL vd.pulizia_notifiche_personali = 'on'")
                await conn.execute("DELETE FROM public.notifiche_personali WHERE id = $1",
                                   recente)
    finally:
        await conn.close()
    assert recente in await _rimaste(db_url)
    # e la funzione non è eseguibile da PUBLIC
    permesso = await sql(db_url, "SELECT has_function_privilege('public', "
                                 "'public.vd_pulisci_notifiche_personali(integer, integer)', "
                                 "'EXECUTE') AS p")
    assert permesso[0]["p"] is False


async def test_replica_senza_lease_non_cancella(db_url) -> None:  # noqa: F811
    from app.application import pulizia_personali as pp

    from libs.shared.db.ambito import ambito_conservatore

    svuota_cache()
    ente = await crea_ente(db_url)
    vecchia = await _voce(db_url, ente, 400)
    adesso = datetime.now(UTC)
    with ambito_conservatore("prova", registra=False):
        gen_a = await pp.prendi("replica-a", adesso, intervallo=INTERVALLO, durata=DURATA)
        assert gen_a == 1
        # il lease di A scade e B lo prende (nuova generazione)
        await sql(db_url, "UPDATE public.lavori_periodici SET lease_scade_il = now() - "
                          "interval '1 second'")
        gen_b = await pp.prendi("replica-b", adesso + timedelta(seconds=1),
                                intervallo=INTERVALLO, durata=DURATA)
        assert gen_b == 2
        # A, rimasta indietro, prova a cancellare con la sua generazione: rifiutata
        with pytest.raises(pp.LeasePerso):
            await pp.cancella_lotto("replica-a", gen_a, giorni=30, lotto=10)
        # anche con il suo nome ma la generazione vecchia (lease ripreso da A stessa)
        with pytest.raises(pp.LeasePerso):
            await pp.cancella_lotto("replica-b", gen_a, giorni=30, lotto=10)
        # un'altra replica con la generazione giusta ma senza il lease: rifiutata
        with pytest.raises(pp.LeasePerso):
            await pp.cancella_lotto("replica-c", gen_b, giorni=30, lotto=10)
        assert vecchia in await _rimaste(db_url)
        assert await pp.cancella_lotto("replica-b", gen_b, giorni=30, lotto=10) == 1
    assert vecchia not in await _rimaste(db_url)


async def test_metriche_della_posta_dal_database(db_url) -> None:  # noqa: F811
    from app.application import metriche_posta
    from prometheus_client import CollectorRegistry, generate_latest

    svuota_cache()
    ente = await crea_ente(db_url)
    vuote = await metriche_posta.leggi()
    assert vuote["verumdoc_posta_messaggi_in_coda"] == 0
    assert vuote["verumdoc_pec_stato_eta_secondi"] == -1
    for stato, minuti in (("IN_CODA", 45), ("NON_PARTITA", 10)):
        await sql(db_url, "INSERT INTO public.notifications (tipo, canale, destinatario, stato, "
                          "tenant_id, created_at, updated_at, payload_json) VALUES ('prova', "
                          "'EMAIL', 'referente@ente-di-prova.example', $1, $2, now() - "
                          "make_interval(mins => $3), now(), jsonb_build_object('oggetto_id', "
                          "$4::text))", stato, ente["tenant"], minuti, str(uuid.uuid4()))
    await sql(db_url, "INSERT INTO public.canali_pec (codice, tipo, nome, attivo) VALUES "
                      "('prova-pec', 'SVILUPPO', 'Canale di prova', true)")
    await sql(db_url, "INSERT INTO public.pec_canali_stato (canale_codice, problemi, "
                      "aggiornato_il) VALUES ('prova-pec', '[\"segreti assenti\"]'::jsonb, "
                      "now() - interval '20 minutes')")
    await sql(db_url, "INSERT INTO public.pec_ricevute (canale_codice, imap_uidvalidity, "
                      "imap_uid, esito, motivo) VALUES ('prova-pec', 1, 7, 'NON_AUTENTICA', "
                      "'firma non valida')")
    metriche_posta.registra(registro := CollectorRegistry())
    valori = await metriche_posta.aggiorna()
    assert valori["verumdoc_posta_messaggi_in_coda"] == 1
    assert valori["verumdoc_posta_coda_eta_secondi"] >= 45 * 60 - 5
    assert valori["verumdoc_posta_messaggi_non_partiti_24h"] == 1
    assert valori["verumdoc_pec_canali_attivi"] == 1
    assert valori["verumdoc_pec_canali_non_utilizzabili"] == 1
    assert valori["verumdoc_pec_stato_eta_secondi"] >= 20 * 60 - 5
    assert valori["verumdoc_pec_ricevute_non_autentiche_24h"] == 1
    testo = generate_latest(registro).decode()
    assert "verumdoc_pec_canali_non_utilizzabili 1.0" in testo


def test_metriche_nel_metrics_del_servizio() -> None:
    from app.application.metriche_posta import METRICHE
    from app.main import _metriche_http

    nomi = {m.name for m in _metriche_http.registro.collect()}
    assert set(METRICHE) <= nomi
