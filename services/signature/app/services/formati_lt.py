# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/formati_lt.py
CAdES-B-LT e CAdES-B-LTA (archive-time-stamp-v3) — R13a; ETSI EN 319 122-1 V1.1.1.

B-LT (§5.5.1-5.5.2): certificati della catena del firmatario e della TSA nel
campo ``certificates`` del SignedData e le informazioni di revoca nel campo
``crls`` (CRL come ``crl``, risposte OCSP come ``other`` con
``id-ri-ocsp-response``, RFC 5940). Aggiungerli non tocca la firma: gli
attributi firmati non li coprono.

B-LTA (§5.5.3): attributo non firmato ``archive-time-stamp-v3``
(0.4.0.1733.2.4), un TimeStampToken la cui impronta copre:
  a) ``encapContentInfo.eContentType`` (DER);
  b) l'impronta del contenuto firmato (contenuto incapsulato o esterno);
  c) i campi version, sid, digestAlgorithm, signedAttrs, signatureAlgorithm e
     signature del SignerInfo (DER di ciascuno, concatenati);
  d) l'``ATSHashIndexV3`` (DER), messo come attributo non firmato
     ``ats-hash-index-v3`` (0.4.0.19122.1.5) nel SignerInfo del token d'archivio,
     con le impronte di ogni certificato, di ogni informazione di revoca e di
     ogni valore di ogni attributo non firmato presenti al momento della marca.
La rimarcatura aggiunge un nuovo archive-time-stamp-v3 che copre anche i
precedenti (stessa funzione). ``verifica_ats_v3`` ricalcola l'impronta e
controlla che quanto indicizzato sia ancora presente.

Riserva (VD-C-143): la costruzione segue la norma ma è provata con il nostro
verificatore e con pyHanko (firma di base e marca), non ancora con EU DSS.
"""
from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from asn1crypto import cms, crl, x509

from app.services.revoca import scelta_ocsp
from libs.shared.firme.asn1_etsi import ATSHashIndexV3, indice_v3, leggi_indice_v3

OID_ATS_V3 = "0.4.0.1733.2.4"
OID_ATS_HASH_INDEX_V3 = "0.4.0.19122.1.5"
_OID_DIGEST = {"sha256": "2.16.840.1.101.3.4.2.1", "sha384": "2.16.840.1.101.3.4.2.2",
               "sha512": "2.16.840.1.101.3.4.2.3"}
_NOMI_DIGEST = {v: k for k, v in _OID_DIGEST.items()}


def _h(algoritmo: str, dati: bytes) -> bytes:
    return hashlib.new(algoritmo, dati).digest()


def aggiungi_validazione(ci: cms.ContentInfo, certificati: list[x509.Certificate],
                         crl_der: list[bytes], ocsp_der: list[bytes]) -> cms.ContentInfo:
    """B-LT: certificati e informazioni di revoca nel SignedData (senza duplicati)."""
    sd = ci["content"]
    presenti = {c.chosen.dump() for c in (sd["certificates"] or [])}
    nuovi_cert = list(sd["certificates"] or [])
    for c in certificati:
        if c.dump() not in presenti:
            nuovi_cert.append(cms.CertificateChoices(name="certificate", value=c))
            presenti.add(c.dump())
    revoche = list(sd["crls"] or []) if sd["crls"] else []
    gia = {r.dump() for r in revoche}
    for d in crl_der:
        scelta = cms.RevocationInfoChoice(name="crl", value=crl.CertificateList.load(d))
        if scelta.dump() not in gia:
            revoche.append(scelta)
            gia.add(scelta.dump())
    for d in ocsp_der:
        scelta = scelta_ocsp(d)
        if scelta.dump() not in gia:
            revoche.append(scelta)
            gia.add(scelta.dump())
    campi = {k: sd[k] for k in ("version", "digest_algorithms", "encap_content_info",
                                "signer_infos")}
    campi["certificates"] = cms.CertificateSet(nuovi_cert)
    if revoche:
        campi["crls"] = cms.RevocationInfoChoices(revoche)
    return cms.ContentInfo({"content_type": "signed_data",
                            "content": cms.SignedData(campi)})


def _indice(sd: cms.SignedData, si: cms.SignerInfo, algoritmo: str) -> ATSHashIndexV3:
    attributi: list[bytes] = []
    for attr in si["unsigned_attrs"] or []:
        tipo = attr["type"].dump()
        for valore in attr["values"]:
            attributi.append(_h(algoritmo, tipo + valore.dump()))
    return indice_v3(
        algoritmo,
        [_h(algoritmo, c.dump()) for c in (sd["certificates"] or [])],
        [_h(algoritmo, r.dump()) for r in (sd["crls"] or [])] if sd["crls"] else [],
        attributi)


def _signer_info_campi(si: cms.SignerInfo) -> bytes:
    return b"".join(si[k].dump() for k in ("version", "sid", "digest_algorithm",
                                           "signed_attrs", "signature_algorithm", "signature"))


def impronta_ats_v3(sd: cms.SignedData, contenuto: bytes, indice: ATSHashIndexV3,
                    algoritmo: str) -> bytes:
    si = sd["signer_infos"][0]
    dati = (sd["encap_content_info"]["content_type"].dump()
            + _h(algoritmo, contenuto)
            + _signer_info_campi(si)
            + indice.dump())
    return _h(algoritmo, dati)


async def aggiungi_ats_v3(ci: cms.ContentInfo, contenuto: bytes, algoritmo: str,
                          marca: Callable[[bytes, str], Awaitable[cms.ContentInfo]]
                          ) -> cms.ContentInfo:
    """B-LTA (o rimarcatura): nuovo archive-time-stamp-v3 sull'ultimo stato della busta."""
    sd = ci["content"]
    si = sd["signer_infos"][0]
    indice = _indice(sd, si, algoritmo)
    token = await marca(impronta_ats_v3(sd, contenuto, indice, algoritmo), algoritmo)
    # ats-hash-index-v3 negli attributi non firmati del SignerInfo del token d'archivio
    sd_tst = token["content"]
    si_tst = sd_tst["signer_infos"][0]
    attr_indice = cms.CMSAttribute({"type": OID_ATS_HASH_INDEX_V3, "values": [indice]})
    si_tst_nuovo = cms.SignerInfo({
        **{k: si_tst[k] for k in ("version", "sid", "digest_algorithm", "signed_attrs",
                                  "signature_algorithm", "signature")},
        "unsigned_attrs": cms.CMSAttributes([*(si_tst["unsigned_attrs"] or []), attr_indice]),
    })
    token_nuovo = cms.ContentInfo({"content_type": "signed_data", "content": cms.SignedData({
        **{k: sd_tst[k] for k in ("version", "digest_algorithms", "encap_content_info",
                                  "certificates")},
        "signer_infos": cms.SignerInfos([si_tst_nuovo]),
    })})
    attr_ats = cms.CMSAttribute({"type": OID_ATS_V3, "values": [token_nuovo]})
    si_nuovo = cms.SignerInfo({
        **{k: si[k] for k in ("version", "sid", "digest_algorithm", "signed_attrs",
                              "signature_algorithm", "signature")},
        "unsigned_attrs": cms.CMSAttributes([*(si["unsigned_attrs"] or []), attr_ats]),
    })
    campi = {k: sd[k] for k in ("version", "digest_algorithms", "encap_content_info",
                                "certificates")}
    if sd["crls"]:
        campi["crls"] = sd["crls"]
    campi["signer_infos"] = cms.SignerInfos([si_nuovo])
    return cms.ContentInfo({"content_type": "signed_data", "content": cms.SignedData(campi)})


def marche_archivio(ci: cms.ContentInfo) -> list[cms.ContentInfo]:
    """Token archive-time-stamp-v3 presenti, nell'ordine in cui sono stati aggiunti."""
    si = ci["content"]["signer_infos"][0]
    return [cms.ContentInfo.load(attr["values"][0].dump())
            for attr in (si["unsigned_attrs"] or []) if attr["type"].dotted == OID_ATS_V3]


def verifica_ats_v3(ci: cms.ContentInfo, contenuto: bytes) -> list[dict]:
    """Per ogni archive-time-stamp-v3: impronta ricalcolata e indice ancora coperto.
    Restituisce ``[{"ok", "impronta", "algoritmo", "token", "motivo"}]`` (la firma
    del token la verifica il chiamante con ``verifica_token``)."""
    sd = ci["content"]
    si = sd["signer_infos"][0]
    esiti: list[dict] = []
    attributi = list(si["unsigned_attrs"] or [])
    for posizione, attr in enumerate(attributi):
        if attr["type"].dotted != OID_ATS_V3:
            continue
        token = cms.ContentInfo.load(attr["values"][0].dump())
        try:
            tst_info = token["content"]["encap_content_info"]["content"].parsed
            imprint = tst_info["message_imprint"]
            algoritmo = _NOMI_DIGEST.get(imprint["hash_algorithm"]["algorithm"].dotted,
                                         imprint["hash_algorithm"]["algorithm"].native)
            indice_der = None
            for a in token["content"]["signer_infos"][0]["unsigned_attrs"] or []:
                if a["type"].dotted == OID_ATS_HASH_INDEX_V3:
                    indice_der = a["values"][0].dump()
            if indice_der is None:
                esiti.append({"ok": False, "motivo": "ats-hash-index-v3 assente"})
                continue
            indice = ATSHashIndexV3.load(indice_der)
            alg_indice, cert_idx, crl_idx, attr_idx = leggi_indice_v3(indice)
            # quanto indicizzato deve essere ancora presente (si può solo aggiungere)
            campi_si = {k: si[k] for k in ("version", "sid", "digest_algorithm", "signed_attrs",
                                           "signature_algorithm", "signature")}
            if posizione:
                campi_si["unsigned_attrs"] = cms.CMSAttributes(attributi[:posizione])
            _, cert_ora, crl_ora, attr_ora = leggi_indice_v3(
                _indice(sd, cms.SignerInfo(campi_si), alg_indice))
            for nome, indicizzati, presenti in (("certificati", cert_idx, cert_ora),
                                                 ("revoche", crl_idx, crl_ora),
                                                 ("attributi", attr_idx, attr_ora)):
                if not set(indicizzati) <= set(presenti):
                    raise ValueError(f"{nome}: elemento indicizzato non più presente")
            atteso = impronta_ats_v3(sd, contenuto, indice, algoritmo)
            ok = atteso == imprint["hashed_message"].native
            esiti.append({"ok": ok, "impronta": atteso, "algoritmo": algoritmo,
                          "token": token,
                          "motivo": None if ok else "impronta della marca d'archivio diversa"})
        except (ValueError, KeyError, TypeError) as exc:
            esiti.append({"ok": False, "motivo": f"marca d'archivio non leggibile: {exc}"[:200]})
    return esiti


__all__ = ["OID_ATS_HASH_INDEX_V3", "OID_ATS_V3", "aggiungi_ats_v3", "aggiungi_validazione",
           "impronta_ats_v3", "marche_archivio", "verifica_ats_v3"]
