# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/formati/pdfa.py — conformità PDF/A dietro un'interfaccia (R08, D02).

  - ``ValidatoreVeraPdf``: veraPDF (``verapdf --format mrr <file>``), il
    validatore di riferimento della PDF Association. Esito: profilo (es.
    «PDF/A-2B») e conformità sì/no con il numero di regole violate;
  - ``DichiarazionePdfA``: ripiego senza veraPDF. Legge la DICHIARAZIONE nei
    metadati XMP (``pdfaid:part`` e ``pdfaid:conformance``) e la riporta come
    «dichiarata, non validata» (``conforme=None``): nessun PDF passa per PDF/A
    conforme senza veraPDF;
  - ``ValidatorePdfAConRipiego``: veraPDF e, se non è installato, la dichiarazione.

Il catalogo dei formati (``libs/verumdoc_rules/catalogo_formati.py``) decide
cosa vale un PDF/A dichiarato ma non validato (esito del formato PDF/A con un
avviso) e un PDF/A NON conforme (esito del PDF generico).
"""
from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree as ET

import structlog

logger = structlog.get_logger(__name__)

#: Byte letti per cercare la dichiarazione XMP (i metadati stanno di solito
#: all'inizio; un XMP compresso non si legge e il PDF risulta «non dichiarato»).
XMP_MAX_BYTE = 4 * 1024 * 1024
_PARTE = re.compile(rb"pdfaid:part\s*(?:=\s*[\"'](\d)[\"']|>\s*(\d)\s*<)")
_CONFORMITA = re.compile(rb"pdfaid:conformance\s*(?:=\s*[\"']([ABUabu])[\"']|>\s*([ABUabu])\s*<)")
_PROFILO = re.compile(r"PDF/A-(\d)([ABU])", re.I)


class ValidatorePdfANonDisponibile(Exception):
    """veraPDF non installato o non eseguibile."""


@dataclass(frozen=True)
class EsitoPdfA:
    validatore: str
    #: livello dichiarato o validato («1B», «2A», «3U»…); None = non PDF/A
    livello: str | None
    #: True/False con veraPDF; None = solo dichiarato (non validato)
    conforme: bool | None
    regole_violate: int | None = None
    motivo: str | None = None

    def come_dict(self) -> dict[str, object]:
        return {"validatore": self.validatore, "livello": self.livello,
                "conforme": self.conforme, "regole_violate": self.regole_violate,
                "motivo": self.motivo}


class ValidatorePdfA(Protocol):
    nome: str

    async def valida(self, percorso: Path) -> EsitoPdfA: ...


def dichiarazione_xmp(testa: bytes) -> str | None:
    parte = _PARTE.search(testa)
    if parte is None:
        return None
    conformita = _CONFORMITA.search(testa)
    livello = (parte.group(1) or parte.group(2)).decode()
    if conformita is not None:
        livello += (conformita.group(1) or conformita.group(2)).decode().upper()
    return livello


class DichiarazionePdfA:
    nome = "dichiarazione XMP (veraPDF non disponibile)"

    async def valida(self, percorso: Path) -> EsitoPdfA:
        testa = await asyncio.to_thread(lambda: percorso.open("rb").read(XMP_MAX_BYTE))
        livello = dichiarazione_xmp(testa)
        return EsitoPdfA(self.nome, livello, None, motivo=(
            "PDF/A dichiarato nei metadati, conformità non validata" if livello
            else "nessuna dichiarazione PDF/A nei metadati"))


class ValidatoreVeraPdf:
    def __init__(self, binario: str = "verapdf", *, timeout_s: float = 300.0) -> None:
        self.binario = binario
        self.timeout_s = timeout_s
        self.nome = "verapdf"

    async def valida(self, percorso: Path) -> EsitoPdfA:
        eseguibile = shutil.which(self.binario)
        if eseguibile is None:
            raise ValidatorePdfANonDisponibile(f"{self.binario} non trovato")
        try:
            processo = await asyncio.create_subprocess_exec(
                eseguibile, "--format", "mrr", str(percorso),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise ValidatorePdfANonDisponibile(f"veraPDF: {type(exc).__name__}") from exc
        try:
            uscita, _ = await asyncio.wait_for(processo.communicate(), self.timeout_s)
        except TimeoutError as exc:
            # revisione X11: il processo non resta attivo dopo il tempo massimo
            processo.kill()
            await processo.wait()
            raise ValidatorePdfANonDisponibile("veraPDF: tempo scaduto") from exc
        return interpreta_mrr(uscita)


def interpreta_mrr(uscita: bytes) -> EsitoPdfA:
    """Esito dal rapporto «machine readable» di veraPDF (elemento validationReport)."""
    try:
        radice = ET.fromstring(uscita)  # noqa: S314 — uscita del processo locale
    except ET.ParseError as exc:
        raise ValidatorePdfANonDisponibile("rapporto di veraPDF non interpretabile") from exc
    rapporto = next((e for e in radice.iter() if e.tag.endswith("validationReport")), None)
    if rapporto is None:
        # PDF non analizzabile (cifrato, danneggiato): non conforme
        return EsitoPdfA("verapdf", None, False, motivo="PDF non analizzabile da veraPDF")
    profilo = _PROFILO.search(rapporto.get("profileName", ""))
    livello = f"{profilo.group(1)}{profilo.group(2).upper()}" if profilo else None
    conforme = rapporto.get("isCompliant", "").lower() == "true"
    dettagli = next((e for e in rapporto.iter() if e.tag.endswith("details")), None)
    violate = int(dettagli.get("failedRules", "0")) if dettagli is not None else None
    return EsitoPdfA("verapdf", livello, conforme, violate,
                     None if conforme else f"PDF/A-{livello or '?'} non conforme "
                                           f"({violate if violate is not None else '?'} "
                                           "regole violate)")


class ValidatorePdfAConRipiego:
    def __init__(self, principale: ValidatorePdfA, ripiego: ValidatorePdfA) -> None:
        self.principale = principale
        self.ripiego = ripiego
        self.nome = f"{principale.nome} (ripiego: {ripiego.nome})"

    async def valida(self, percorso: Path) -> EsitoPdfA:
        try:
            return await self.principale.valida(percorso)
        except ValidatorePdfANonDisponibile as exc:
            logger.warning("pdfa_ripiego_dichiarazione", motivo=str(exc))
            return await self.ripiego.valida(percorso)


__all__ = ["DichiarazionePdfA", "EsitoPdfA", "ValidatorePdfA", "ValidatorePdfAConRipiego",
           "ValidatorePdfANonDisponibile", "ValidatoreVeraPdf", "dichiarazione_xmp",
           "interpreta_mrr"]
