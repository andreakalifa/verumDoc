# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/signature/app/services/fornitori.py
Interfacce dei fornitori di firma (sigillo) e di marca temporale — VD-C-010.

Due ruoli distinti, come richiesto da eIDAS e dalle Linee guida AgID:
  - FornitoreFirma: appone la firma/sigillo del conservatore (chiave in HSM
    remoto o, solo in sviluppo, chiave locale autogenerata);
  - FornitoreMarca: rilascia marche temporali RFC 3161 (TSA).

La busta CAdES/PAdES si costruisce localmente (firma_cms.py); al fornitore di
firma si chiede soltanto la firma crittografica dei byte da firmare, al
fornitore di marca soltanto il token RFC 3161 su un'impronta.

Riferimenti: Reg. UE 910/2014 artt. 26, 36, 42; ETSI EN 319 122 (CAdES),
ETSI EN 319 142 (PAdES), RFC 3161/5816; Cloud Signature Consortium API v2.
Decisione aperta VD-P-05: scelta del fornitore qualificato.

R13a: interfaccia unica per ogni adattatore (CSC, PKCS#11, sviluppo; TSA RFC
3161, sviluppo). Al fornitore di firma si passa SOLO l'impronta
(``firma_impronta``): il documento non lascia il sistema. Ogni fornitore ha il
``codice`` del registro (``fornitori_firma``) e le radici con cui si verifica
la firma che produce (``radici``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from asn1crypto import algos, cms, x509

from libs.shared.errori import ErroreApi

# OID dell'estensione QCStatements e della dichiarazione QcCompliance
# (ETSI EN 319 412-5): presente solo nei certificati qualificati.
OID_QC_COMPLIANCE = "0.4.0.1862.1.1"
# Estensione del TSTInfo che dichiara la marca «qualificata» (ETSI EN 319 422 §9.1).
OID_QTST_STATEMENT = "0.4.0.19422.1.1"


class ErroreFirma(ErroreApi):
    """Errore di dominio del servizio di firma, con codice stabile del catalogo
    errori (VD-C-100) e stato HTTP: il gestore comune risponde in problem+json
    con il messaggio come ``detail``."""

    codice = "FIRMA-500"
    stato_http = 500

    def __init__(self, messaggio: str, dettagli: dict | None = None) -> None:
        super().__init__(type(self).codice, messaggio, status=self.stato_http,
                         dettagli=dettagli)
        self.messaggio = messaggio

    def __str__(self) -> str:
        return self.messaggio

    def come_dict(self) -> dict:
        return {"codice": self.codice, "messaggio": self.messaggio, "dettagli": self.dettagli}


class FirmaNonConfigurata(ErroreFirma):
    """Nessun fornitore configurato per l'operazione richiesta."""

    codice = "FIRMA-503-NON-CONFIGURATA"
    stato_http = 503


class FornitoreVietatoInProduzione(ErroreFirma):
    """L'adattatore di sviluppo non può firmare né marcare in produzione."""

    codice = "FIRMA-409-SVILUPPO-IN-PRODUZIONE"
    stato_http = 409


class ErroreFornitore(ErroreFirma):
    """Il fornitore remoto ha risposto con un errore o una risposta non valida."""

    codice = "FIRMA-502-FORNITORE"
    stato_http = 502


class ConfigurazioneNonDisponibile(ErroreFirma):
    """R13a: registro dei fornitori o parametri non leggibili (transitorio)."""

    codice = "FIRMA-503-CONFIGURAZIONE"
    stato_http = 503


class FirmaProdottaNonValida(ErroreFirma):
    """R13a: la firma (o la marca) prodotta non supera la verifica: non si usa."""

    codice = "FIRMA-502-FIRMA-NON-VALIDA"
    stato_http = 502


@dataclass(frozen=True)
class IdentitaAttesa:
    """Revisione R13a (4): identità attesa del certificato di firma del fornitore
    (impronte SHA-256 del DER e/o organizationIdentifier, OID 2.5.4.97). Vuota =
    nessun vincolo (solo fuori produzione: la configurazione la esige in produzione)."""

    certificati_sha256: frozenset[str] = frozenset()
    organization_identifier: str = ""

    @property
    def vuota(self) -> bool:
        return not self.certificati_sha256 and not self.organization_identifier

    def controlla(self, certificato: x509.Certificate) -> None:
        """``FirmaProdottaNonValida`` se il certificato non è quello atteso."""
        import hashlib

        if self.certificati_sha256 and \
                hashlib.sha256(certificato.dump()).hexdigest() not in self.certificati_sha256:
            raise FirmaProdottaNonValida("il certificato del fornitore non è fra quelli attesi "
                                         "(certificati_attesi_sha256)")
        if self.organization_identifier:
            trovati = [str(a["value"].native) for rdn in certificato.subject.chosen
                       for a in rdn if a["type"].dotted == "2.5.4.97"]
            if self.organization_identifier not in trovati:
                raise FirmaProdottaNonValida("organizationIdentifier del certificato diverso da "
                                             "quello atteso")


class FornitoreNonRegistrato(ErroreFirma):
    codice = "FIRMA-404-FORNITORE"
    stato_http = 404


class RichiestaNonValida(ErroreFirma):
    """Input non utilizzabile (formato errato, contenuto non PDF per PAdES, ...)."""

    codice = "FIRMA-422-RICHIESTA"
    stato_http = 422


@dataclass(frozen=True)
class MaterialeFirma:
    """Certificato del firmatario, catena e meccanismo di firma."""

    certificato: x509.Certificate
    catena: list[x509.Certificate] = field(default_factory=list)
    meccanismo: algos.SignedDigestAlgorithm | None = None

    @property
    def qualificato(self) -> bool:
        """True se il certificato dichiara QcCompliance (ETSI EN 319 412-5).

        È la dichiarazione dell'emittente: la verifica sulla Trusted List
        europea non è ancora integrata (vedi stato del servizio).
        """
        return certificato_dichiara_qualificato(self.certificato)


def certificato_dichiara_qualificato(cert: x509.Certificate) -> bool:
    """Legge l'estensione QCStatements e cerca QcCompliance."""
    for ext in cert["tbs_certificate"]["extensions"] or []:
        if ext["extn_id"].dotted == "1.3.6.1.5.5.7.1.3":
            try:
                for statement in ext["extn_value"].parsed:
                    if statement["statement_id"].dotted == OID_QC_COMPLIANCE:
                        return True
            except Exception:  # noqa: BLE001 — estensione malformata = non qualificato
                return False
    return False


class FornitoreFirma(ABC):
    """Interfaccia di un fornitore di firma/sigillo."""

    #: tipo dell'adattatore (es. «sviluppo», «csc», «pkcs11»)
    nome: str = ""
    #: True solo per l'adattatore di sviluppo (chiave locale non qualificata)
    di_sviluppo: bool = False
    #: codice del registro dei fornitori (R13a); vuoto = configurazione d'ambiente
    codice: str = ""
    #: radici con cui si verifica la firma prodotta (R13a)
    radici_proprie: tuple[x509.Certificate, ...] = ()
    #: identità attesa del certificato (revisione R13a (4))
    identita: IdentitaAttesa = IdentitaAttesa()

    @abstractmethod
    async def materiale(self) -> MaterialeFirma:
        """Certificato, catena e meccanismo usati per firmare."""

    @abstractmethod
    async def firma_impronta(self, impronta: bytes, algoritmo_digest: str) -> bytes:
        """Firma crittografica dell'IMPRONTA (già calcolata localmente) degli
        attributi firmati: al fornitore non arriva mai il documento."""

    async def firma_dati(self, dati: bytes, algoritmo_digest: str) -> bytes:
        """Firma di `dati` (attributi firmati DER): impronta locale e ``firma_impronta``."""
        import hashlib

        if algoritmo_digest not in ("sha256", "sha384", "sha512"):
            raise RichiestaNonValida(f"digest non supportato: {algoritmo_digest}")
        return await self.firma_impronta(hashlib.new(algoritmo_digest, dati).digest(),
                                         algoritmo_digest)

    def radici(self) -> list[x509.Certificate]:
        """Radici attendibili per la verifica della firma prodotta."""
        return list(self.radici_proprie)

    async def chiudi(self) -> None:  # noqa: B027 — facoltativo per gli adattatori
        """Rilascia le risorse (sessioni HSM, connessioni)."""

    async def revoca_locale(self, adesso: Any) -> tuple[list[bytes], list[bytes]]:
        """(CRL, risposte OCSP) DER che il fornitore dà senza rete (B-LT); il resto
        si recupera dagli indirizzi dei certificati (``revoca.py``)."""
        return [], []

    async def lunghezza_firma_stimata(self) -> int:
        """Spazio da riservare per la firma (usato da PAdES in prova a vuoto)."""
        mat = await self.materiale()
        pub = mat.certificato.public_key
        bit = pub.bit_size
        if pub.algorithm == "ec":
            return 2 * ((bit + 7) // 8) + 9
        return (bit + 7) // 8


class FornitoreMarca(ABC):
    """Interfaccia di un fornitore di marca temporale RFC 3161."""

    nome: str = ""
    di_sviluppo: bool = False
    codice: str = ""

    @abstractmethod
    async def marca(self, impronta: bytes, algoritmo: str) -> cms.ContentInfo:
        """TimeStampToken (ContentInfo signedData) sull'impronta indicata."""

    def radici_attendibili(self) -> list[x509.Certificate]:
        """Certificati radice con cui verificare i token di questo fornitore."""
        return []

    async def revoca_locale(self, adesso: Any) -> tuple[list[bytes], list[bytes]]:
        return [], []
