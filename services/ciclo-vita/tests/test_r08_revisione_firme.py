# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Revisione R08 — firme del ciclo di vita:
  - X1: una firma INDETERMINATE (certificato scaduto) non è mai valida;
  - X3: firmatario autorizzato (CF o impronta del certificato nell'elenco del
    conservatore per l'ente); elenco vuoto = rifiuto; il lavoratore lo ricontrolla
    dal WORM; nulla osta secondo NULLA_OSTA_FIRMA;
  - X12: il riferimento al versamento conta solo nel contenuto firmato (PAdES:
    revisione coperta dall'ultima firma, nessuna modifica dopo);
  - X14: firma staccata senza documento: esito esplicito.
"""
from __future__ import annotations

import base64

import pytest
from app.application import firme
from app.domain import stati as st

from libs.shared.firme.tests.pki_prova import Pki
from tests.firme_prova import CF_PROVA, P7M, PKI, autorizzazione
from tests.infra_reale import richiede_pg, sql
from tests.supporto import client_app, dipendenze, intestazioni, lavora
from tests.test_r05_revisione2_scarto_incompleto import _lavora_finche
from tests.test_r05_revisione4_scarto import _bloccata, _difetti, _eliminati
from tests.test_r05_revisione_scarto import _ente_con_pda, _tr
from tests.test_r06_revisione_puntuali_autorizzazioni import _ambiente_con_vittima
from tests.test_r08_firme_ciclo_vita import _fino_ad_autorizzata

PARAMETRI = firme.ParametriFirme(firme.LivelloVerifica.CRITTOGRAFICA, False, False,
                                 frozenset({CF_PROVA}), frozenset({CF_PROVA}))
PDV = "6f1c2a8e-0000-4000-8000-000000000001"
ALTRO = "6f1c2a8e-0000-4000-8000-000000000002"
AMMESSI = frozenset({CF_PROVA})


async def _esito(contenuto: bytes, nome: str = "a.p7m") -> dict:
    return await firme.verifica_documento(contenuto, nome, firme.LivelloVerifica.CRITTOGRAFICA)


# ── unità (nessun database) ──────────────────────────────────────────

async def test_x1_x3_giudizio_del_documento() -> None:
    valida = await _esito(P7M)
    assert firme.problemi_documento(st.ALLEGATO_AUTORIZZAZIONE, valida, PARAMETRI) == ([], None)
    # X3: stessa CA, firma valida, titolare diverso (CF non in elenco) → rifiuto
    altro = Pki(PKI.ca_chiave, PKI.ca, *_firmatario(PKI, "Utente qualunque",
                                                      "QLNTNT80A01H501X"), PKI.adesso)
    estraneo = await _esito(altro.cades(b"Autorizzazione allo scarto"))
    assert estraneo["indicazione"] == "TOTAL_PASSED"
    problemi, _ = firme.problemi_documento(st.ALLEGATO_AUTORIZZAZIONE, estraneo, PARAMETRI)
    assert problemi and "firmatario non autorizzato" in problemi[0]
    problemi, _ = firme.problemi_documento(st.ALLEGATO_RICHIESTA_ANNULLAMENTO, estraneo,
                                           PARAMETRI)
    assert problemi
    # elenco vuoto: nessuno è autorizzato
    vuoto = firme.ParametriFirme(firme.LivelloVerifica.CRITTOGRAFICA, False, False)
    problemi, _ = firme.problemi_documento(st.ALLEGATO_AUTORIZZAZIONE, valida, vuoto)
    assert problemi and "elenco vuoto" in problemi[0]
    # impronta SHA-256 del certificato (anche nella forma «C-…» di EU DSS) al posto del CF
    impronta = valida["firme"][0]["certificato"]["impronta_sha256"]
    per_impronta = firme.da_valori({
        "CICLO_VITA_FIRME_LIVELLO": "CRITTOGRAFICA", "NULLA_OSTA_FIRMA": "AVVISO",
        "ANNULLAMENTO_RICHIESTA_FIRMATA": "FACOLTATIVA",
        "CICLO_VITA_FIRMATARI_AUTORIZZATI": ["C-" + impronta.upper()],
        "CICLO_VITA_FIRMATARI_NULLA_OSTA": []})
    assert firme.problemi_documento(st.ALLEGATO_AUTORIZZAZIONE, valida, per_impronta)[0] == []
    # nulla osta: firmatario non autorizzato segue NULLA_OSTA_FIRMA
    problemi, avviso = firme.problemi_documento(st.ALLEGATO_NULLA_OSTA, estraneo, PARAMETRI)
    assert problemi == [] and "firmatario non autorizzato" in (avviso or "")
    bloccante = firme.ParametriFirme(firme.LivelloVerifica.CRITTOGRAFICA, True, False,
                                     frozenset({CF_PROVA}), frozenset({CF_PROVA}))
    assert firme.problemi_documento(st.ALLEGATO_NULLA_OSTA, estraneo, bloccante)[0]
    # elenchi distinti: il dirigente dell'ente non firma il nulla osta della Soprintendenza
    distinti = firme.ParametriFirme(firme.LivelloVerifica.CRITTOGRAFICA, True, False,
                                    frozenset({CF_PROVA}), frozenset({"SPRNTN80A01H501Q"}))
    assert firme.problemi_documento(st.ALLEGATO_NULLA_OSTA, valida, distinti)[0]
    assert firme.problemi_documento(st.ALLEGATO_AUTORIZZAZIONE, valida, distinti)[0] == []
    # X1: certificato scaduto della CA fidata → INDETERMINATE → mai valida
    scaduto = Pki(PKI.ca_chiave, PKI.ca, *_firmatario(PKI, "Dirigente di prova", CF_PROVA,
                                                        scaduto=True), PKI.adesso)
    esito = await _esito(scaduto.cades(b"Autorizzazione"))
    assert esito["indicazione"] != "TOTAL_PASSED"
    assert firme.problemi_documento(st.ALLEGATO_AUTORIZZAZIONE, esito, PARAMETRI)[0]


def _firmatario(pki: Pki, cn: str, cf: str, *, scaduto: bool = False):  # type: ignore[no-untyped-def]
    """Chiave e certificato di un altro titolare emesso dalla STESSA CA di prova."""
    from datetime import timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    from libs.shared.firme.tests.pki_prova import _nome

    chiave = ec.generate_private_key(ec.SECP256R1())
    fine = pki.adesso - timedelta(days=1) if scaduto else pki.adesso + timedelta(days=365)
    cert = (x509.CertificateBuilder().subject_name(_nome(cn, cf)).issuer_name(pki.ca.subject)
            .public_key(chiave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(pki.adesso - timedelta(days=10)).not_valid_after(fine)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False,
                                         decipher_only=False), critical=True)
            .sign(pki.ca_chiave, hashes.SHA256()))
    return chiave, cert


def _aggiornamento_incrementale(pdf: bytes, testo: str) -> bytes:
    import io

    from pyhanko.pdf_utils import generic
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    w = IncrementalPdfFileWriter(io.BytesIO(pdf))
    w.root["/VDNota"] = generic.ByteStringObject(testo.encode("ascii"))
    w.update_root()
    uscita = io.BytesIO()
    w.write(uscita)
    return uscita.getvalue()


def test_x12_riferimento_solo_nel_contenuto_firmato() -> None:
    pdf = PKI.pades(f"Si chiede l'annullamento del versamento {PDV}")
    assert firme.contiene_riferimento(pdf, PDV, ammessi=AMMESSI)
    assert not firme.contiene_riferimento(pdf, ALTRO, ammessi=AMMESSI)
    # aggiornamento incrementale (vero, pyHanko) DOPO la firma che cita un altro versamento
    aggiunto = _aggiornamento_incrementale(pdf, ALTRO)
    assert len(aggiunto) > len(pdf)  # la revisione firmata non copre più tutto il file
    assert not firme.contiene_riferimento(aggiunto, ALTRO, ammessi=AMMESSI)
    # modificato dopo la firma
    assert not firme.contiene_riferimento(aggiunto, PDV, ammessi=AMMESSI)
    # testo in chiaro accodato dopo la firma (non coperto dal /ByteRange)
    accodato = pdf + f"\n% {ALTRO}\n".encode()
    assert not firme.contiene_riferimento(accodato, ALTRO, ammessi=AMMESSI)
    # PDF non firmato che cita il versamento: non vale
    assert not firme.contiene_riferimento(
        f"%PDF-1.7\n({PDV})\n%%EOF".encode(), PDV, ammessi=AMMESSI)
    # CAdES: solo il contenuto incapsulato
    assert firme.contiene_riferimento(PKI.cades(f"annullamento {PDV}".encode()), PDV,
                                      ammessi=AMMESSI)
    assert not firme.contiene_riferimento(PKI.cades(b"annullamento") + PDV.encode(), PDV,
                                          ammessi=AMMESSI)


async def test_x14_firma_staccata_esito_esplicito() -> None:
    p7s = PKI.cades(b"Autorizzazione", staccata=True)
    esito = await _esito(p7s, "autorizzazione.pdf.p7s")
    problemi, _ = firme.problemi_documento(st.ALLEGATO_AUTORIZZAZIONE, esito, PARAMETRI)
    assert problemi and "firma staccata non verificabile" in problemi[0]


# ── integrazione: il lavoratore ricontrolla il firmatario dal WORM ─────

@pytest.mark.integration
@richiede_pg
async def test_x3_lavoratore_blocca_firmatario_tolto_dall_elenco(db_url, s3_ciclo) -> None:
    sc = await _ente_con_pda(db_url, s3_ciclo, 1)
    dip = dipendenze(db_url)
    async with client_app() as c:
        pid, aut = await _fino_ad_autorizzata(c, sc, autorizzazione)
        assert aut["status"] == 201, aut
        assert (await _tr(c, pid, sc["admin"], "PROPOSTA", "AUTORIZZATA_PRODUTTORE",
                          [aut["id"]])).status_code == 200
        assert (await _tr(c, pid, sc["rsc"], "AUTORIZZATA_PRODUTTORE",
                          "ESEGUIBILE")).status_code == 200
        # il conservatore toglie il firmatario dall'elenco dell'ente
        await sql(db_url, "UPDATE public.impostazioni_valori SET valore = '[]'::jsonb WHERE "
                          "tenant_id = $1 AND codice = 'CICLO_VITA_FIRMATARI_AUTORIZZATI'",
                  sc["tenant"])
        assert (await _tr(c, pid, sc["rsc"], "ESEGUIBILE", "ESEGUITA")).status_code == 202
        await _lavora_finche(db_url, dip, pid)
    difetti = _difetti(await _bloccata(db_url, pid))
    assert "ALLEGATO_FIRMA_NON_VALIDA" in difetti and "firmatario non autorizzato" in difetti
    assert await _eliminati(db_url) == []


@pytest.mark.integration
@richiede_pg
async def test_x3_x12_annullamento_api(db_url, s3_ciclo) -> None:
    a = await _ambiente_con_vittima(db_url, s3_ciclo)
    chi = intestazioni(a["tenant"], "ADMIN_TENANT", a["admin"])
    corpo = {"pdv_id": str(a["pdv"]), "motivo": "Documento versato per errore"}

    def documento(contenuto: bytes, nome: str = "richiesta.pdf") -> dict:
        return {"nome_file": nome, "contenuto": base64.b64encode(contenuto).decode()}

    legittima_altro = PKI.pades(f"Annullamento del versamento {a['pdv_v']}")
    riusata = legittima_altro + f"\n9 0 obj\n<< /C ({a['pdv']}) >>\nendobj\n%%EOF\n".encode()
    altro = Pki(PKI.ca_chiave, PKI.ca, *_firmatario(PKI, "Utente qualunque",
                                                      "QLNTNT80A01H501X"), PKI.adesso)
    async with client_app() as c:
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi, json={
            **corpo, "documento_firmato": documento(riusata)})
        assert r.status_code == 422, r.text
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi, json={
            **corpo, "documento_firmato": documento(
                altro.cades(f"annullamento {a['pdv']}".encode()), "r.p7m")})
        assert r.status_code == 422 and "firmatario non autorizzato" in r.json()["detail"]
        r = await c.post("/api/v1/ciclo-vita/pratiche/annullamento", headers=chi, json={
            **corpo, "documento_firmato": documento(
                PKI.pades(f"Annullamento del versamento {a['pdv']}"))})
        assert r.status_code == 201, r.text
    await lavora(dipendenze(db_url))


# ── revisione Y2: riferimento legato alla firma del firmatario autorizzato ──

def _seconda_firma(pdf: bytes, firmatario: Pki, motivo: str) -> bytes:
    """Seconda firma PAdES (modifica ammessa) con il motivo in chiaro (/Reason
    letterale, come lo scriverebbe un qualunque altro strumento): prova
    ``r08/y/secondafirma.py`` della seconda revisione."""
    import asyncio
    import io

    import pyhanko.pdf_utils.generic as g
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    originale = g.TextStringObject.write_to_stream

    def letterale(self, stream, handler=None, container_ref=None):  # type: ignore[no-untyped-def]
        if str(self) == motivo:
            stream.write(b"(" + motivo.encode("ascii") + b")")
            return
        return originale(self, stream, handler, container_ref)

    g.TextStringObject.write_to_stream = letterale  # type: ignore[method-assign]
    try:
        uscita = io.BytesIO()
        meta = signers.PdfSignatureMetadata(field_name="Firma2", reason=motivo)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(1) as e:
            e.submit(asyncio.run, signers.PdfSigner(meta, signer=firmatario._signer())
                     .async_sign_pdf(IncrementalPdfFileWriter(io.BytesIO(pdf)),
                                     output=uscita)).result()
        return uscita.getvalue()
    finally:
        g.TextStringObject.write_to_stream = originale  # type: ignore[method-assign]


async def test_y2_seconda_firma_di_un_estraneo_non_lega_il_riferimento() -> None:
    autentico = PKI.pades(f"Si chiede l'annullamento del versamento {PDV}")
    altro = Pki(PKI.ca_chiave, PKI.ca, *_firmatario(PKI, "Utente qualunque",
                                                      "QLNTNT80A01H501X"), PKI.adesso)
    doc = _seconda_firma(autentico, altro, f"annullamento {ALTRO}")
    assert ALTRO.encode() in doc  # il riferimento c'è, in chiaro, nell'ultima revisione
    esito = await _esito(doc, "r.pdf")
    assert [f["indicazione"] for f in esito["firme"]] == ["TOTAL_PASSED", "TOTAL_PASSED"]
    problemi, _ = firme.problemi_documento(st.ALLEGATO_RICHIESTA_ANNULLAMENTO, esito, PARAMETRI)
    assert problemi and "Utente qualunque" in problemi[0]
    assert firme.problemi_documento(st.ALLEGATO_AUTORIZZAZIONE, esito, PARAMETRI)[0]
    assert not firme.contiene_riferimento(doc, ALTRO, ammessi=AMMESSI)
    assert not firme.contiene_riferimento(doc, PDV, ammessi=AMMESSI)
    # due firme entrambe autorizzate: valgono
    doppia = _seconda_firma(autentico, PKI, f"annullamento {PDV}")
    assert firme.contiene_riferimento(doppia, PDV, ammessi=AMMESSI)
    esito = await _esito(doppia, "r.pdf")
    assert firme.problemi_documento(st.ALLEGATO_RICHIESTA_ANNULLAMENTO, esito,
                                    PARAMETRI) == ([], None)
    # PDF firmato SOLO dall'estraneo: il riferimento non conta
    assert not firme.contiene_riferimento(altro.pades(f"annullamento {PDV}"), PDV,
                                          ammessi=AMMESSI)


def test_y2_cades_firmatario_e_buste_annidate() -> None:
    altro = Pki(PKI.ca_chiave, PKI.ca, *_firmatario(PKI, "Utente qualunque",
                                                      "QLNTNT80A01H501X"), PKI.adesso)
    assert not firme.contiene_riferimento(altro.cades(f"annullamento {PDV}".encode()), PDV,
                                          ammessi=AMMESSI)
    interna = PKI.cades(f"annullamento {PDV}".encode())
    # p7m autorizzato dentro un p7m firmato all'esterno (anche da un autorizzato): non vale
    assert not firme.contiene_riferimento(altro.cades(interna), PDV, ammessi=AMMESSI)
    assert not firme.contiene_riferimento(PKI.cades(interna), PDV, ammessi=AMMESSI)


@pytest.mark.parametrize("metodo", [12, 14], ids=["bzip2", "lzma"])
def test_y1_riversamento_rifiuta_membri_bzip2_lzma(metodo: int) -> None:
    """Revisione Y1: un PdD ricevuto con un membro bzip2/LZMA (bomba con
    dimensione dichiarata falsa) si respinge senza decomprimere."""
    import io
    import tracemalloc

    from app.application.riversamento import verifica_pdd

    from libs.shared.firme.tests.test_r08_revisione import bomba_compressa

    bomba = bomba_compressa(metodo, nome="documento.xml")
    tracemalloc.start()
    try:
        rapporto = verifica_pdd(io.BytesIO(bomba), dimensione_indice_max=1024 * 1024)
        _, picco = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert any("metodo di compressione" in r for r in rapporto["rilievi"]), rapporto
    assert picco < 8 * 1024 * 1024
