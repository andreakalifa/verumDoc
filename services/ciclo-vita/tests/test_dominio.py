# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Regole pure del ciclo di vita (app/domain): macchine a stati, permessi delle
transizioni, blocchi dello scarto, controllo della retention prima di ogni
cancellazione, impronta del piano, verbale, lettura sicura dell'indice di un PdD.
Contratto Q2 di R05, VD-C-072; REGOLE §9.7, §11.2, §13.1, §23.1.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from app.domain import permessi as pm
from app.domain import scarto as sc
from app.domain import stati as st
from app.domain.documenti import atto, scrivi_verbale_scarto
from app.domain.indice_pdd import IndiceNonValido, leggi_indice_pdd, nome_sicuro

ADESSO = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _ctx(ruolo: str, *, pa: bool = False, disponibili=(), citati=(), stesso=True):
    return st.ContestoTransizione(ruolo=ruolo, stesso_ente=stesso, richiede_nulla_osta=pa,
                                  allegati_disponibili=frozenset(disponibili),
                                  allegati_citati=frozenset(citati))


def test_stati_ogni_stato_raggiungibile() -> None:
    """REGOLE §11.2: ogni stato non iniziale ha una transizione che lo raggiunge."""
    assert st.stati_non_raggiungibili() == []


def test_stati_scarto_iter_contratto_q2() -> None:
    m = st.macchina(st.SCARTO)
    # R09 (D20): CHIUSA_CON_ECCEZIONI è finale
    assert m.iniziale == st.BOZZA and m.finali == {st.ESEGUITA, st.RESPINTA,
                                                   st.CHIUSA_CON_ECCEZIONI}
    assert {(t.da, t.a) for t in m.transizioni} >= {
        (st.BOZZA, st.PROPOSTA), (st.PROPOSTA, st.AUTORIZZATA_PRODUTTORE),
        (st.AUTORIZZATA_PRODUTTORE, st.NULLA_OSTA), (st.NULLA_OSTA, st.ESEGUIBILE),
        (st.AUTORIZZATA_PRODUTTORE, st.ESEGUIBILE), (st.ESEGUIBILE, st.ESEGUITA)}


def test_transizione_autorizzazione_senza_allegato_rifiutata() -> None:
    blocco = st.motivo_blocco_transizione(st.SCARTO, st.PROPOSTA, st.AUTORIZZATA_PRODUTTORE,
                                          _ctx(st.ADMIN_TENANT))
    assert blocco is not None and blocco[0] == "LC-422-ALLEGATI"


def test_transizione_autorizzazione_con_allegato_citato_ammessa() -> None:
    ctx = _ctx(st.ADMIN_TENANT, disponibili={st.ALLEGATO_AUTORIZZAZIONE},
               citati={st.ALLEGATO_AUTORIZZAZIONE})
    assert st.motivo_blocco_transizione(st.SCARTO, st.PROPOSTA, st.AUTORIZZATA_PRODUTTORE,
                                        ctx) is None


def test_transizione_autorizzazione_ruolo_conservatore_rifiutato() -> None:
    """Contratto Q2: autorizza il PRODUTTORE (ADMIN_TENANT), non il conservatore."""
    ctx = _ctx(st.RSC, disponibili={st.ALLEGATO_AUTORIZZAZIONE},
               citati={st.ALLEGATO_AUTORIZZAZIONE})
    blocco = st.motivo_blocco_transizione(st.SCARTO, st.PROPOSTA, st.AUTORIZZATA_PRODUTTORE, ctx)
    assert blocco is not None and blocco[0] == "LC-403-RUOLO"


def test_transizione_esecuzione_solo_conservatore() -> None:
    for ruolo, atteso in ((st.ADMIN_TENANT, "LC-403-RUOLO"), (st.OPERATORE, "LC-403-RUOLO"),
                          (st.RSC, None), (st.SUPER_ADMIN, None)):
        blocco = st.motivo_blocco_transizione(st.SCARTO, st.ESEGUIBILE, st.ESEGUITA, _ctx(ruolo))
        assert (blocco[0] if blocco else None) == atteso, ruolo


def test_transizione_pa_richiede_nulla_osta() -> None:
    """Per una PA da AUTORIZZATA_PRODUTTORE non si va direttamente a ESEGUIBILE."""
    blocco = st.motivo_blocco_transizione(st.SCARTO, st.AUTORIZZATA_PRODUTTORE, st.ESEGUIBILE,
                                          _ctx(st.RSC, pa=True))
    assert blocco is not None and blocco[0] == "LC-409-TRANSIZIONE"
    assert st.motivo_blocco_transizione(st.SCARTO, st.AUTORIZZATA_PRODUTTORE, st.ESEGUIBILE,
                                        _ctx(st.RSC, pa=False)) is None
    blocco = st.motivo_blocco_transizione(
        st.SCARTO, st.AUTORIZZATA_PRODUTTORE, st.NULLA_OSTA,
        _ctx(st.ADMIN_TENANT, pa=False, disponibili={"NULLA_OSTA"}, citati={"NULLA_OSTA"}))
    assert blocco is not None and blocco[0] == "LC-409-TRANSIZIONE"


def test_transizione_ruolo_ente_su_altro_ente_404() -> None:
    blocco = st.motivo_blocco_transizione(st.SCARTO, st.BOZZA, st.PROPOSTA,
                                          _ctx(st.ADMIN_TENANT, stesso=False))
    assert blocco == ("LC-404", "Pratica non trovata")


def test_transizione_automatica_non_richiedibile() -> None:
    blocco = st.motivo_blocco_transizione(st.RETTIFICA, st.RICHIESTA, st.IN_VERSAMENTO,
                                          _ctx(st.ADMIN_TENANT))
    assert blocco is not None and blocco[0] == "LC-409-TRANSIZIONE"


def test_azioni_consentite_motivo_blocco() -> None:
    azioni = st.azioni_consentite(st.SCARTO, st.PROPOSTA, _ctx(st.ADMIN_TENANT))
    autorizza = next(a for a in azioni if a["nuovo_stato"] == st.AUTORIZZATA_PRODUTTORE)
    assert autorizza["consentita"] is False and "AUTORIZZAZIONE" in autorizza["motivo_blocco"]


def test_permessi_avvio_scarto() -> None:
    ente = "00000000-0000-0000-0000-0000000000aa"
    admin = pm.Identita(ente, "u", st.ADMIN_TENANT, "jwt")
    assert pm.motivo_blocco_avvio(st.SCARTO, admin, ente) is None
    assert pm.motivo_blocco_avvio(st.SCARTO, admin, "altro")[0] == "LC-404-ENTE"
    assert pm.motivo_blocco_avvio(st.SCARTO, pm.Identita(ente, "u", st.OPERATORE, "jwt"),
                                  ente)[0] == "LC-403-RUOLO"
    assert pm.motivo_blocco_avvio(st.SCARTO, pm.Identita(ente, "u", st.ADMIN_TENANT, "api_key"),
                                  ente)[0] == "LC-403-RUOLO"
    rsc = pm.Identita("conservatore", "u", st.RSC, "jwt")
    assert pm.motivo_blocco_avvio(st.SCARTO, rsc, ente) is None


def test_permessi_annullamento_regola_configurabile() -> None:
    op = pm.Identita("e", "u", st.OPERATORE, "jwt")
    argomenti = {"ruoli_prima": ["ADMIN_TENANT", "OPERATORE"], "ruoli_dopo": ["ADMIN_TENANT"]}
    assert pm.motivo_blocco_annullamento(op, conservato=False, **argomenti) is None
    assert pm.motivo_blocco_annullamento(op, conservato=True, **argomenti)[0] == "LC-403-RUOLO"
    admin = pm.Identita("e", "u", st.ADMIN_TENANT, "jwt")
    # R09 (D23): elenco vuoto = annullamento non ammesso in quello stato
    assert pm.motivo_blocco_annullamento(admin, conservato=True, ruoli_prima=["ADMIN_TENANT"],
                                         ruoli_dopo=[])[0] == "LC-409-ANNULLAMENTO"


def _pda(**campi):
    base = {"aip_id": "a", "urn": "u", "stato": "CONSERVATO", "permanente": False,
            "scadenza": ADESSO - timedelta(days=1)}
    return sc.DatiPda(**{**base, **campi})


@pytest.mark.parametrize(("campi", "motivo"), [
    ({}, None),
    ({"stato": "IN_CATENA"}, sc.STATO_NON_CONSERVATO),
    ({"permanente": True}, sc.CONSERVAZIONE_PERMANENTE),
    ({"scadenza": None}, sc.CONSERVAZIONE_PERMANENTE),
    ({"scadenza": ADESSO + timedelta(seconds=1)}, sc.RETENTION_NON_SCADUTA),
    ({"fascicolo_aperto": True}, sc.FASCICOLO_APERTO),
    ({"termine_fascicolo": ADESSO + timedelta(days=1)}, sc.TERMINE_FASCICOLO_NON_SCADUTO),
    ({"in_altra_pratica": True}, sc.IN_ALTRA_PRATICA),
    ({"esibizione_in_corso": True}, sc.ESIBIZIONE_IN_CORSO),
])
def test_motivo_blocco_pda(campi, motivo) -> None:
    assert sc.motivo_blocco_pda(_pda(**campi), ADESSO) == motivo


def test_motivo_blocco_oggetto_retention_futura_bloccata() -> None:
    """Contratto Q2: MAI una cancellazione prima della scadenza della retention."""
    futuro = sc.OggettoWorm("k", "v", ADESSO + timedelta(microseconds=1), False)
    assert sc.motivo_blocco_oggetto(futuro, ADESSO) == sc.RETENTION_NON_SCADUTA
    scaduto = sc.OggettoWorm("k", "v", ADESSO - timedelta(seconds=1), False)
    assert sc.motivo_blocco_oggetto(scaduto, ADESSO) is None
    assert sc.motivo_blocco_oggetto(sc.OggettoWorm("k", "v", ADESSO, False), ADESSO) is None


def test_motivo_blocco_oggetto_legal_hold_e_retention_assente() -> None:
    assert sc.motivo_blocco_oggetto(sc.OggettoWorm("k", "v", ADESSO - timedelta(days=1), True),
                                    ADESSO) == sc.LEGAL_HOLD
    assert sc.motivo_blocco_oggetto(sc.OggettoWorm("k", "v", None, False),
                                    ADESSO) == sc.RETENTION_ASSENTE


def test_motivo_blocco_oggetti_mai_parziale() -> None:
    oggetti = [sc.OggettoWorm("a", "1", ADESSO - timedelta(days=1), False),
               sc.OggettoWorm("b", "1", ADESSO + timedelta(days=1), False)]
    assert sc.motivo_blocco_oggetti(oggetti, ADESSO) == sc.RETENTION_NON_SCADUTA


def test_impronta_piano_dipende_da_ordine_ed_esiti() -> None:
    def impronta(righe):
        i = sc.ImprontaPiano("SCARTO", "e", {"classi": ["x"]})
        for r in righe:
            i.aggiungi(*r)
        return i.hex()

    base = [("a", "SELEZIONATO", None), ("b", "BLOCCATO", "LEGAL_HOLD")]
    assert impronta(base) == impronta(list(base))
    assert impronta(base) != impronta(list(reversed(base)))
    assert impronta(base) != impronta([("a", "BLOCCATO", "X"), base[1]])


def test_verbale_deterministico_e_completo() -> None:
    def scrivi():
        parti: list[str] = []
        scrivi_verbale_scarto(
            parti.append, pratica={"id": "p", "motivo": "m", "criteri": {"anni": [2015]},
                                   "impronta_piano": "f" * 64, "eseguita_il": "2026-10-23"},
            ente={"id": "e", "denominazione": "Comune & Co", "codice_ipa": "c_x"},
            conservatore="", cronologia=[{"da_stato": "BOZZA", "a_stato": "PROPOSTA"}],
            allegati=[{"tipo": "NULLA_OSTA", "sha256": "a" * 64, "nome_file": "n.pdf"}],
            elementi=iter([{"aip_id": "1", "urn": "u1", "esito": "ELIMINATO", "motivo": None},
                           {"aip_id": "2", "urn": "u2", "esito": "BLOCCATO",
                            "motivo": "LEGAL_HOLD"}]),
            conteggi={"ELIMINATO": 1, "BLOCCATO": 1})
        return "".join(parti)

    a, b = scrivi(), scrivi()
    assert a == b
    assert "Comune &amp; Co" in a and 'motivo="LEGAL_HOLD"' in a and "non indicato" in a


def test_atto_ordinato_e_json_canonico() -> None:
    x = atto("rettifica", {"b": {"z": 1, "a": 2}, "a": "v"})
    assert x == atto("rettifica", {"a": "v", "b": {"a": 2, "z": 1}})
    assert x.index(b'nome="a"') < x.index(b'nome="b"')


INDICE_VERUMDOC = """<?xml version="1.0"?>
<SInCRO xmlns="urn:uni:uninfo:sincro:2020" tipo="DI">
  <Intestazione><IdentificativoAIP>AIP-1</IdentificativoAIP>
    <ConservatoreAIP>Altro conservatore</ConservatoreAIP></Intestazione>
  <Descrizione><MetadatiDocumento><Oggetto>Delibera</Oggetto>
    <SoggettoProduttore><Identificativo>c_x</Identificativo></SoggettoProduttore>
  </MetadatiDocumento></Descrizione>
  <FileGroup><File><NomeFile>doc.pdf</NomeFile><TipoFile>PRINCIPALE</TipoFile>
    <Hash><Impronta>{h}</Impronta><Algoritmo>SHA-256</Algoritmo></Hash></File></FileGroup>
</SInCRO>"""


def test_indice_formato_verumdoc() -> None:
    h = hashlib.sha256(b"x").hexdigest()
    indice = leggi_indice_pdd(INDICE_VERUMDOC.format(h=h).encode(), dimensione_massima=10**6)
    assert indice.identificativo == "AIP-1" and indice.conservatore == "Altro conservatore"
    assert [(f.nome, f.impronta, f.tipo) for f in indice.file] == [("doc.pdf", h, "PRINCIPALE")]
    assert indice.metadati == {"oggetto": "Delibera", "soggetto_produttore_id": "c_x"}


def test_indice_stile_uni_base64() -> None:
    grezzo = hashlib.sha256(b"y").digest()
    xml = (f'<PIndex xmlns="urn:x" ID="IDC-9"><FileGroup><File><Path>a/b.xml</Path>'
           f'<Hash function="SHA-256">{base64.b64encode(grezzo).decode()}</Hash></File>'
           f"</FileGroup></PIndex>").encode()
    indice = leggi_indice_pdd(xml, dimensione_massima=10**6)
    assert indice.identificativo == "IDC-9"
    assert indice.file[0].impronta == grezzo.hex() and indice.file[0].nome == "a/b.xml"


def test_indice_dtd_rifiutata() -> None:
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'
    with pytest.raises(IndiceNonValido, match="DTD"):
        leggi_indice_pdd(xml, dimensione_massima=10**6)


def test_indice_senza_file_o_troppo_grande() -> None:
    with pytest.raises(IndiceNonValido):
        leggi_indice_pdd(b"<SInCRO/>", dimensione_massima=10**6)
    with pytest.raises(IndiceNonValido, match="oltre"):
        leggi_indice_pdd(b"<SInCRO/>", dimensione_massima=3)


def test_nome_sicuro_zip() -> None:
    assert nome_sicuro("dir/doc.pdf")
    for cattivo in ("/etc/passwd", "../x", "a/../b", "a\\b", "a\x00b", ""):
        assert not nome_sicuro(cattivo), cattivo


# ── R09 (D23, D20): regole pure ──────────────────────────────────────────

def test_r09_quattro_occhi_e_doppia_approvazione() -> None:
    richiedente = "u-1"
    secondo = pm.Identita("e", "u-2", st.ADMIN_TENANT, "jwt")
    stesso = pm.Identita("e", richiedente, st.ADMIN_TENANT, "jwt")
    chiave = pm.Identita("e", "u-3", st.ADMIN_TENANT, "api_key")
    altro_ente = pm.Identita("x", "u-4", st.ADMIN_TENANT, "jwt")
    from datetime import UTC, datetime, timedelta

    richiesta_il = datetime(2026, 11, 1, tzinfo=UTC)
    argomenti = {"tenant_pratica": "e", "richiedente": richiedente,
                 "ruoli_approvazione": ["ADMIN_TENANT"], "richiesta_ruolo": "ADMIN_TENANT",
                 "pratica_creata_il": richiesta_il,
                 "approvatore_creato_il": richiesta_il - timedelta(days=1)}
    assert pm.motivo_blocco_approvazione(secondo, **argomenti) is None
    # Revisione R09 (Z1): richiesta con chiave API → si respinge, non si approva
    via_chiave = {**argomenti, "richiesta_ruolo": "API_SYSTEM"}
    assert pm.motivo_blocco_approvazione(secondo, **via_chiave)[0] == "LC-403-QUATTRO-OCCHI"
    assert pm.motivo_blocco_approvazione(secondo, **via_chiave, approva=False) is None
    # Z1: account creato dopo la richiesta (alias creato apposta)
    dopo = {**argomenti, "approvatore_creato_il": richiesta_il + timedelta(seconds=1)}
    assert pm.motivo_blocco_approvazione(secondo, **dopo)[0] == "LC-403-QUATTRO-OCCHI"
    assert pm.motivo_blocco_approvazione(
        secondo, **{**argomenti, "approvatore_creato_il": None})[0] == "LC-403-QUATTRO-OCCHI"
    # Z1: con la doppia approvazione la richiesta è di una persona
    assert pm.motivo_blocco_richiesta_doppia(chiave)[0] == "LC-403-QUATTRO-OCCHI"
    assert pm.motivo_blocco_richiesta_doppia(
        pm.Identita("e", "u-5", "API_SYSTEM", "jwt"))[0] == "LC-403-QUATTRO-OCCHI"
    assert pm.motivo_blocco_richiesta_doppia(secondo) is None
    assert st.RUOLI_APPROVAZIONE == frozenset({st.ADMIN_TENANT})
    assert pm.motivo_blocco_approvazione(stesso, **argomenti)[0] == "LC-403-QUATTRO-OCCHI"
    assert pm.motivo_blocco_approvazione(chiave, **argomenti)[0] == "LC-403-QUATTRO-OCCHI"
    assert pm.motivo_blocco_approvazione(altro_ente, **argomenti)[0] == "LC-404"
    assert pm.motivo_blocco_approvazione(
        secondo, **{**argomenti, "ruoli_approvazione": []})[0] == "LC-403-QUATTRO-OCCHI"
    assert pm.doppia_approvazione_richiesta("DOPO_CONSERVAZIONE", conservato=True)
    assert not pm.doppia_approvazione_richiesta("DOPO_CONSERVAZIONE", conservato=False)
    assert not pm.doppia_approvazione_richiesta("MAI", conservato=True)
    assert pm.doppia_approvazione_richiesta("valore sconosciuto", conservato=False)


def _passo_r09(da, a, utente, ruolo):
    return {"da_stato": da, "a_stato": a, "utente_id": utente, "ruolo": ruolo}


def test_r09_iter_annullamento_quattro_occhi() -> None:
    from app.domain import autorizzazioni as aut

    def difetti(approvatore, richiesta=True):
        cronologia = [_passo_r09(None, "RICHIESTA", "u-1", "ADMIN_TENANT")]
        da = "RICHIESTA"
        if approvatore:
            cronologia.append(_passo_r09("RICHIESTA", "APPROVATA", approvatore, "ADMIN_TENANT"))
            da = "APPROVATA"
        cronologia.append(_passo_r09(da, "ESEGUITA", None, "SISTEMA"))
        return aut.difetti_iter_puntuale(
            "ANNULLAMENTO", cronologia, stato_attuale="ESEGUITA", creata_da="u-1",
            creata_ruolo="ADMIN_TENANT", ruoli_ammessi={"ADMIN_TENANT"},
            approvazione_richiesta=richiesta, ruoli_approvazione={"ADMIN_TENANT"})

    assert difetti("u-2") == []
    assert any(d.startswith("QUATTRO_OCCHI") for d in difetti("u-1"))
    # Revisione R09 (Z1): richiesta con chiave API e doppia approvazione
    cronologia = [_passo_r09(None, "RICHIESTA", "u-1", "API_SYSTEM"),
                  _passo_r09("RICHIESTA", "APPROVATA", "u-2", "ADMIN_TENANT")]
    assert any(d.startswith("RICHIEDENTE_NON_PERSONA") for d in aut.difetti_iter_puntuale(
        "ANNULLAMENTO", cronologia, stato_attuale="APPROVATA", creata_da="u-1",
        creata_ruolo="API_SYSTEM", ruoli_ammessi={"API_SYSTEM"}, approvazione_richiesta=True,
        ruoli_approvazione={"ADMIN_TENANT"}, applica=True))
    # Z3: gli stati in cui il lavoratore applica l'annullamento
    assert aut.difetti_iter_puntuale(
        "ANNULLAMENTO", cronologia[:1], stato_attuale="RICHIESTA", creata_da="u-1",
        creata_ruolo="API_SYSTEM", ruoli_ammessi={"API_SYSTEM"}, applica=True) == []
    assert any(d.startswith("APPROVAZIONE_ASSENTE") for d in difetti(None))
    assert difetti(None, richiesta=False) == []


def test_r09_chiusura_con_eccezioni_motivo_e_ruolo() -> None:
    ctx = st.ContestoTransizione(st.RSC, False, False, motivo="breve")
    blocco = st.motivo_blocco_transizione(st.SCARTO, st.ESEGUITA_CON_ECCEZIONI,
                                          st.CHIUSA_CON_ECCEZIONI, ctx)
    assert blocco[0] == "LC-422-MOTIVO"
    ctx = st.ContestoTransizione(st.ADMIN_TENANT, True, False, motivo="x" * 30)
    assert st.motivo_blocco_transizione(st.SCARTO, st.ESEGUITA_CON_ECCEZIONI,
                                        st.CHIUSA_CON_ECCEZIONI, ctx)[0] == "LC-403-RUOLO"
    # l'approvazione dell'annullamento non passa dalla transizione generica
    ctx = st.ContestoTransizione(st.ADMIN_TENANT, True, False, motivo="x" * 30)
    assert st.motivo_blocco_transizione(st.ANNULLAMENTO, st.RICHIESTA, st.APPROVATA,
                                        ctx)[0] == "LC-409-TRANSIZIONE"
    assert st.stati_non_raggiungibili() == []
