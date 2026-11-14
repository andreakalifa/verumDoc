# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R06 — V2 (VD-C-119): prima di scrivere il FATTO di annullamento o
rettifica il lavoratore verifica la catena della richiesta.

Prima della correzione il lavoratore scriveva il fatto per QUALUNQUE riga
``pratiche`` di annullamento (``lavoro = REGISTRO``) o rettifica (``VERSA``):
la riga la scrive il login dell'API esposta, quindi un'API compromessa poteva
far produrre al lavoratore stesso il fatto «scrivibile solo dal lavoratore» e
far accettare la foglia dal riscontro della hashchain, anche sul PdA di un
altro ente. Ora: richiedente presente, attivo e dell'ente, ruolo ammesso,
cronologia dalla richiesta allo stato attuale, voce d'audit della richiesta,
PdV e PdA della pratica; altrimenti pratica BLOCCATA (``LC-409-AUTORIZZAZIONI``,
allarme), nessun atto, fatto, versamento né foglia.

Il registro di questi test applica il RISCONTRO VERO della hashchain
(``services/hashchain/app/registro/interne.py``) sul database prima di
accettare la foglia: il percorso è lavoratore → fatto → riscontro, senza foglie
inviate a mano.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from libs.shared.db.ruolo_prova import url_ruolo_servizio
from tests.infra_reale import richiede_pg, sql
from tests.supporto import (
    PASSATO,
    RegistroFinto,
    bucket_worm,
    client_app,
    crea_classe,
    crea_ente,
    crea_pda,
    crea_pdv,
    crea_utente,
    dipendenze,
    intestazioni,
    lavora,
)

pytestmark = [pytest.mark.integration, richiede_pg]

_INTERNE = Path(__file__).resolve().parents[2] / "hashchain" / "app" / "registro" / "interne.py"


def _riscontro_hashchain() -> Any:
    """Il modulo del riscontro della hashchain (dipende solo da libs e sqlalchemy)."""
    spec = importlib.util.spec_from_file_location("hashchain_registro_interne_r06", _INTERNE)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo  # (le dataclass del modulo lo cercano qui)
    spec.loader.exec_module(modulo)
    return modulo


class RegistroRiscontrato(RegistroFinto):
    """Registro finto che accetta la foglia solo se il riscontro della hashchain
    trova il fatto nel database (come ``POST …/interno/foglie``)."""

    rifiuti: list[str]

    def __init__(self) -> None:
        super().__init__()
        self.rifiuti = []
        self._interne = _riscontro_hashchain()

    async def aggiungi_foglia(self, *, tipo: str, pda: str, riferimento: dict[str, Any],
                              impronta_documento_pratica: str) -> str:
        from app.infrastructure.registro import ErroreRegistro
        from sqlalchemy import text

        from libs.shared.db import get_async_session
        from libs.shared.db.ambito import ambito_conservatore

        with ambito_conservatore("test: riscontro della hashchain", registra=False):
            async with get_async_session() as session:
                aip = (await session.execute(text("SELECT id FROM public.aip WHERE urn = :u"),
                                             {"u": pda})).scalar()
                try:
                    await self._interne.riscontro_ciclo_vita(
                        session, tipo=tipo, aip_id=aip, riferimento=riferimento,
                        impronta=impronta_documento_pratica)
                except self._interne.RiscontroMancante as exc:
                    self.rifiuti.append(str(exc))
                    raise ErroreRegistro(f"riscontro mancante: {exc}", ritentabile=False,
                                         codice="CAT-409-RISCONTRO") from None
        return await super().aggiungi_foglia(
            tipo=tipo, pda=pda, riferimento=riferimento,
            impronta_documento_pratica=impronta_documento_pratica)


def _dip(url: str) -> Any:
    dip = dipendenze(url)
    dip.registro = RegistroRiscontrato()
    return dip


async def _come_api(url: str, istruzione: str, *argomenti: Any) -> None:
    """Il login dell'API di ciclo-vita (solo ``verumdoc_applicazione``)."""
    dsn = (await url_ruolo_servizio(url)).replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.ambito', 'conservatore', true)")
            await conn.execute(istruzione, *argomenti)
    finally:
        await conn.close()


async def _colonne(url: str) -> list[str]:
    righe = await sql(url, "SELECT column_name FROM information_schema.columns WHERE "
                           "table_schema = 'public' AND table_name = 'pratiche' "
                           "ORDER BY ordinal_position")
    return [r["column_name"] for r in righe]


async def _fabbrica(url: str, modello: str, sostituzioni: dict[str, str], *argomenti: Any
                    ) -> None:
    """L'API compromessa copia una pratica vera cambiandone le colonne indicate."""
    colonne = await _colonne(url)
    scelte = ", ".join(sostituzioni.get(c, c) for c in colonne)
    await _come_api(url, f"INSERT INTO public.pratiche ({', '.join(colonne)}) "  # noqa: S608
                         f"SELECT {scelte} FROM public.pratiche WHERE id = $1::uuid",
                    modello, *argomenti)


async def _bloccata(url: str, pratica: Any) -> dict[str, Any]:
    riga = (await sql(url, "SELECT lavoro, ultimo_errore FROM public.pratiche WHERE id = "
                           "$1::uuid", str(pratica)))[0]
    assert riga["lavoro"] is None
    errore = json.loads(riga["ultimo_errore"]) if isinstance(riga["ultimo_errore"], str) \
        else riga["ultimo_errore"]
    assert errore["codice"] == "LC-409-AUTORIZZAZIONI", errore
    return errore


async def _nessun_fatto(url: str, pratica: Any) -> None:
    assert await sql(url, "SELECT 1 FROM public.esiti_esecuzione WHERE pratica_id = $1::uuid",
                     str(pratica)) == []


async def _ambiente_con_vittima(url: str, s3: str) -> dict[str, Any]:
    tenant = await crea_ente(url)
    admin = await crea_utente(url, tenant, "ADMIN_TENANT")
    classe = await crea_classe(url, tenant)
    pdv = await crea_pdv(url, tenant, classe, admin)
    aip, urn = await crea_pda(url, tenant, pdv, scadenza=PASSATO)
    vittima = await crea_ente(url)
    admin_v = await crea_utente(url, vittima, "ADMIN_TENANT")
    classe_v = await crea_classe(url, vittima)
    pdv_v = await crea_pdv(url, vittima, classe_v, admin_v)
    aip_v, urn_v = await crea_pda(url, vittima, pdv_v, scadenza=PASSATO)
    bucket_worm(s3)
    return {"tenant": tenant, "admin": admin, "pdv": pdv, "aip": aip, "urn": urn,
            "vittima": vittima, "admin_v": admin_v, "pdv_v": pdv_v, "aip_v": aip_v,
            "urn_v": urn_v}


async def test_annullamento_vero_passa_lavoratore_e_riscontro(db_url, s3_ciclo) -> None:
    a = await _ambiente_con_vittima(db_url, s3_ciclo)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"})
        assert r.status_code == 201, r.text
        pratica = r.json()["id"]
    dip = _dip(db_url)
    await lavora(dip)
    assert dip.registro.rifiuti == []
    assert ("annullamento", a["urn"], pratica) in dip.registro.foglie
    fatti = await sql(db_url, "SELECT esito FROM public.esiti_esecuzione WHERE pratica_id = "
                              "$1::uuid ORDER BY scritto_il, id", pratica)
    # revisione R09 (Z3): prima il fatto dell'annullamento applicato dal lavoratore
    assert [f["esito"] for f in fatti] == ["AUTORIZZAZIONI_VERIFICATE", "ATTO_ARCHIVIATO"]


@pytest.mark.parametrize("richiedente", ["nessuno", "admin_della_vittima"])
async def test_annullamento_fabbricato_dall_api_bloccato(db_url, s3_ciclo, richiedente) -> None:
    """La prova del revisore: pratica inserita dal login dell'API sul PdA di un
    ALTRO ente, PdV portato ad ANNULLATO; anche indicando come richiedente un
    utente vero dell'ente vittima manca la catena (cronologia, audit)."""
    from app.application import metriche

    a = await _ambiente_con_vittima(db_url, s3_ciclo)
    async with client_app() as c:  # pratica vera: solo il modello della riga
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"pdv_id": str(a["pdv"]), "motivo": "Pratica modello di prova"})
        assert r.status_code == 201, r.text
        modello = r.json()["id"]
    dip = _dip(db_url)
    await lavora(dip)
    falsa = uuid.uuid4()
    creata_da = "NULL" if richiedente == "nessuno" else f"'{a['admin_v']}'::uuid"
    await _fabbrica(db_url, modello, {
        "id": "$2::uuid", "tenant_id": "$3::uuid", "pdv_id": "$4::uuid",
        "dati": "jsonb_build_object('urn', $5::text, 'aip_id', $6::text, "
                "'stato_precedente', 'CONSERVATO')",
        "lavoro": "'REGISTRO'", "lease_detentore": "NULL", "lease_scade_il": "NULL",
        "tentativi": "0", "ultimo_errore": "NULL", "idempotency_key": "NULL",
        "creata_da": creata_da},
        str(falsa), str(a["vittima"]), str(a["pdv_v"]), a["urn_v"], str(a["aip_v"]))
    await _come_api(db_url, "UPDATE public.pdv SET stato = 'ANNULLATO', metadata_json = "
                            "COALESCE(metadata_json, '{}'::jsonb) || jsonb_build_object("
                            "'annullamento', jsonb_build_object('pratica_id', $2::text)) "
                            "WHERE id = $1", a["pdv_v"], str(falsa))
    await lavora(dip)
    await _nessun_fatto(db_url, falsa)
    assert not [k for k in dip.registro.foglie if k[2] == str(falsa)]
    errore = await _bloccata(db_url, falsa)
    testo = " ".join(errore["difetti"])
    assert "CRONOLOGIA_ASSENTE" in testo and "RICHIESTA_SENZA_AUDIT" in testo
    if richiedente == "nessuno":
        assert "RICHIEDENTE_ASSENTE" in testo
    # nessun atto archiviato per la pratica fabbricata
    assert await sql(db_url, "SELECT 1 FROM public.pratiche_allegati WHERE pratica_id = "
                             "$1::uuid", str(falsa)) == []
    audit = await sql(db_url, "SELECT 1 FROM public.audit_global WHERE evento = "
                              "'ciclo_vita.annullamento.autorizzazioni_non_verificate' "
                              "AND dettagli ->> 'pratica_id' = $1", str(falsa))
    assert audit
    assert (await metriche.aggiorna())["puntuali"] >= 1
    # il riscontro della hashchain resta comunque chiuso (nessun fatto)
    with pytest.raises(Exception, match="riscontro mancante"):
        await dip.registro.aggiungi_foglia(
            tipo="annullamento", pda=a["urn_v"], riferimento={"pratica": str(falsa),
                                                             "pdv": str(a["pdv_v"])},
            impronta_documento_pratica="ab" * 32)


async def test_rettifica_fabbricata_dall_api_bloccata_senza_versamento(db_url,
                                                                      s3_ciclo) -> None:
    """Rettifica inserita dal login dell'API con ``creata_da`` scelto (un utente
    vero dell'ente vittima): nessun versamento per suo conto, nessun fatto."""
    a = await _ambiente_con_vittima(db_url, s3_ciclo)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica",
                         headers=intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"]),
                         json={"aip_id": str(a["aip"]), "metadati_nuovi": {"oggetto": "Nuovo"},
                               "motivo": "Pratica modello di prova"})
        assert r.status_code == 202, r.text
        modello = r.json()["id"]
    falsa = uuid.uuid4()
    await _fabbrica(db_url, modello, {
        "id": "$2::uuid", "tenant_id": "$3::uuid", "pdv_id": "$4::uuid",
        "dati": "jsonb_set(jsonb_set(dati, '{urn}', to_jsonb($5::text)), '{aip_id}', "
                "to_jsonb($6::text))",
        "lavoro": "'VERSA'", "lease_detentore": "NULL", "lease_scade_il": "NULL",
        "tentativi": "0", "ultimo_errore": "NULL", "idempotency_key": "NULL",
        "creata_da": "$7::uuid"},
        str(falsa), str(a["vittima"]), str(a["pdv_v"]), a["urn_v"], str(a["aip_v"]),
        str(a["admin_v"]))
    # la pratica vera la porta avanti il lavoratore (versamento); quella falsa no
    await sql(db_url, "UPDATE public.pratiche SET lavoro = NULL WHERE id = $1::uuid", modello)
    dip = _dip(db_url)
    await lavora(dip)
    assert f"cv-{falsa}-versamento" not in dip.versatore.versamenti
    assert all(p["tenant_id"] != str(a["vittima"]) for p in dip.versatore.per_conto)
    await _nessun_fatto(db_url, falsa)
    errore = await _bloccata(db_url, falsa)
    assert "CRONOLOGIA_ASSENTE" in " ".join(errore["difetti"])


async def test_rettifica_vera_passa_lavoratore_e_riscontro(db_url, s3_ciclo) -> None:
    tenant = await crea_ente(db_url)
    operatore = await crea_utente(db_url, tenant, "OPERATORE")
    classe = await crea_classe(db_url, tenant)
    pdv = await crea_pdv(db_url, tenant, classe, operatore)
    aip, urn = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    bucket_worm(s3_ciclo)
    chi = intestazioni(tenant, "OPERATORE", operatore)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica", headers=chi, json={
            "aip_id": str(aip), "metadati_nuovi": {"oggetto": "Contratto rettificato"},
            "motivo": "Oggetto errato nel versamento originale"})
        assert r.status_code == 202, r.text
        pratica = r.json()["id"]
        dip = _dip(db_url)
        await lavora(dip)
        nuovo = (await c.get(f"/api/v1/ciclo-vita/pratiche/{pratica}", headers=chi)
                 ).json()["pdv_risultato_id"]
    await sql(db_url, "UPDATE public.pdv SET stato = 'CONSERVATO' WHERE id = $1::uuid", nuovo)
    await crea_pda(db_url, tenant, uuid.UUID(nuovo), scadenza=PASSATO)
    await sql(db_url, "UPDATE public.pratiche SET prossimo_tentativo = now() WHERE id = "
                      "$1::uuid", pratica)
    await lavora(dip)
    assert dip.registro.rifiuti == []
    assert ("rettifica", urn, pratica) in dip.registro.foglie
    stato = await sql(db_url, "SELECT stato FROM public.pratiche WHERE id = $1::uuid", pratica)
    assert stato[0]["stato"] == "COMPLETATA"


async def test_rettifica_con_richiedente_disattivato_respinta_senza_allarme(db_url,
                                                                           s3_ciclo) -> None:
    tenant = await crea_ente(db_url)
    operatore = await crea_utente(db_url, tenant, "OPERATORE")
    classe = await crea_classe(db_url, tenant)
    pdv = await crea_pdv(db_url, tenant, classe, operatore)
    aip, _ = await crea_pda(db_url, tenant, pdv, scadenza=PASSATO)
    bucket_worm(s3_ciclo)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/rettifica",
                         headers=intestazioni(tenant, "OPERATORE", operatore), json={
                             "aip_id": str(aip), "metadati_nuovi": {"oggetto": "Altro"},
                             "motivo": "Oggetto errato nel versamento originale"})
        pratica = r.json()["id"]
    await sql(db_url, "UPDATE public.users SET attivo = false WHERE id = $1", operatore)
    dip = _dip(db_url)
    await lavora(dip)
    riga = (await sql(db_url, "SELECT stato, ultimo_errore FROM public.pratiche WHERE id = "
                              "$1::uuid", pratica))[0]
    assert riga["stato"] == "RESPINTA"
    assert "LC-409-AUTORIZZAZIONI" not in str(riga["ultimo_errore"] or "")
    await _nessun_fatto(db_url, pratica)
