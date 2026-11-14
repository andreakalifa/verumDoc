# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Base class per i validator AGID.
Ogni validator implementa un singolo step di validazione.
Riferimento piano: F1.2 — 9 step validazione LG AGID 2022
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # solo annotazioni: lettura_file importa questo modulo
    from app.services.lettura_file import FileLetto, LettoreFile, RiferimentoFile
    from app.services.regole_pdv import RegolePdv


class ErroreVerificaTransitorio(Exception):
    """
    Errore tecnico durante un passo (DB o storage non raggiungibili): non è un
    esito della verifica. La pipeline lo propaga e il consumer ritenta (VD-C-009).
    """


@dataclass
class ValidationResult:
    """Risultato di un singolo step di validazione."""
    step: int
    nome: str
    ok: bool
    codice_errore: str | None = None
    messaggio: str | None = None
    dettagli: dict | None = None

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "nome": self.nome,
            "ok": self.ok,
            "codice_errore": self.codice_errore,
            "messaggio": self.messaggio,
            "dettagli": self.dettagli,
        }


@dataclass
class ValidationContext:
    """
    Contesto condiviso tra tutti i validator per un PdV.

    R03 (b5): nessun file in memoria. I passi che leggono i file usano
    ``lettore`` (streaming a blocchi) sui ``riferimenti`` (nome_file → posizione
    nello staging); i risultati delle letture restano in ``dimensioni`` e
    ``file_letti`` (dimensione e impronte, mai il contenuto). ``regole`` sono i
    parametri della fotografia del PdV (limite per file, formati ammessi);
    None = predefiniti del servizio.
    """
    pdv_id: str
    tenant_id: str
    indice: dict
    riferimenti: dict[str, RiferimentoFile] = field(default_factory=dict)
    lettore: LettoreFile | None = None
    regole: RegolePdv | None = None
    dimensioni: dict[str, int] = field(default_factory=dict)
    file_letti: dict[str, FileLetto] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    #: R08: cartella temporanea della verifica e copie locali dei file (None = oltre
    #: il limite della copia), formati identificati dal passo 10
    cartella: Path | None = None
    copie: dict[str, Path | None] = field(default_factory=dict)
    identificati: dict[str, dict] = field(default_factory=dict)


class BaseValidator(ABC):
    """Interfaccia comune per tutti i validator."""

    step: int
    nome: str

    @abstractmethod
    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        """Esegue la validazione e ritorna il risultato."""
        ...

    def ok(self, ctx: ValidationContext, dettagli: dict | None = None) -> ValidationResult:
        return ValidationResult(step=self.step, nome=self.nome, ok=True, dettagli=dettagli)

    def fail(self, ctx: ValidationContext, codice: str, msg: str,
             dettagli: dict | None = None) -> ValidationResult:
        return ValidationResult(
            step=self.step, nome=self.nome, ok=False,
            codice_errore=codice, messaggio=msg, dettagli=dettagli
        )


def atto_di_sistema(ctx: ValidationContext, doc: dict) -> bool:
    """R09 (D24): atto XML di rettifica versato da ciclo-vita (canale CICLO_VITA):
    sempre ammesso, qualunque siano i formati della classe."""
    from libs.verumdoc_rules.atti_sistema import e_atto_di_sistema

    return e_atto_di_sistema((ctx.metadata or {}).get("canale"), tipo=doc.get("tipo"),
                             formato=doc.get("formato"), parametri=doc.get("parametri"))
