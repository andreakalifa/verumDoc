# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme/asn1_etsi.py — attributi CAdES ETSI per asn1crypto (R13a).

asn1crypto non conosce gli attributi non firmati di ETSI EN 319 122-1 e li legge
come ``Any``: con un ContentInfo dentro (archive-time-stamp-v3) la lettura
completa della busta (``.native``) fallisce e la busta risulterebbe «illeggibile».
Qui si registrano, una volta all'importazione, i tipi dei due attributi del
livello B-LTA:
  - ``archive-time-stamp-v3`` (0.4.0.1733.2.4): SET OF ContentInfo (TimeStampToken);
  - ``ats-hash-index-v3`` (0.4.0.19122.1.5): SET OF ATSHashIndexV3.
Lo importano il verificatore pyHanko, la rilevazione delle firme e il servizio di
firma (che produce B-LTA). Idempotente.
"""
from __future__ import annotations

from asn1crypto import cms, core

OID_ATS_V3 = "0.4.0.1733.2.4"
OID_ATS_HASH_INDEX_V3 = "0.4.0.19122.1.5"


class _ImpronteIndice(core.SequenceOf):
    _child_spec = core.OctetString


class ATSHashIndexV3(core.SequenceOf):
    """ETSI EN 319 122-1 §5.5.3 ``ATSHashIndexV3 ::= SEQUENCE { hashIndAlgorithm
    AlgorithmIdentifier DEFAULT sha256, certificatesHashIndex, crlsHashIndex,
    unsignedAttrsHashIndex }``. Il primo campo facoltativo e le tre sequenze sono
    tutti SEQUENCE: asn1crypto non li distingue per tag, quindi si legge e si
    scrive a mano (``indice_v3`` e ``leggi_indice_v3``), con il DER che omette il
    valore predefinito."""

    _child_spec = core.Any


def indice_v3(algoritmo: str, certificati: list[bytes], revoche: list[bytes],
              attributi: list[bytes]) -> ATSHashIndexV3:
    elementi = [] if algoritmo == "sha256" else [
        core.Any.load(cms.DigestAlgorithm({"algorithm": algoritmo}).dump())]
    for impronte in (certificati, revoche, attributi):
        elementi.append(core.Any.load(_ImpronteIndice(list(impronte)).dump()))
    return ATSHashIndexV3(elementi)


def leggi_indice_v3(indice: ATSHashIndexV3) -> tuple[str, list[bytes], list[bytes], list[bytes]]:
    """(algoritmo, impronte dei certificati, delle revoche, degli attributi)."""
    elementi = list(indice)
    if len(elementi) == 4:
        algoritmo = str(cms.DigestAlgorithm.load(elementi[0].dump())["algorithm"].native)
        elementi = elementi[1:]
    elif len(elementi) == 3:
        algoritmo = "sha256"
    else:
        raise ValueError("ATSHashIndexV3: numero di campi non valido")
    liste = [[bytes(v.native) for v in _ImpronteIndice.load(e.dump())] for e in elementi]
    return algoritmo, liste[0], liste[1], liste[2]


class SetOfATSHashIndexV3(core.SetOf):
    _child_spec = ATSHashIndexV3


def registra() -> None:
    cms.CMSAttributeType._map.setdefault(OID_ATS_V3, "archive_time_stamp_v3")
    cms.CMSAttributeType._map.setdefault(OID_ATS_HASH_INDEX_V3, "ats_hash_index_v3")
    cms.CMSAttributeType._reverse_map = None  # ricalcolata da asn1crypto alla richiesta
    cms.CMSAttribute._oid_specs.setdefault("archive_time_stamp_v3", cms.SetOfContentInfo)
    cms.CMSAttribute._oid_specs.setdefault("ats_hash_index_v3", SetOfATSHashIndexV3)


registra()

__all__ = ["ATSHashIndexV3", "OID_ATS_HASH_INDEX_V3", "OID_ATS_V3", "indice_v3",
           "leggi_indice_v3", "registra"]
