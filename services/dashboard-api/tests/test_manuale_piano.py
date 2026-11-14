# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Manuale di conservazione e piano di cessazione generati dai dati reali — VD-C-013.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.services import manuale as man
from app.services import piano_cessazione as pc
from app.services.manuale import DatiConservatore, campo, genera_manuale
from app.services.piano_cessazione import genera_piano_cessazione
from httpx import ASGITransport, AsyncClient

ADESSO = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

DATI_COMPLETI = {
    "denominazione": "Conservatore Esempio S.p.A.",
    "codice_fiscale": "12345678903",
    "partita_iva": "12345678903",
    "sede_legale": "Via Roma 1, 00100 Roma",
    "pec": "conservazione@pec.esempio.it",
    "email_contatto": "info@esempio.it",
    "responsabile_servizio_nome": "Maria Rossi",
    "responsabile_servizio_cf": "RSSMRA80A41H501U",
    "responsabile_funzione_archivistica_nome": "Luca Bianchi",
    "responsabile_sicurezza_nome": "Anna Verdi",
    "responsabile_protezione_dati_nome": "Paolo Neri",
    "ubicazione_data_center": "Italia (Milano)",
    "manuale_versione": "1.0",
    "piano_cessazione_versione": "1.0",
}

CLASSI = [
    {
        "codice": "FATTURA",
        "nome": "Fatture",
        "tenant_id": None,
        "tipologia_xml": "DAI",
        "anni_conservazione": 10,
        "conservazione_permanente": False,
        "formati_ammessi": ["application/pdf"],
        "metadati_obbligatori": ["NumeroFattura"],
    },
    {
        "codice": "DELIBERA",
        "nome": "Delibere",
        "tenant_id": "t-1",
        "tipologia_xml": "DAI",
        "anni_conservazione": None,
        "conservazione_permanente": True,
        "formati_ammessi": ["application/pdf"],
        "metadati_obbligatori": [],
    },
]


def _vuoti() -> DatiConservatore:
    return DatiConservatore(**dict.fromkeys(DATI_COMPLETI, ""))


def _testo(doc: dict) -> str:
    import json

    return json.dumps(doc, ensure_ascii=False)


# ── dati del conservatore ─────────────────────────────────────────────


def test_campo_segnaposto_da_compilare():
    """Il CF 00000000000 e «Da nominare» non sono dati reali: risultano «da compilare»."""
    assert campo("00000000000", "cf_ente") == {"valore": None, "stato": "da compilare"}
    assert campo("Da nominare")["stato"] == "da compilare"
    assert campo("", "email")["stato"] == "da compilare"


def test_campo_valori_non_validi():
    assert campo("12345678901", "piva")["stato"] == "non valido"  # cifra di controllo errata
    assert campo("tenant-1@verumdoc.local", "email")["stato"] == "non valido"
    assert campo("12345678903", "piva")["stato"] == "compilato"
    assert campo("RSSMRA80A41H501U", "cf_persona")["stato"] == "compilato"


# ── manuale ───────────────────────────────────────────────────────────


def test_manuale_senza_configurazione_niente_dati_inventati():
    """Senza configurazione: nessun CF segnaposto, nessuna denominazione inventata, bozza."""
    doc = genera_manuale(_vuoti(), adesso=ADESSO, classi=CLASSI)
    testo = _testo(doc)
    assert "00000000000" not in testo
    assert "VerumDoc S.r.l." not in testo
    assert doc["conservatore"]["codice_fiscale"]["stato"] == "da compilare"
    s1 = doc["sezioni"][0]
    assert s1["stato_sezione"] == "da_compilare"
    assert "[da compilare]" in s1["contenuto"]
    assert "codice_fiscale" in s1["dati_da_compilare"]
    assert doc["meta"]["stato_documento"] == "bozza"
    assert doc["meta"]["versione"] == "bozza non versionata"


def test_manuale_funzioni_inesistenti_marcate_da_realizzare():
    """Esibizione, duplicati, scarto: «da_realizzare» nel testo e nel campo strutturato."""
    doc = genera_manuale(DatiConservatore(**DATI_COMPLETI), adesso=ADESSO, classi=CLASSI)
    per_numero = {s["numero"]: s for s in doc["sezioni"]}
    for numero in ("6", "9", "10"):
        assert per_numero[numero]["stato_sezione"] == "da_realizzare", numero
        assert per_numero[numero]["contenuto"].startswith("[DA REALIZZARE]")
    # Monitoraggio: hashchain presente ma verifiche periodiche assenti → in parte
    assert per_numero["8"]["stato_sezione"] == "parziale"
    assert per_numero["8"]["contenuto"].startswith("[IN PARTE DA REALIZZARE]")
    assert (
        "verifiche periodiche di integrità e leggibilità: da realizzare"
        in per_numero["8"]["contenuto"].lower()
    )
    # PdA: Object Lock in parte realizzato, IPdA/firma da realizzare → parziale
    assert per_numero["5"]["stato_sezione"] == "parziale"
    stati = {f["chiave"]: f["stato"] for f in per_numero["5"]["funzioni"]}
    assert stati["ipda_sincro"] == "da_realizzare"
    # R13a: realizzata, prestatore da scegliere
    assert stati["firma_marca_qualificate"] == "parziale"
    # Nessuna sezione dichiara come esistente una funzione non realizzata
    for s in doc["sezioni"]:
        if any(f["stato"] != "generata" for f in s["funzioni"]):
            assert s["stato_sezione"] != "generata"


def test_manuale_classi_dal_db_in_tabella():
    doc = genera_manuale(DatiConservatore(**DATI_COMPLETI), adesso=ADESSO, classi=CLASSI)
    sez = doc["sezioni"][2]
    tabella = sez["dati"]["classi"]
    assert [r["codice"] for r in tabella] == ["FATTURA", "DELIBERA"]
    assert tabella[0]["tempo_conservazione"] == "10 anni"
    assert tabella[1]["tempo_conservazione"] == "permanente"
    assert tabella[1]["ambito"] == "cliente"


def test_manuale_db_non_disponibile_sezione_non_disponibile():
    doc = genera_manuale(
        DatiConservatore(**DATI_COMPLETI),
        adesso=ADESSO,
        classi=None,
        errore_classi="lettura non riuscita (OperationalError)",
    )
    sez = doc["sezioni"][2]
    assert sez["stato_sezione"] == "non_disponibile"
    assert sez["contenuto"].startswith("[NON DISPONIBILE]")


def test_manuale_versione_firma_e_impronta():
    """Versione da configurazione, stato di firma «non firmato», impronta stabile del contenuto."""
    d = DatiConservatore(**DATI_COMPLETI)
    a = genera_manuale(d, adesso=ADESSO, classi=CLASSI)
    b = genera_manuale(d, adesso=datetime(2027, 1, 1, tzinfo=UTC), classi=CLASSI)
    assert a["meta"]["versione"] == "1.0"
    assert a["meta"]["stato_firma"]["firmato"] is False
    assert "Pubblica" in a["meta"]["stato_firma"]["motivo"]  # R13a: versione sigillata
    assert a["meta"]["impronta_contenuto_sha256"] == b["meta"]["impronta_contenuto_sha256"]
    c = genera_manuale(d, adesso=ADESSO, classi=CLASSI[:1])
    assert c["meta"]["impronta_contenuto_sha256"] != a["meta"]["impronta_contenuto_sha256"]


def test_manuale_non_cita_norme_abrogate():
    doc = genera_manuale(DatiConservatore(**DATI_COMPLETI), adesso=ADESSO, classi=CLASSI)
    assert not any("DPCM 3 dicembre 2013" in r for r in doc["meta"]["riferimenti_normativi"])


def test_dati_conservatore_da_ambiente(monkeypatch):
    monkeypatch.setenv("CONSERVATORE_DENOMINAZIONE", "Conservatore Esempio S.p.A.")
    monkeypatch.setenv("CONSERVATORE_CODICE_FISCALE", "12345678903")
    d = DatiConservatore()
    assert d.denominazione == "Conservatore Esempio S.p.A."
    assert campo(d.codice_fiscale, "cf_ente")["stato"] == "compilato"


# ── piano di cessazione ───────────────────────────────────────────────

CONSISTENZA = [
    {
        "ragione_sociale": "Comune di Esempio",
        "codice_fiscale": "80012345678",
        "stato": "ATTIVO",
        "numero_pda": 3,
        "volume_byte": 3000,
        "numero_documenti": 5,
    },
    {
        "ragione_sociale": "Ditta Prova",
        "codice_fiscale": "12345678903",
        "stato": "TRIAL",
        "numero_pda": 0,
        "volume_byte": 0,
        "numero_documenti": 0,
    },
]


def test_piano_struttura_allegato_b():
    """Otto sezioni dell'Allegato B, la 7 con le sei fasi del processo."""
    doc = genera_piano_cessazione(
        DatiConservatore(**DATI_COMPLETI), adesso=ADESSO, consistenza=CONSISTENZA
    )
    assert [s["numero"] for s in doc["sezioni"]] == [str(i) for i in range(1, 9)]
    assert [s["numero"] for s in doc["sezioni"][6]["sottosezioni"]] == [
        "7.1",
        "7.2",
        "7.3",
        "7.4",
        "7.5",
        "7.6",
    ]
    assert any("455/2021" in n for n in doc["meta"]["normativa"])


def test_piano_elenchi_consistenza_dal_db():
    doc = genera_piano_cessazione(
        DatiConservatore(**DATI_COMPLETI), adesso=ADESSO, consistenza=CONSISTENZA
    )
    analisi = doc["sezioni"][6]["sottosezioni"][1]
    assert analisi["dati"]["totale_pda"] == 3
    assert analisi["dati"]["elenchi_consistenza"][0]["produttore"] == "Comune di Esempio"


def test_piano_export_e_cancellazione_da_realizzare():
    doc = genera_piano_cessazione(
        DatiConservatore(**DATI_COMPLETI), adesso=ADESSO, consistenza=CONSISTENZA
    )
    sotto = {s["numero"]: s for s in doc["sezioni"][6]["sottosezioni"]}
    assert sotto["7.3"]["stato_sezione"] == "da_realizzare"
    assert sotto["7.3"]["contenuto"].startswith("[DA REALIZZARE]")
    assert doc["sezioni"][6]["stato_sezione"] == "da_realizzare"
    assert doc["sezioni"][7]["stato_sezione"] == "da_realizzare"


def test_piano_senza_configurazione_niente_contatti_inventati():
    """Né CF segnaposto né PEC, e-mail o telefono inventati."""
    doc = genera_piano_cessazione(_vuoti(), adesso=ADESSO, consistenza=[])
    testo = _testo(doc)
    for finto in ("00000000000", "pec.verumdoc.it", "cessazione@verumdoc.it", "+39 02 000 0000"):
        assert finto not in testo
    assert doc["sezioni"][0]["stato_sezione"] == "da_compilare"
    assert doc["meta"]["stato_firma"]["firmato"] is False
    assert doc["meta"]["versione"] == "bozza non versionata"


def test_piano_db_non_disponibile():
    doc = genera_piano_cessazione(
        DatiConservatore(**DATI_COMPLETI),
        adesso=ADESSO,
        consistenza=None,
        errore_consistenza="errore DB",
    )
    assert doc["sezioni"][6]["sottosezioni"][1]["stato_sezione"] == "non_disponibile"


# ── endpoint ──────────────────────────────────────────────────────────


def _tok(ruolo: str) -> str:
    from app.auth.jwt_handler import create_access_token

    return create_access_token(tenant_id="t-1", user_id="u-1", ruolo=ruolo, scopes=["*"])


@pytest.mark.asyncio
async def test_endpoint_manuale_e_piano_usano_i_dati_reali(monkeypatch):
    """Gli endpoint leggono configurazione e DB e restituiscono stato_sezione, versione e firma."""
    from app.main import app

    async def _classi():
        return CLASSI, None

    async def _consistenza():
        return CONSISTENZA, None

    monkeypatch.setattr(man, "leggi_classi_attive", _classi)
    monkeypatch.setattr(pc, "leggi_consistenza", _consistenza)
    monkeypatch.setenv("CONSERVATORE_DENOMINAZIONE", "Conservatore Esempio S.p.A.")
    h = {"Authorization": f"Bearer {_tok('RSC_CONSERVATORE')}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        m = await c.get("/api/dashboard/manuale-conservazione", headers=h)
        p = await c.get("/api/dashboard/piano-cessazione", headers=h)
    assert m.status_code == 200 and p.status_code == 200
    manuale = m.json()
    assert manuale["conservatore"]["denominazione"]["valore"] == "Conservatore Esempio S.p.A."
    assert manuale["sezioni"][2]["dati"]["classi"][0]["codice"] == "FATTURA"
    assert all("stato_sezione" in s for s in manuale["sezioni"])
    assert manuale["meta"]["stato_firma"]["stato"] == "non firmato"
    piano = p.json()
    assert piano["sezioni"][6]["sottosezioni"][1]["dati"]["totale_pda"] == 3


@pytest.mark.asyncio
async def test_endpoint_manuale_senza_db_non_va_in_500():
    """Senza DB inizializzato la risposta è 200 con la sezione classi «non_disponibile»."""
    from app.main import app

    h = {"Authorization": f"Bearer {_tok('SUPER_ADMIN')}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/dashboard/manuale-conservazione", headers=h)
        p = await c.get("/api/dashboard/piano-cessazione", headers=h)
    assert r.status_code == 200
    assert r.json()["sezioni"][2]["stato_sezione"] == "non_disponibile"
    assert p.status_code == 200
    assert p.json()["sezioni"][6]["sottosezioni"][1]["stato_sezione"] == "non_disponibile"
