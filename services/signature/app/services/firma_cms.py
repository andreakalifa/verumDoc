# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/firma_cms.py
Firma CAdES/PAdES, marca RFC 3161 e verifica crittografica — VD-C-010.

Costruisce le buste con pyHanko (ETSI EN 319 122 CAdES, EN 319 142 PAdES)
delegando la sola operazione crittografica al FornitoreFirma e il token di
marca al FornitoreMarca. La verifica è crittografica: ricalcola l'impronta del
contenuto, verifica la firma con la chiave pubblica del certificato, costruisce
la catena verso le radici configurate e verifica il token di marca.
Una busta manipolata (contenuto, attributi o valore di firma) è «non_valida».

Limiti dichiarati in ogni esito di verifica:
  - stato di revoca (OCSP/CRL) non verificato;
  - Trusted List europea non integrata: la qualificazione è letta dal
    certificato (QcStatements), non verificata sulla lista.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from asn1crypto import algos, cms, x509
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import fields, signers
from pyhanko.sign.signers.pdf_cms import PdfCMSSignedAttributes, Signer
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko.sign.validation import async_validate_pdf_signature
from pyhanko.sign.validation.generic_cms import async_validate_detached_cms
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko_certvalidator.registry import SimpleCertificateStore

from app.services import formati_lt
from app.services.fornitori import (
    ErroreFirma,
    ErroreFornitore,
    FirmaNonConfigurata,
    FirmaProdottaNonValida,
    FornitoreFirma,
    FornitoreMarca,
    FornitoreNonRegistrato,
    FornitoreVietatoInProduzione,
    RichiestaNonValida,
    certificato_dichiara_qualificato,
)
from app.services.tsa_client import contesto_validazione, verifica_token

logger = structlog.get_logger(__name__)

AVVISO_SVILUPPO = (
    "FIRMA/MARCA DI SVILUPPO NON QUALIFICATA: chiave autogenerata, "
    "nessun valore legale. Non usare per documenti conservati."
)
#: R13a: livelli ETSI prodotti (B-B senza marca)
LIVELLI = ("B-B", "B-T", "B-LT", "B-LTA")
LIMITI_VERIFICA = [
    "stato di revoca dei certificati (OCSP/CRL) non verificato",
    "Trusted List europea non integrata: la qualificazione è quella dichiarata dal certificato",
]
FORMATI = ("cades", "cades_detached", "pades")
ALGORITMO = "sha256"


class _SignerAdattato(Signer):
    """Signer pyHanko che delega la firma crittografica al fornitore."""

    def __init__(
        self,
        fornitore: FornitoreFirma,
        certificato: x509.Certificate,
        catena: list[x509.Certificate],
        meccanismo,
        lunghezza: int,
    ) -> None:  # type: ignore[no-untyped-def]
        super().__init__(
            signing_cert=certificato,
            cert_registry=SimpleCertificateStore.from_certs(catena),
            signature_mechanism=meccanismo,
            embed_roots=True,
        )
        self._fornitore = fornitore
        self._lunghezza = lunghezza

    async def async_sign_raw(
        self, data: bytes, digest_algorithm: str, dry_run: bool = False
    ) -> bytes:
        if dry_run:
            return bytes(self._lunghezza)
        return await self._fornitore.firma_dati(data, digest_algorithm)


class _TimeStamperAdattato(TimeStamper):
    """TimeStamper pyHanko che delega al FornitoreMarca."""

    def __init__(self, fornitore: FornitoreMarca) -> None:
        super().__init__(include_nonce=True)
        self._fornitore = fornitore

    async def async_timestamp(self, message_digest, md_algorithm) -> cms.ContentInfo:  # type: ignore[no-untyped-def]
        return await self._fornitore.marca(message_digest, md_algorithm)


@dataclass
class EsitoFirma:
    busta: bytes
    formato: str
    contenuto_incluso: bool
    livello: str
    fornitore_firma: str
    fornitore_marca: str | None
    qualificata: bool
    firmatario: str
    data_marca: str | None
    impronta_sha256_contenuto: str
    avvisi: list[str] = field(default_factory=list)
    # R13a: dati per la rimarcatura e per il registro dei documenti firmati
    codice_fornitore: str = ""
    algoritmo: str = ALGORITMO
    certificato_sha256: str = ""
    certificato_scadenza: str | None = None
    marca_scadenza: str | None = None
    verificata: bool = False


def _nome_soggetto(cert: x509.Certificate) -> str:
    return cert.subject.human_friendly


def decodifica_busta(dati: bytes) -> bytes:
    """Accetta DER, PEM («-----BEGIN PKCS7-----») o base64 di un DER."""
    testo = dati.strip()
    if testo.startswith(b"-----BEGIN"):
        righe = [r for r in testo.splitlines() if not r.startswith(b"-----")]
        testo = b"".join(righe)
        return base64.b64decode(testo, validate=False)
    if testo[:1] == b"\x30":
        return dati
    try:
        return base64.b64decode(testo, validate=True)
    except (binascii.Error, ValueError):
        return dati


class ServizioFirma:
    """Firma, marca e verifica con i fornitori configurati.

    R13a: più fornitori di firma in ordine di priorità con ripiego (un errore del
    fornitore o una firma prodotta che non supera la verifica fa passare al
    successivo), livelli B-T, B-LT e B-LTA, verifica della firma prodotta con il
    verificatore di R08 (pyHanko) prima di restituirla, rimarcatura."""

    def __init__(
        self,
        fornitore_firma: FornitoreFirma | list[FornitoreFirma] | None,
        fornitore_marca: FornitoreMarca | None,
        *,
        produzione: bool,
        radici_firma: list[x509.Certificate] | None = None,
        radici_marca: list[x509.Certificate] | None = None,
        radici_sviluppo: list[x509.Certificate] | None = None,
        trasporto_revoca: Any = None,
        configurazione: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(fornitore_firma, list):
            self.fornitori_firma = list(fornitore_firma)
        else:
            self.fornitori_firma = [fornitore_firma] if fornitore_firma is not None else []
        self.fornitore_firma = self.fornitori_firma[0] if self.fornitori_firma else None
        self.fornitore_marca = fornitore_marca
        self.produzione = produzione
        self._radici_firma = list(radici_firma or [])
        self._radici_marca = list(radici_marca or [])
        self._radici_sviluppo = list(radici_sviluppo or [])
        self._timestamper = _TimeStamperAdattato(fornitore_marca) if fornitore_marca else None
        self._trasporto_revoca = trasporto_revoca
        #: impronta e descrizione della configurazione da cui è nato (stato, console)
        self.configurazione = dict(configurazione or {})
        self._cache_tsa: list[x509.Certificate] | None = None

    # ── controlli ─────────────────────────────────────────────────────

    def _controlla_fornitore(self, f: FornitoreFirma) -> FornitoreFirma:
        if f.di_sviluppo and self.produzione:
            raise FornitoreVietatoInProduzione(
                "l'adattatore di firma di sviluppo è vietato in produzione",
            )
        return f

    def _controlla_firma(self) -> FornitoreFirma:
        f = self.fornitore_firma
        if f is None:
            raise FirmaNonConfigurata(
                "firma qualificata: non configurata (fornitore da scegliere, decisione VD-P-05)",
            )
        return self._controlla_fornitore(f)

    def _controlla_marca(self) -> FornitoreMarca:
        m = self.fornitore_marca
        if m is None:
            raise FirmaNonConfigurata(
                "marca temporale qualificata: non configurata "
                "(TSA da scegliere, decisione VD-P-05)",
            )
        if m.di_sviluppo and self.produzione:
            raise FornitoreVietatoInProduzione(
                "l'adattatore di marca di sviluppo è vietato in produzione",
            )
        return m

    def fornitore(self, codice: str) -> FornitoreFirma | None:
        return next((f for f in self.fornitori_firma if f.codice == codice), None)

    # ── firma ─────────────────────────────────────────────────────────

    async def firma(
        self,
        contenuto: bytes,
        *,
        formato: str = "cades",
        con_marca: bool = True,
        adesso: datetime,
        livello: str | None = None,
        algoritmo: str = ALGORITMO,
        solo_fornitore: str | None = None,
    ) -> EsitoFirma:
        """Firma `contenuto` in CAdES (attached/detached) o PAdES, B-B, B-T, B-LT o B-LTA.

        I fornitori si provano in ordine di priorità: un errore del fornitore o una
        firma che non supera la verifica fa passare al successivo; se nessuno
        riesce, l'ultimo errore. ``solo_fornitore``: solo quel codice (prova)."""
        if formato not in FORMATI:
            raise RichiestaNonValida(
                f"formato non supportato: {formato}", {"ammessi": list(FORMATI)}
            )
        if algoritmo not in ("sha256", "sha384", "sha512"):
            raise RichiestaNonValida(f"algoritmo non supportato: {algoritmo}")
        livello = livello or ("B-T" if con_marca else "B-B")
        if livello not in LIVELLI:
            raise RichiestaNonValida(f"livello non supportato: {livello}",
                                     {"ammessi": list(LIVELLI)})
        con_marca = livello != "B-B"
        if formato == "pades" and not contenuto.startswith(b"%PDF-"):
            raise RichiestaNonValida("PAdES richiede un documento PDF")
        candidati = ([f for f in self.fornitori_firma if f.codice == solo_fornitore]
                     if solo_fornitore else list(self.fornitori_firma))
        if not candidati:
            if solo_fornitore:
                raise FornitoreNonRegistrato(f"fornitore di firma {solo_fornitore} non attivo")
            self._controlla_firma()
        if con_marca:
            self._controlla_marca()
        ultimo: ErroreFirma | None = None
        for fornitore in candidati:
            self._controlla_fornitore(fornitore)
            try:
                return await self._firma_con(fornitore, contenuto, formato=formato,
                                             livello=livello, adesso=adesso,
                                             algoritmo=algoritmo)
            except (ErroreFornitore, FirmaProdottaNonValida) as exc:
                logger.error("firma_fornitore_scartato", fornitore=fornitore.codice or
                             fornitore.nome, codice_errore=exc.codice,
                             motivo=exc.messaggio[:200])
                ultimo = exc
        assert ultimo is not None
        raise ultimo

    def _radici_firmatario(self, fornitore: FornitoreFirma) -> list[x509.Certificate]:
        """Radici con cui si verifica il FIRMATARIO (mai quelle delle TSA)."""
        return (list(self._radici_firma) + fornitore.radici()
                + (list(self._radici_sviluppo) if fornitore.di_sviluppo else []))

    def _radici_per(self, fornitore: FornitoreFirma) -> list[x509.Certificate]:
        """Radici del firmatario e delle TSA (validazione per B-LT e catene)."""
        return self._radici_firmatario(fornitore) + self._radici_token()

    async def _certificati_tsa(self) -> list[x509.Certificate]:
        """Certificati della TSA (PAdES B-LT: le revoche servono prima di firmare):
        una marca di sonda, poi in cache."""
        if self._cache_tsa is None:
            assert self.fornitore_marca is not None
            token = await self.fornitore_marca.marca(hashlib.sha256(b"vd-sonda-tsa").digest(),
                                                     "sha256")
            self._cache_tsa = [s.chosen for s in (token["content"]["certificates"] or [])
                               if isinstance(s.chosen, x509.Certificate)]
        return list(self._cache_tsa)

    async def _firma_con(self, fornitore: FornitoreFirma, contenuto: bytes, *, formato: str,
                         livello: str, adesso: datetime, algoritmo: str) -> EsitoFirma:
        mat = await fornitore.materiale()
        fornitore.identita.controlla(mat.certificato)  # revisione R13a (4)
        # fornitori del registro (codice): in produzione l'identità è obbligatoria
        # (la configurazione la esige già; secondo controllo qui)
        if self.produzione and fornitore.codice and not fornitore.di_sviluppo \
                and fornitore.identita.vuota:
            raise FirmaProdottaNonValida("in produzione serve l'identità attesa del "
                                         "certificato del fornitore")
        meccanismo = algos.SignedDigestAlgorithm({"algorithm": (
            f"{algoritmo}_ecdsa" if mat.certificato.public_key.algorithm == "ec"
            else f"{algoritmo}_rsa")})
        signer = _SignerAdattato(fornitore, mat.certificato, mat.catena, meccanismo,
                                 await fornitore.lunghezza_firma_stimata())
        timestamper = self._timestamper if livello != "B-B" else None
        lt = livello in ("B-LT", "B-LTA")
        crl_der: list[bytes] = []
        ocsp_der: list[bytes] = []
        catena_completa = [mat.certificato, *mat.catena, *self._radici_per(fornitore)]
        if formato == "pades":
            try:
                writer = IncrementalPdfFileWriter(io.BytesIO(contenuto))
            except Exception as exc:  # noqa: BLE001 — PDF illeggibile = richiesta non valida
                raise RichiestaNonValida(f"PDF non leggibile: {type(exc).__name__}") from exc
            vc = None
            if lt:
                catena_completa += await self._certificati_tsa()
                crl_der, ocsp_der = await self._revoche(fornitore, catena_completa, adesso)
                vc = self._contesto_revoche(self._radici_per(fornitore), catena_completa,
                                            crl_der, ocsp_der)
            meta = signers.PdfSignatureMetadata(
                field_name="FirmaConservatore",
                subfilter=fields.SigSeedSubFilter.PADES,
                md_algorithm=algoritmo,
                embed_validation_info=lt,
                validation_context=vc,
                use_pades_lta=livello == "B-LTA",
            )
            try:
                uscita = await signers.PdfSigner(
                    meta, signer=signer, timestamper=timestamper
                ).async_sign_pdf(writer)
            except ErroreFirma:
                raise
            except Exception as exc:  # noqa: BLE001 — revoche o catena non utilizzabili
                raise ErroreFornitore(f"busta PAdES {livello} non costruibile: "
                                      f"{type(exc).__name__}", {"servizio": "pades"}) from exc
            busta = uscita.getvalue()
            sd = PdfFileReader(io.BytesIO(busta)).embedded_signatures[0].signed_data
            incluso = True
        else:
            incluso = formato == "cades"
            ci = await signer.async_sign_general_data(
                contenuto,
                algoritmo,
                detached=not incluso,
                use_cades=True,
                timestamper=timestamper,
                signed_attr_settings=PdfCMSSignedAttributes(signing_time=adesso),
            )
            if lt:
                certificati = [*catena_completa, *_certificati_marche(ci)]
                crl_der, ocsp_der = await self._revoche(fornitore, certificati, adesso)
                ci = formati_lt.aggiungi_validazione(ci, certificati, crl_der, ocsp_der)
            if livello == "B-LTA":
                ci = await formati_lt.aggiungi_ats_v3(ci, contenuto, algoritmo,
                                                     self._marca_verificata)
            busta = ci.dump()
            sd = ci["content"]
        data_marca = _data_marca_in_firma(sd)
        await self._verifica_prodotta(fornitore, mat, busta, contenuto, formato=formato,
                                      livello=livello, crl_der=crl_der, ocsp_der=ocsp_der)
        avvisi: list[str] = []
        sviluppo = fornitore.di_sviluppo or bool(
            self.fornitore_marca and livello != "B-B" and self.fornitore_marca.di_sviluppo
        )
        if sviluppo:
            avvisi.append(AVVISO_SVILUPPO)
        # Qualificata = certificato che dichiara QcCompliance, catena verso radici
        # configurate (verifica prodotta) e fornitore non di sviluppo.
        qualificata = (not fornitore.di_sviluppo) and mat.qualificato and bool(
            self._radici_firma + fornitore.radici() or not self.produzione)
        if not mat.qualificato:
            avvisi.append(
                "il certificato di firma non dichiara QcCompliance: firma non qualificata"
            )
        if livello == "B-B":
            avvisi.append("firma senza marca temporale (livello B-B)")
        logger.info(
            "firma_apposta",
            formato=formato,
            fornitore=fornitore.codice or fornitore.nome,
            livello=livello,
            qualificata=qualificata,
            impronta=hashlib.sha256(contenuto).hexdigest()[:16],
        )
        return EsitoFirma(
            busta=busta,
            formato="PAdES" if formato == "pades" else "CAdES",
            contenuto_incluso=incluso,
            livello=livello,
            fornitore_firma=fornitore.nome,
            fornitore_marca=self.fornitore_marca.codice or self.fornitore_marca.nome
            if timestamper and self.fornitore_marca
            else None,
            qualificata=qualificata,
            firmatario=_nome_soggetto(mat.certificato),
            data_marca=data_marca,
            impronta_sha256_contenuto=hashlib.sha256(contenuto).hexdigest(),
            avvisi=avvisi,
            codice_fornitore=fornitore.codice,
            algoritmo=algoritmo,
            certificato_sha256=hashlib.sha256(mat.certificato.dump()).hexdigest(),
            certificato_scadenza=_scadenza(mat.certificato),
            marca_scadenza=_scadenza_ultima_marca(sd, busta if formato == "pades" else None),
            verificata=True,
        )

    async def _marca_verificata(self, impronta: bytes, algoritmo: str) -> cms.ContentInfo:
        token, esito = await self.marca(impronta, algoritmo)
        if not esito.get("valida"):
            raise FirmaProdottaNonValida("marca d'archivio non valida")
        return token

    async def _revoche(self, fornitore: FornitoreFirma, certificati: list[x509.Certificate],
                       adesso: datetime) -> tuple[list[bytes], list[bytes]]:
        """CRL e OCSP per il livello LT: prima quelli del fornitore e della TSA senza
        rete, poi dagli indirizzi dei certificati."""
        from app.services import revoca

        crl_f, ocsp_f = await fornitore.revoca_locale(adesso)
        crl_m, ocsp_m = ((await self.fornitore_marca.revoca_locale(adesso))
                         if self.fornitore_marca else ([], []))
        return await revoca.recupera(certificati, gia_disponibili=(crl_f + crl_m,
                                                                   ocsp_f + ocsp_m),
                                     trasporto=self._trasporto_revoca)

    @staticmethod
    def _contesto_revoche(radici: list[x509.Certificate], altri: list[x509.Certificate],
                          crl_der: list[bytes], ocsp_der: list[bytes]) -> Any:
        from asn1crypto import crl as asn1_crl
        from asn1crypto import ocsp as asn1_ocsp
        from pyhanko_certvalidator import ValidationContext

        return ValidationContext(
            trust_roots=radici, other_certs=altri, allow_fetching=False,
            revocation_mode="require",
            crls=[asn1_crl.CertificateList.load(d) for d in crl_der],
            ocsps=[asn1_ocsp.OCSPResponse.load(d) for d in ocsp_der])

    async def _verifica_prodotta(self, fornitore: FornitoreFirma, mat: Any, busta: bytes,
                                 contenuto: bytes, *, formato: str, livello: str,
                                 crl_der: list[bytes], ocsp_der: list[bytes]) -> None:
        """R13a: la firma prodotta si verifica con il verificatore di R08 PRIMA di
        restituirla. Fallisce chiuso: firma non integra, certificato diverso da
        quello del fornitore, catena non attendibile quando ci sono radici (in
        produzione sempre), marca non valida, revoche assenti per B-LT, marca
        d'archivio non verificata per B-LTA."""
        from libs.shared.firme.esito import Indicazione, LivelloVerifica, StatoRevoca
        from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko

        radici = self._radici_firmatario(fornitore)
        con_radici = bool(self._radici_firma or fornitore.radici() or fornitore.di_sviluppo)
        if self.produzione and not con_radici:
            raise FirmaProdottaNonValida(
                "in produzione la firma si verifica solo con le radici del prestatore: "
                "configurarle in console (radici_pem)")
        lt = livello in ("B-LT", "B-LTA")
        verificatore = VerificatorePyHanko(radici, crl_locali=crl_der, ocsp_locali=ocsp_der,
                                           processo_separato=False)
        esito = await verificatore.verifica(
            busta, livello=LivelloVerifica.CON_REVOCHE if lt else LivelloVerifica.CRITTOGRAFICA,
            nome_file="firma.pdf" if formato == "pades" else "firma.p7m",
            originale=None if formato != "cades_detached" else contenuto)
        if not esito.firme:
            raise FirmaProdottaNonValida("firma prodotta non leggibile dal verificatore")
        firma = esito.firme[0] if formato != "pades" else esito.firme[0]
        if not firma.crittografia_valida:
            raise FirmaProdottaNonValida("la firma prodotta non è crittograficamente valida")
        atteso = hashlib.sha256(mat.certificato.dump()).hexdigest()
        if firma.certificato is None or firma.certificato.impronta_sha256 != atteso:
            raise FirmaProdottaNonValida("la firma prodotta porta un certificato diverso da "
                                         "quello del fornitore")
        # revisione R13a (4): ed è il certificato ATTESO (impronta o organizationIdentifier)
        fornitore.identita.controlla(mat.certificato)
        if con_radici and firma.indicazione is not Indicazione.TOTAL_PASSED:
            raise FirmaProdottaNonValida(
                "la firma prodotta non supera la verifica: "
                + "; ".join(firma.motivi[:3]) if firma.motivi else
                "la firma prodotta non supera la verifica")
        if lt and firma.revoca is not StatoRevoca.BUONO:
            raise FirmaProdottaNonValida("B-LT: stato di revoca del firmatario non "
                                         "verificabile con le informazioni incluse")
        if formato != "pades" and livello != "B-B":
            await self._verifica_marche_cades(busta, contenuto, livello)

    async def _verifica_marche_cades(self, busta: bytes, contenuto: bytes, livello: str) -> None:
        ci = cms.ContentInfo.load(busta)
        si = ci["content"]["signer_infos"][0]
        tst = None
        for attr in si["unsigned_attrs"] or []:
            if attr["type"].native == "signature_time_stamp_token":
                tst = attr["values"][0]
        if tst is None:
            raise FirmaProdottaNonValida("marca temporale della firma assente")
        alg = tst["content"]["encap_content_info"]["content"].parsed[
            "message_imprint"]["hash_algorithm"]["algorithm"].native
        esito = await verifica_token(tst, hashlib.new(alg, si["signature"].native).digest(),
                                     self._radici_token())
        if not esito["valida"] or (self._radici_token() and not esito["attendibile"]):
            raise FirmaProdottaNonValida("marca temporale della firma non valida")
        if livello == "B-LTA" or formati_lt.marche_archivio(ci):
            esiti = formati_lt.verifica_ats_v3(ci, contenuto)
            if not esiti or not all(e["ok"] for e in esiti):
                raise FirmaProdottaNonValida("marca d'archivio (archive-time-stamp-v3) non "
                                             "verificata")
            for e in esiti:
                ver = await verifica_token(e["token"], e["impronta"], self._radici_token())
                if not ver["valida"]:
                    raise FirmaProdottaNonValida("firma della marca d'archivio non valida")

    # ── rimarcatura ───────────────────────────────────────────────────

    async def rimarca(self, busta: bytes, contenuto: bytes | None, *, adesso: datetime,
                      algoritmo: str = ALGORITMO) -> EsitoFirma:
        """Nuova marca d'archivio su una busta già firmata (prima della scadenza dei
        certificati o di un algoritmo): CAdES → archive-time-stamp-v3 (con le
        informazioni di revoca della TSA precedente), PAdES → document timestamp."""
        marca = self._controlla_marca()
        if busta.lstrip()[:5] == b"%PDF-":
            from pyhanko.sign.signers.pdf_signer import PdfTimeStamper

            certificati = _certificati_pdf(busta)
            crl_der, ocsp_der = await revoca_recupera(certificati, marca, adesso,
                                                      self._trasporto_revoca)
            vc = self._contesto_revoche(self._radici_token(), certificati, crl_der, ocsp_der)
            try:
                aggiornatore = PdfTimeStamper(self._timestamper)
                uscita = await aggiornatore.async_update_archival_timestamp_chain(
                    PdfFileReader(io.BytesIO(busta)), vc, in_place=False)
            except Exception as exc:  # noqa: BLE001
                raise ErroreFornitore(f"rimarcatura PAdES non riuscita: {type(exc).__name__}",
                                      {"servizio": "pades"}) from exc
            nuova = uscita.getvalue()
            esito = await self.verifica(nuova)
            if esito["esito"] == "non_valida":
                raise FirmaProdottaNonValida("busta rimarcata non valida")
            sd = PdfFileReader(io.BytesIO(nuova)).embedded_signatures[0].signed_data
            return self._esito_rimarca(nuova, sd, "PAdES", True, contenuto or b"", algoritmo,
                                       nuova)
        ci = cms.ContentInfo.load(decodifica_busta(busta))
        sd = ci["content"]
        incluso = sd["encap_content_info"]["content"].native is not None
        dati = sd["encap_content_info"]["content"].native if incluso else contenuto
        if dati is None:
            raise RichiestaNonValida("rimarcatura di una firma staccata: serve il contenuto")
        certificati = _certificati_marche(ci)
        crl_der, ocsp_der = await revoca_recupera(certificati, marca, adesso,
                                                  self._trasporto_revoca)
        ci = formati_lt.aggiungi_validazione(ci, certificati, crl_der, ocsp_der)
        ci = await formati_lt.aggiungi_ats_v3(ci, dati, algoritmo, self._marca_verificata)
        nuova = ci.dump()
        await self._verifica_marche_cades(nuova, dati, "B-LTA")
        esito = await self.verifica(nuova, None if incluso else dati)
        if esito["esito"] == "non_valida":
            raise FirmaProdottaNonValida("busta rimarcata non valida: "
                                         + "; ".join(esito["motivi"][:3]))
        return self._esito_rimarca(nuova, ci["content"], "CAdES", incluso, dati, algoritmo,
                                   None)

    def _esito_rimarca(self, busta: bytes, sd: cms.SignedData, formato: str, incluso: bool,
                       contenuto: bytes, algoritmo: str, pdf: bytes | None) -> EsitoFirma:
        cert = _certificato_firmatario(sd)
        return EsitoFirma(
            busta=busta, formato=formato, contenuto_incluso=incluso, livello="B-LTA",
            fornitore_firma="", qualificata=False,
            fornitore_marca=(self.fornitore_marca.codice or self.fornitore_marca.nome)
            if self.fornitore_marca else None,
            firmatario=_nome_soggetto(cert) if cert else "",
            data_marca=_data_marca_in_firma(sd),
            impronta_sha256_contenuto=hashlib.sha256(contenuto).hexdigest(),
            algoritmo=algoritmo,
            certificato_sha256=hashlib.sha256(cert.dump()).hexdigest() if cert else "",
            certificato_scadenza=_scadenza(cert) if cert else None,
            marca_scadenza=_scadenza_ultima_marca(sd, pdf), verificata=True)

    # ── marca ─────────────────────────────────────────────────────────

    async def marca(
        self, impronta: bytes, algoritmo: str = ALGORITMO
    ) -> tuple[cms.ContentInfo, dict]:
        """Marca temporale RFC 3161 su un'impronta; restituisce token ed esito di verifica."""
        if len(impronta) != hashlib.new(algoritmo).digest_size:
            raise RichiestaNonValida("lunghezza dell'impronta non coerente con l'algoritmo")
        fornitore = self._controlla_marca()
        token = await fornitore.marca(impronta, algoritmo)
        esito = await verifica_token(token, impronta, self._radici_token())
        esito["di_sviluppo"] = fornitore.di_sviluppo
        return token, esito

    async def verifica_marca(self, token_der: bytes, impronta: bytes) -> dict:
        try:
            token = cms.ContentInfo.load(decodifica_busta(token_der))
            _ = token.native
        except Exception as exc:  # noqa: BLE001 — token illeggibile = non valido
            return {
                "valida": False,
                "motivo": f"token non decodificabile: {type(exc).__name__}",
                "limiti": LIMITI_VERIFICA,
            }
        esito = await verifica_token(token, impronta, self._radici_token())
        esito["limiti"] = LIMITI_VERIFICA
        return esito

    def _radici_token(self) -> list[x509.Certificate]:
        radici = list(self._radici_marca) + list(self._radici_sviluppo)
        if self.fornitore_marca:
            radici += self.fornitore_marca.radici_attendibili()
        return radici

    def _radici_verifica(self) -> list[x509.Certificate]:
        radici = list(self._radici_firma) + list(self._radici_sviluppo)
        for f in self.fornitori_firma:  # R13a: radici dei fornitori registrati
            radici += f.radici()
        return radici

    # ── verifica ──────────────────────────────────────────────────────

    async def verifica(self, busta: bytes, contenuto: bytes | None = None) -> dict:
        """Verifica crittografica di una busta CAdES (.p7m attached/detached) o PAdES."""
        if busta.lstrip()[:5] == b"%PDF-":
            return await self._verifica_pdf(busta)
        der = decodifica_busta(busta)
        try:
            ci = cms.ContentInfo.load(der)
            if ci["content_type"].native != "signed_data":
                raise ValueError("non è un SignedData")
            sd = ci["content"]
            _ = sd.native  # forza il parsing completo: rileva DER corrotti
        except Exception as exc:  # noqa: BLE001 — qualunque busta illeggibile è non valida
            return _esito_non_valido("CAdES", f"busta non decodificabile: {type(exc).__name__}")
        if len(sd["signer_infos"]) != 1:
            return _esito_non_valido("CAdES", "sono supportate solo buste con un firmatario")
        incapsulato = sd["encap_content_info"]["content"]
        if incapsulato.native is not None:
            dati = incapsulato.native
            if contenuto is not None and contenuto != dati:
                return _esito_non_valido(
                    "CAdES", "il contenuto fornito è diverso da quello incluso nella busta"
                )
            eci = sd["encap_content_info"]
            sd = cms.SignedData(
                {
                    **{
                        k: sd[k]
                        for k in ("version", "digest_algorithms", "certificates", "signer_infos")
                    },
                    "encap_content_info": {"content_type": eci["content_type"].native},
                }
            )
        else:
            if contenuto is None:
                return _esito_non_valido("CAdES", "firma detached: serve il contenuto originale")
            dati = contenuto
        vc = contesto_validazione(self._radici_verifica())
        tvc = contesto_validazione(self._radici_token())
        try:
            stato = await async_validate_detached_cms(
                dati,
                sd,
                signer_validation_context=vc,
                ts_validation_context=tvc,
            )
        except Exception as exc:  # noqa: BLE001 — errore strutturale = non valida
            return _esito_non_valido(
                "CAdES", f"struttura della firma non valida: {type(exc).__name__}"
            )
        return self._componi_esito("CAdES", stato, incapsulato.native is not None)

    async def _verifica_pdf(self, pdf: bytes) -> dict:
        try:
            lettore = PdfFileReader(io.BytesIO(pdf), strict=False)
            firme = lettore.embedded_signatures
        except Exception as exc:  # noqa: BLE001
            return _esito_non_valido("PAdES", f"PDF non leggibile: {type(exc).__name__}")
        if not firme:
            return _esito_non_valido("PAdES", "il PDF non contiene firme")
        vc = contesto_validazione(self._radici_verifica())
        tvc = contesto_validazione(self._radici_token())
        esiti = []
        # R13a: i timestamp di documento (PAdES B-LTA) sono marche, non firme
        marche_documento = [f for f in firme if f.sig_object.get("/Type") == "/DocTimeStamp"]
        firme = [f for f in firme if f.sig_object.get("/Type") != "/DocTimeStamp"]
        if not firme:
            return _esito_non_valido("PAdES", "il PDF non contiene firme")
        problemi_marche: list[str] = []
        for marca_doc in marche_documento:
            from pyhanko.sign.validation import async_validate_pdf_timestamp

            try:
                stato_m = await async_validate_pdf_timestamp(marca_doc, validation_context=tvc)
                if not (stato_m.intact and stato_m.valid):
                    problemi_marche.append("timestamp di documento non valido")
            except Exception as exc:  # noqa: BLE001
                problemi_marche.append(f"timestamp di documento non verificabile: "
                                       f"{type(exc).__name__}")
        for firma in firme:
            try:
                stato = await async_validate_pdf_signature(
                    firma,
                    signer_validation_context=vc,
                    ts_validation_context=tvc,
                )
            except Exception as exc:  # noqa: BLE001
                esiti.append(
                    _esito_non_valido("PAdES", f"firma PDF non verificabile: {type(exc).__name__}")
                )
                continue
            esito = self._componi_esito("PAdES", stato, True)
            if stato.coverage != SignatureCoverageLevel.ENTIRE_FILE:
                esito["motivi"].append("il PDF contiene modifiche successive alla firma")
                if esito["esito"] == "valida":
                    esito["esito"] = "indeterminata"
            esiti.append(esito)
        principale = dict(esiti[-1])
        ordine = {"non_valida": 0, "indeterminata": 1, "valida": 2}
        principale["esito"] = min((e["esito"] for e in esiti), key=lambda x: ordine[x])
        principale["numero_firme"] = len(esiti)
        principale["firme"] = esiti if len(esiti) > 1 else None
        principale["marche_documento"] = len(marche_documento)
        if problemi_marche:
            principale["motivi"] = list(principale["motivi"]) + problemi_marche
            principale["esito"] = "non_valida"
        elif marche_documento:
            principale["livello"] = "B-LTA"
        return principale

    def _componi_esito(self, formato: str, stato, contenuto_incluso: bool) -> dict:  # type: ignore[no-untyped-def]
        motivi: list[str] = []
        cert = stato.signing_cert
        sviluppo = any(cert.issuer == r.subject for r in self._radici_sviluppo)
        if not stato.intact:
            motivi.append("l'impronta del contenuto non coincide: contenuto o attributi modificati")
        if not stato.valid:
            motivi.append("la firma crittografica non è valida")
        if stato.intact and stato.valid and not stato.trusted:
            motivi.append("catena del certificato non riconducibile a una radice attendibile")
        marca = None
        ts = stato.timestamp_validity
        if ts is not None:
            marca = {
                "presente": True,
                "valida": bool(ts.intact and ts.valid),
                "attendibile": bool(ts.trusted),
                "data_marca": ts.timestamp.isoformat() if ts.timestamp else None,
            }
            if not marca["valida"]:
                motivi.append("la marca temporale non è valida")
        if not (stato.intact and stato.valid) or (marca and not marca["valida"]):
            esito = "non_valida"
        elif not stato.trusted or (marca and not marca["attendibile"]):
            esito = "indeterminata"
        else:
            esito = "valida"
        if sviluppo:
            motivi.append(
                "firma emessa dalla CA di SVILUPPO: non qualificata, nessun valore legale"
            )
        return {
            "esito": esito,
            "formato": formato,
            "livello": "B-T" if marca else "B-B",
            "contenuto_incluso": contenuto_incluso,
            "integra": bool(stato.intact),
            "firma_valida": bool(stato.valid),
            "catena_attendibile": bool(stato.trusted),
            "firmatario": _nome_soggetto(cert),
            # R16 (VD-C-166): la hashchain lega la busta alla credenziale della persona
            "certificato_sha256": hashlib.sha256(cert.dump()).hexdigest(),
            "certificato_dichiara_qualificato": certificato_dichiara_qualificato(cert),
            "firma_di_sviluppo": sviluppo,
            "data_firma_dichiarata": (
                stato.signer_reported_dt.isoformat()
                if getattr(stato, "signer_reported_dt", None)
                else None
            ),
            "marca": marca,
            "motivi": motivi,
            "limiti": LIMITI_VERIFICA,
        }


def _esito_non_valido(formato: str, motivo: str) -> dict:
    return {
        "esito": "non_valida",
        "formato": formato,
        "livello": None,
        "contenuto_incluso": None,
        "integra": False,
        "firma_valida": False,
        "catena_attendibile": False,
        "firmatario": None,
        "certificato_dichiara_qualificato": False,
        "firma_di_sviluppo": False,
        "data_firma_dichiarata": None,
        "marca": None,
        "motivi": [motivo],
        "limiti": LIMITI_VERIFICA,
    }


def _data_marca_in_firma(sd: cms.SignedData) -> str | None:
    """Legge la data del signature-time-stamp-token, se presente."""
    try:
        si = sd["signer_infos"][0]
        for attr in si["unsigned_attrs"] or []:
            if attr["type"].native == "signature_time_stamp_token":
                tst = attr["values"][0]["content"]["encap_content_info"]["content"].parsed
                return tst["gen_time"].native.isoformat()
    except (KeyError, IndexError, ValueError):
        return None
    return None


def _scadenza(cert: x509.Certificate) -> str:
    return cert["tbs_certificate"]["validity"]["not_after"].native.isoformat()


def _certificato_firmatario(sd: cms.SignedData) -> x509.Certificate | None:
    try:
        sid = sd["signer_infos"][0]["sid"]
        for scelta in sd["certificates"] or []:
            c = scelta.chosen
            if isinstance(c, x509.Certificate) and sid.name == "issuer_and_serial_number" \
                    and c.issuer == sid.chosen["issuer"] \
                    and c.serial_number == sid.chosen["serial_number"].native:
                return c
    except (KeyError, IndexError, ValueError):
        return None
    return None


def _token_marche(sd: cms.SignedData) -> list[cms.ContentInfo]:
    """Marca della firma e marche d'archivio (CAdES), nell'ordine."""
    token: list[cms.ContentInfo] = []
    for attr in sd["signer_infos"][0]["unsigned_attrs"] or []:
        if attr["type"].native == "signature_time_stamp_token" \
                or attr["type"].dotted == formati_lt.OID_ATS_V3:
            token.append(cms.ContentInfo.load(attr["values"][0].dump()))
    return token


def _certificati_marche(ci: cms.ContentInfo) -> list[x509.Certificate]:
    certificati: list[x509.Certificate] = []
    for token in _token_marche(ci["content"]):
        for scelta in token["content"]["certificates"] or []:
            if isinstance(scelta.chosen, x509.Certificate):
                certificati.append(scelta.chosen)
    return certificati


def _certificati_pdf(pdf: bytes) -> list[x509.Certificate]:
    certificati: list[x509.Certificate] = []
    for firma in PdfFileReader(io.BytesIO(pdf)).embedded_signatures:
        for scelta in firma.signed_data["certificates"] or []:
            if isinstance(scelta.chosen, x509.Certificate):
                certificati.append(scelta.chosen)
    return certificati


def _scadenza_ultima_marca(sd: cms.SignedData, pdf: bytes | None) -> str | None:
    """Scadenza del certificato della TSA dell'ULTIMA marca (firma, archivio o
    timestamp di documento PAdES): fissa quando va rifatta la marca."""
    token = _token_marche(sd)
    if pdf is not None:
        for firma in PdfFileReader(io.BytesIO(pdf)).embedded_signatures:
            if firma.sig_object.get("/Type") == "/DocTimeStamp":
                token.append(cms.ContentInfo({"content_type": "signed_data",
                                              "content": firma.signed_data}))
    if not token:
        return None
    from app.services.tsa_client import _certificato_tsa

    cert = _certificato_tsa(token[-1])
    return _scadenza(cert) if cert is not None else None


async def revoca_recupera(certificati: list[x509.Certificate], marca: FornitoreMarca,
                          adesso: datetime, trasporto: Any) -> tuple[list[bytes], list[bytes]]:
    from app.services import revoca

    return await revoca.recupera(certificati, gia_disponibili=await marca.revoca_locale(adesso),
                                 trasporto=trasporto)
