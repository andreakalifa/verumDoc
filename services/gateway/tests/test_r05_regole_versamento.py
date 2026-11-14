# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R05 Q1 — il versamento usa i livelli nuovi del motore delle regole: accordo di
versamento (scelto dalla classe e dall'ente, oppure dal campo ``accordo``
dell'indice) e personalizzazione motivata del PdV (``override``); la fotografia
``regole_applicate`` li riporta. Inoltre: l'impronta di un indice senza i campi
nuovi non cambia (idempotenza fra rilasci) e il validatore preventivo della
libreria dà gli stessi codici del gateway sui controlli di forma.
"""
# ruff: noqa: F811 — le fixture importate da test_versamento si ridichiarano come parametri
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from typing import Any

import pytest
from app.models.versamento import CAMPI_METADATI_NOTI, IndiceUD
from app.services import versamento_service as vs

from libs.verumdoc_rules.validatore import CAMPI_METADATI_INDICE, controlla_forma
from tests.infra_reale import crea_tenant_e_classe, db_url, richiede_pg, s3_server, sql  # noqa: F401
from tests.test_versamento import PDF, _indice, _versa, s3, utente_di  # noqa: F401 — fixture


async def _accordo(url: str, tenant: uuid.UUID, codice: str, valori: dict[str, Any], *,
                   classe: uuid.UUID | None = None, versione: int = 1,
                   valido_dal: str = "2026-01-01", cessato: bool = False) -> None:
    await sql(url, "INSERT INTO public.accordi_versamento (tenant_id, codice, versione, classe_id, "
                   "valido_dal, cessato, valori, motivo) VALUES ($1, $2, $3, $4, "
                   "$5, $6, CAST($7 AS jsonb), 'accordo di prova')",
              tenant, codice, versione, classe, date.fromisoformat(valido_dal), cessato,
              json.dumps(valori))


async def _fotografia(url: str, pdv_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    riga = (await sql(url, "SELECT regole_applicate, avvisi FROM public.pdv WHERE id = $1",
                      uuid.UUID(pdv_id)))[0]
    return json.loads(riga["regole_applicate"]), json.loads(riga["avvisi"])


# ── Impronta dell'indice e schema ──────────────────────────────────

def test_impronta_indice_invariata_senza_accordo_e_override() -> None:
    """Un indice di prima di R05 ha la stessa impronta: i ritentativi a cavallo del
    rilascio restano la stessa richiesta (idempotenza, duplicati)."""
    grezzo = _indice()
    indice = IndiceUD.model_validate(grezzo)
    vecchio = {k: v for k, v in indice.model_dump(mode="json").items()
               if k not in ("accordo", "override")}
    atteso = hashlib.sha256(json.dumps(vecchio, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hexdigest()
    assert vs.calcola_impronta_indice(indice) == atteso
    con_accordo = IndiceUD.model_validate({**grezzo, "accordo": "AV-1"})
    assert vs.calcola_impronta_indice(con_accordo) != atteso


def test_campi_noti_della_libreria_coincidono_con_lo_schema() -> None:
    """Il validatore preventivo conosce gli stessi metadati dello schema (RGL-103)."""
    assert CAMPI_METADATI_INDICE == CAMPI_METADATI_NOTI


@pytest.mark.parametrize("modifica,codice", [
    (lambda i: i["documenti"][0].update(
        allegati=[dict(i["documenti"][0], id="x", nome_file="y.pdf")]), "VRS-114"),
    (lambda i: i["documenti"].append(dict(i["documenti"][0], id="d2")), "VRS-111"),
    (lambda i: i["documenti"][0]["parametri"].update(modalita_formazione="z"), "VRS-001"),
    (lambda i: i["documenti"][0]["parametri"].update(oggetto="abc"), "VRS-001"),
    (lambda i: i["documenti"][0]["hash"].update(algoritmo="MD5"), "VRS-001"),
    (lambda i: i["documenti"][0].update(tipo="ALTRO"), "VRS-001"),
    (lambda i: i.update(accordo="spazi non ammessi"), "VRS-001"),
])
def test_forma_del_validatore_come_il_gateway(modifica, codice) -> None:
    """Stesso codice d'errore nel gateway (schema + controlli) e nel validatore."""
    from app.models.versamento import IndiceUD as Modello

    indice = _indice()
    modifica(indice)
    _, rilievi = controlla_forma(indice)
    assert codice in {r["codice"] for r in rilievi}
    with pytest.raises(vs.RifiutoVersamento) as exc:
        letto = vs.leggi_indice(json.dumps(indice))
        assert isinstance(letto, Modello)
        fonti = [vs.FonteFlusso(nome_file=d["nome_file"], flusso=iter(()))
                 for d in {d["nome_file"]: d for d in indice["documenti"]}.values()]
        vs.controlla_indice_e_fonti(letto, fonti, "chiave-di-prova")
    assert exc.value.codice == codice


# ── Accordo di versamento ───────────────────────────────────────────

@richiede_pg
class TestAccordoEOverride:
    async def test_accordo_della_classe_applicato_e_fotografato(self, db_url, s3, utente_di):
        tenant, classe = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        await _accordo(db_url, tenant, "AV-FATT", {"METADATI_OBBLIGATORI": ["cig"]},
                       classe=classe)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "VRS-130"
        rilievi = resp.json()["dettagli"]["rilievi"]
        assert [(r["codice"], r["campo"]) for r in rilievi] == [("RGL-102", "cig")]
        resp = await _versa(_indice(extra={"metadati_extra": {"cig": "Z123"}}),
                            [("fattura.pdf", PDF)])
        assert resp.status_code == 202, resp.text
        foto, _ = await _fotografia(db_url, resp.json()["pdv_id"])
        assert foto["accordo"]["codice"] == "AV-FATT" and foto["accordo"]["versione"] == 1
        assert foto["impostazioni"]["METADATI_OBBLIGATORI"] == {"valore": ["cig"],
                                                                "origine": "ACCORDO"}

    async def test_accordo_dell_indice_e_versione_in_vigore(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        await _accordo(db_url, tenant, "AV-1", {"REGOLA.RGL-103.EFFETTO": "BLOCCA"})
        await _accordo(db_url, tenant, "AV-1", {"REGOLA.RGL-103.EFFETTO": "IGNORA"}, versione=2,
                       valido_dal="2099-01-01")  # non ancora in vigore
        resp = await _versa({**_indice(extra={"campo_libero": "x"}), "accordo": "AV-1"},
                            [("fattura.pdf", PDF)])
        assert resp.status_code == 422
        assert resp.json()["dettagli"]["rilievi"][0]["codice"] == "RGL-103"
        # accordo inesistente o cessato: rifiuto, mai un ripiego silenzioso
        resp = await _versa({**_indice(), "accordo": "AV-NO"}, [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "RGL-112"
        await _accordo(db_url, tenant, "AV-CHIUSO", {}, cessato=True)
        resp = await _versa({**_indice(), "accordo": "AV-CHIUSO"}, [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["dettagli"]["accordo"] == "AV-CHIUSO"

    async def test_override_motivato_avviso_e_fotografia(self, db_url, s3, utente_di):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        override = {"motivo": "Metadato gestionale concordato",
                    "valori": {"REGOLA.RGL-103.EFFETTO": "IGNORA"}}
        resp = await _versa({**_indice(extra={"campo_libero": "x"}), "override": override},
                            [("fattura.pdf", PDF)])
        assert resp.status_code == 202, resp.text
        assert [a["codice"] for a in resp.json()["avvisi"]] == ["RGL-110"]
        foto, avvisi = await _fotografia(db_url, resp.json()["pdv_id"])
        assert foto["override"] == override
        assert foto["impostazioni"]["REGOLA.RGL-103.EFFETTO"] == {"valore": "IGNORA",
                                                                  "origine": "PDV"}
        assert [a["codice"] for a in avvisi] == ["RGL-110"]

    @pytest.mark.parametrize("override", [
        {"motivo": "corto", "valori": {"REGOLA.RGL-103.EFFETTO": "IGNORA"}},
        {"motivo": "Motivo abbastanza lungo", "valori": {"REGOLA.RGL-101.EFFETTO": "IGNORA"}},
        {"motivo": "Motivo abbastanza lungo", "valori": {"FORMATI_AMMESSI": ["image/png"]}},
        {"motivo": "Motivo abbastanza lungo", "valori": {"REGOLA.RGL-103.EFFETTO": "FORSE"}},
    ])
    async def test_override_non_ammesso_rgl_111(self, db_url, s3, utente_di, override):
        tenant, _ = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        resp = await _versa({**_indice(), "override": override}, [("fattura.pdf", PDF)])
        assert resp.status_code == 422 and resp.json()["codice"] == "RGL-111"
        assert resp.json()["dettagli"]["problemi"]
        assert await sql(db_url, "SELECT 1 FROM public.pdv") == []

    async def test_profilo_di_tipologia_scelto_dalla_classe(self, db_url, s3, utente_di):
        """PROFILO_TIPOLOGIA della classe = fattura: i metadati obbligatori del profilo
        (Allegato 5 + DM 17/06/2014) si applicano e la fotografia lo riporta."""
        tenant, classe = await crea_tenant_e_classe(db_url)
        utente_di(tenant)
        await sql(db_url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, classe_id, "
                          "codice, valore) VALUES ('CLASSE', NULL, $1, 'PROFILO_TIPOLOGIA', "
                          "'\"fattura\"'::jsonb)", classe)
        resp = await _versa(_indice(), [("fattura.pdf", PDF)])
        assert resp.status_code == 422
        mancanti = {r["campo"] for r in resp.json()["dettagli"]["rilievi"]
                    if r["codice"] == "RGL-102"}
        assert {"numero_fattura", "cedente_partita_iva", "riservato"} <= mancanti
        assert "impronta_documento" not in mancanti  # calcolato dal servizio
