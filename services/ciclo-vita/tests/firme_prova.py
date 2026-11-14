# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Documenti firmati di prova per le suite di ciclo-vita (R08): una CA di prova
generata a runtime (``libs/shared/firme/tests/pki_prova.py``), nessuna chiave nel
repository. ``conftest.py`` configura il verificatore del processo con questa
CA come unica trust anchor.
"""
from __future__ import annotations

from libs.shared.firme.tests.pki_prova import Pki

#: Revisione X3: il certificato porta il codice fiscale (serialNumber TINIT-…);
#: ``supporto.crea_ente`` lo mette fra i firmatari autorizzati dell'ente
CF_PROVA = "DRGPRV80A01H501Z"
PKI = Pki.nuova("Dirigente di prova", cf=CF_PROVA)
#: Autorizzazione allo scarto firmata CAdES (.p7m attached)
P7M = PKI.cades(b"Autorizzazione allo scarto di prova")
#: PDF firmato PAdES (autorizzazione o nulla osta)
PDF_FIRMATO = PKI.pades("Nulla osta di prova")
#: Firma di una CA NON attendibile (catena non riconducibile alla trust anchor)
P7M_ESTRANEO = Pki.nuova("Firmatario estraneo").cades(b"Autorizzazione allo scarto di prova")


def richiesta_annullamento(pdv_id: str) -> bytes:
    """Richiesta di annullamento firmata che cita il versamento."""
    return PKI.cades(f"Si chiede l'annullamento del versamento {pdv_id}".encode())


def autorizzazione(pratica_id: str) -> bytes:
    """R12a (VD-C-145): autorizzazione allo scarto firmata CAdES che cita la pratica."""
    return PKI.cades(f"Autorizzazione allo scarto, pratica {pratica_id}".encode())


def autorizzazione_pdf(pratica_id: str) -> bytes:
    """R12a (VD-C-145): autorizzazione (o nulla osta) PAdES che cita la pratica."""
    return PKI.pades(f"Autorizzazione allo scarto, pratica {pratica_id}")
