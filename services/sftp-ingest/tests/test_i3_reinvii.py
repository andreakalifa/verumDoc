# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R03 i3 — reinvii sul canale SFTP (richieste 1 e 2 del rapporto f1), su
PostgreSQL reale con il gateway finto.

1. Un esito «VERSATA» precedente non si riusa senza chiedere al gateway lo stato
   attuale del PdV: se il PdV è stato poi rifiutato (ERRORE) o annullato, la
   cartella reinviata uguale è SCARTATA con VRS-127, come risponderebbe il
   gateway; se il PdV è valido l'esito riporta lo stato attuale.
2. La chiave della sessione di caricamento comprende il contenuto (impronta
   dell'indice): una cartella corretta ripresa nella stessa lavorazione apre
   sessioni nuove invece di ricevere 409 UPL-007.
"""
from __future__ import annotations

import hashlib
import json

from app.domain.protocollo import chiave_caricamento
from app.infrastructure import archivio, repository

from tests.conftest import crea_account
from tests.infra_reale import richiede_pg, sql
from tests.test_lavoratore import PDF, _deposita, _esito, _indice, _righe

pytestmark = richiede_pg


async def test_reinvio_dopo_rifiuto_del_pdv_non_risulta_versato(
        db_url, radice, lavoratore, gateway_finto) -> None:
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-RIF", file, _indice(file))
    await lavoratore.giro()
    primo = _esito(radice, account, "PDV-RIF")
    assert primo["stato"] == "VERSATO"
    # la verifica asincrona rifiuta il PdV dopo la risposta 202
    gateway_finto.stati_pdv[primo["pdv_id"]] = "ERRORE"

    await _deposita(radice, account, "PDV-RIF", file, _indice(file))
    assert await lavoratore.giro() == 1
    secondo = _esito(radice, account, "PDV-RIF")
    assert secondo["stato"] == "SCARTATO"
    (problema,) = secondo["errori"]
    assert problema["codice"] == "VRS-127"
    assert problema["dettagli"]["pdv_id_esistente"] == primo["pdv_id"]
    assert problema["dettagli"]["stato_esistente"] == "ERRORE"
    # il gateway è stato interrogato; nessun nuovo versamento
    assert gateway_finto.letture_stato_pdv == [primo["pdv_id"]]
    assert len(gateway_finto.chiamate_versamento) == 1
    righe = await _righe(db_url, account)
    assert [r["stato"] for r in righe] == ["VERSATA", "SCARTATA"]


async def test_reinvio_pdv_valido_riusa_l_esito_con_lo_stato_attuale(
        db_url, radice, lavoratore, gateway_finto) -> None:
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-OK", file, _indice(file))
    await lavoratore.giro()
    primo = _esito(radice, account, "PDV-OK")
    gateway_finto.stati_pdv[primo["pdv_id"]] = "CONSERVATO"

    await _deposita(radice, account, "PDV-OK", file, _indice(file))
    await lavoratore.giro()
    secondo = _esito(radice, account, "PDV-OK")
    assert secondo["stato"] == "VERSATO" and secondo["reinvio"] is True
    assert secondo["pdv_id"] == primo["pdv_id"]
    assert secondo["stato_pdv"] == "CONSERVATO"
    assert gateway_finto.letture_stato_pdv == [primo["pdv_id"]]
    assert len(gateway_finto.chiamate_versamento) == 1


async def test_reinvio_corretto_dopo_rifiuto_crea_un_pdv_nuovo(
        db_url, radice, lavoratore, gateway_finto) -> None:
    """Dopo il rifiuto il cliente corregge il file (indice con la nuova impronta):
    versamento nuovo con una chiave nuova, nessun riuso dell'esito vecchio."""
    account = await crea_account(db_url)
    file = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-COR", file, _indice(file))
    await lavoratore.giro()
    primo = _esito(radice, account, "PDV-COR")
    gateway_finto.stati_pdv[primo["pdv_id"]] = "ERRORE"

    corretto = {"a.pdf": PDF + b"corretto"}
    await _deposita(radice, account, "PDV-COR", corretto, _indice(corretto))
    await lavoratore.giro()
    secondo = _esito(radice, account, "PDV-COR")
    assert secondo["stato"] == "VERSATO" and secondo["reinvio"] is False
    assert secondo["pdv_id"] != primo["pdv_id"]
    assert len(gateway_finto.chiamate_versamento) == 2


async def test_cartella_corretta_ripresa_nella_stessa_lavorazione_nuove_sessioni(
        db_url, radice, lavoratore, gateway_finto, client_gateway) -> None:
    """Lavorazione ripresa (stessa riga, lease scaduto) con il contenuto cambiato
    rispetto al tentativo precedente: le sessioni ripartono (generazione 1) e la
    chiave, che comprende l'impronta dell'indice, è nuova. Prima della correzione
    la chiave era uguale a quella della sessione vecchia: 409 UPL-007 e scarto."""
    account = await crea_account(db_url)
    vecchio = {"a.pdf": PDF}
    await _deposita(radice, account, "PDV-CAMBIO", vecchio, _indice(vecchio))
    lav = await repository.prendi_in_carico(account.tenant_id, account.id, "PDV-CAMBIO",
                                            "morta", 60)
    assert lav is not None
    area = radice / str(account.id)
    archivio.assicura_area(area)
    (area / "in" / "PDV-CAMBIO").rename(area / "lavorazione" / "PDV-CAMBIO")
    impronta_vecchia = hashlib.sha256(json.dumps(_indice(vecchio)).encode()).hexdigest()
    token = lavoratore.emettitore.token(tenant_id=account.tenant_id,
                                        user_id=account.responsabile_id, ruolo="OPERATORE")
    vecchia = await client_gateway.apri_caricamento(
        token, nome_file="a.pdf", dimensione=len(PDF),
        impronta={"algoritmo": "SHA-256", "valore": hashlib.sha256(PDF).hexdigest()},
        chiave=chiave_caricamento(str(lav.id), "a.pdf", impronta_vecchia, 1))
    await repository.aggiorna_lavoro(
        lav.id, "morta", impronta_indice=impronta_vecchia, chiave_idempotenza=None,
        caricamenti={"a.pdf": {"id": vecchia["caricamento_id"], "gen": 1,
                               "dimensione_parte": 1000, "numero_parti": 6}})
    # il contenuto della cartella cambia (file corretto, indice con la nuova impronta)
    corretto = {"a.pdf": PDF + b"corretto"}
    cartella = area / "lavorazione" / "PDV-CAMBIO"
    (cartella / "a.pdf").write_bytes(corretto["a.pdf"])
    (cartella / "indice.json").write_bytes(json.dumps(_indice(corretto)).encode())
    await sql(db_url, "UPDATE public.sftp_lavorazioni SET lease_scade_il = now() - "
                      "interval '1 second' WHERE id = $1", lav.id)

    assert await lavoratore.giro() == 1
    esito = _esito(radice, account, "PDV-CAMBIO")
    assert esito["stato"] == "VERSATO", esito["errori"]
    (chiamata,) = gateway_finto.chiamate_versamento
    nuova = chiamata["file"]["a.pdf"]
    assert nuova != vecchia["caricamento_id"]
    s = gateway_finto.sessioni[nuova]
    assert b"".join(s.parti[i] for i in sorted(s.parti)) == corretto["a.pdf"]
    (riga,) = await _righe(db_url, account)
    assert riga["id"] == lav.id and riga["stato"] == "VERSATA"
