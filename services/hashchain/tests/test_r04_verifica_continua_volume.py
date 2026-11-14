# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Prova di volume della programmazione della verifica continua (R04, r04-verifiche):
100.000 PdA su PostgreSQL vero, due enti. Misura (stampa con ``-s``) e controlla
con soglie larghe:
  - semina iniziale di 100.000 PdA e giro successivo senza novità;
  - passaggio completo della riconciliazione;
  - presa in carico di lotti fra 100.000 righe in scadenza (nessun doppione);
  - registrazione di 1.000 esiti (registro, programmazione, copertura);
  - preparazione di una verifica d'ente (50.000 PdA);
  - riepilogo (copertura, anomalie aperte) e una pagina del registro.
Lo storage non c'entra (si misura la parte di database): la lettura dei byte è
limitata dai budget e dalla banda, non dalla programmazione.
"""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.verifica_continua import repository
from app.verifica_continua.config import ImpostazioniVerifica
from app.verifica_continua.verificatore import EsitoOggetto, EsitoPda

from tests.infra_reale import (
    crea_tenant_e_classe,
    db_url,  # noqa: F401 — fixture
    richiede_pg,
    sql,
)

pytestmark = [pytest.mark.integration, richiede_pg]

N = 100_000
TEMPI: dict[str, float] = {}


def _misura(nome: str, inizio: float) -> float:
    durata = time.perf_counter() - inizio
    TEMPI[nome] = durata
    return durata


async def test_volume_programmazione_100000_pda(db_url):  # noqa: F811
    t1, classe = await crea_tenant_e_classe(db_url)
    t2, _ = await crea_tenant_e_classe(db_url)
    for tenant in (t1, t2):
        await sql(db_url,
                  "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id) "
                  "SELECT gen_random_uuid(), 'volume', 'CONSERVATO', $1, gen_random_uuid(), $2 "
                  "FROM generate_series(1, $3)", classe, tenant, N // 2)
    await sql(db_url,
              "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
              "hash_sha256, data_versamento) SELECT gen_random_uuid(), p.id, p.tenant_id, "
              "'urn:volume:' || p.id, 'CONSERVATO', 'k', 'b', repeat('0', 64), "
              "now() - random() * interval '30 days' FROM public.pdv p WHERE p.oggetto = 'volume'")
    await sql(db_url, "ANALYZE public.aip")
    cfg = ImpostazioniVerifica(semina_lotto=5000, semina_lotti_per_giro=50,
                               riconciliazione_lotti_per_giro=50, lotto=100,
                               preparazione_lotto=5000, prima_entro_giorni=30)
    adesso = datetime.now(UTC)

    inizio = time.perf_counter()
    assert await repository.semina(cfg, adesso) == N
    assert _misura("semina iniziale 100.000 PdA", inizio) < 120
    inizio = time.perf_counter()
    assert await repository.semina(cfg, adesso) == 0
    assert _misura("semina successiva (sovrapposizione 2 h, nessun PdA nuovo)", inizio) < 5
    inizio = time.perf_counter()
    assert await repository.riconcilia(cfg, adesso) == 0
    assert _misura("riconciliazione: passaggio completo di 100.000 PdA", inizio) < 120
    await sql(db_url, "ANALYZE public.verifiche_programmazione")

    # Rotazione uniforme: nessun giorno della finestra con più del doppio della media
    per_giorno = await sql(db_url, "SELECT date_trunc('day', prossima_verifica - $1) AS g, "
                                   "count(*) AS n FROM public.verifiche_programmazione GROUP BY 1",
                           adesso)
    assert len(per_giorno) in (30, 31) and max(r["n"] for r in per_giorno) < 2 * N / 30

    # Presa in carico fra 100.000 righe in scadenza
    futuro = adesso + timedelta(days=31)
    presi: list[uuid.UUID] = []
    durate = []
    for i in range(10):
        inizio = time.perf_counter()
        lotto = await repository.prendi_lotto(cfg, futuro, f"replica-{i % 2}")
        durate.append(time.perf_counter() - inizio)
        presi += [p.aip_id for p in lotto]
    TEMPI["presa in carico di un lotto da 100 (media di 10)"] = sum(durate) / len(durate)
    assert len(presi) == len(set(presi)) == 1000 and max(durate) < 2

    # Registrazione di 1.000 esiti (5 oggetti ciascuno)
    leases = {r["aip_id"]: r["lease_id"] for r in await sql(
        db_url, "SELECT aip_id, lease_id FROM public.verifiche_programmazione "
                "WHERE aip_id = ANY($1::uuid[])", presi)}
    tutti = []
    for gruppo in {leases[a] for a in presi}:
        tutti += await repository.dati_pda([a for a in presi if leases[a] == gruppo], gruppo)
    assert len(tutti) == 1000
    inizio = time.perf_counter()
    for pda in tutti:
        oggetti = [EsitoOggetto(c, f"k/{c}", "OK", impronta_attesa="0" * 64,
                                impronta_trovata="0" * 64) for c in ("PDA", "IPDA", "RDV")]
        oggetti += [EsitoOggetto("FILE", f"k/f{j}", "OK", documento_id=uuid.uuid4(),
                                 impronta_attesa="0" * 64, impronta_trovata="0" * 64)
                    for j in range(2)]
        conclusione = await repository.registra_esito(EsitoPda(pda, oggetti), futuro, cfg, "r")
        assert conclusione.registrato and conclusione.concluso
    durata = _misura("registrazione di 1.000 esiti (5.000 righe di registro)", inizio)
    assert durata < 60

    # Verifica d'ente su 50.000 PdA: preparazione a lotti
    stato = await repository.crea_richiesta_ente(t1, richiesta_da="u", ruolo="RSC_CONSERVATORE",
                                                 adesso=futuro)
    inizio = time.perf_counter()
    marcati = 0
    while True:
        n = await repository.prepara_richieste(cfg, futuro)
        marcati += n
        richiesta = await repository.leggi_richiesta(uuid.UUID(stato["richiesta_id"]))
        if richiesta["preparazione_conclusa"]:
            break
    _misura("preparazione verifica d'ente (50.000 PdA)", inizio)
    assert richiesta["oggetti_totali"] + richiesta["gia_in_verifica"] == N // 2

    # Riepilogo e registro (letture delle API)
    inizio = time.perf_counter()
    per_giorno_ente, _ = await repository.copertura_per_giorno(t1, futuro)
    anomalie = await repository.anomalie_aperte(t1)
    _misura("riepilogo di un ente (copertura + anomalie aperte)", inizio)
    assert sum(per_giorno_ente.values()) == N // 2 and anomalie["numero"] == 0
    inizio = time.perf_counter()
    await repository.aggiorna_riepilogo_globale(futuro)
    _misura("riepilogo globale (lavoratore)", inizio)
    inizio = time.perf_counter()
    pagina = await repository.elenco_registro(tenant=t1, aip_id=None, esito=None, cursore=None,
                                              limite=100)
    _misura("pagina da 100 del registro di un ente", inizio)
    assert len(pagina["voci"]) == 100
    inizio = time.perf_counter()
    assert await repository.ritardo_rotazione(futuro) > 0
    _misura("ritardo della rotazione (metrica)", inizio)

    print("\nTempi della prova di volume (100.000 PdA):")  # noqa: T201 — tempi per il rapporto (-s)
    for nome, secondi in TEMPI.items():
        print(f"  {nome}: {secondi:.3f} s")  # noqa: T201
