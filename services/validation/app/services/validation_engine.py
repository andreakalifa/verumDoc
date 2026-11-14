# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Engine principale di validazione — orchestra i 9 step AGID.
Riferimento piano: F1.2
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from app.validators.base import (
    ErroreVerificaTransitorio,
    ValidationContext,
    ValidationResult,
)
from app.validators.steps import VALIDATION_PIPELINE

if TYPE_CHECKING:
    from app.services.lettura_file import LettoreFile, RiferimentoFile
    from app.services.regole_pdv import RegolePdv

logger = structlog.get_logger(__name__)


async def run_validation(
    pdv_id: str,
    tenant_id: str,
    indice: dict,
    *,
    riferimenti: dict[str, RiferimentoFile] | None = None,
    lettore: LettoreFile | None = None,
    regole: RegolePdv | None = None,
    canale: str | None = None,
) -> tuple[bool, list[ValidationResult]]:
    """
    Esegue la pipeline di validazione completa (9 step AGID).

    I file non si passano in memoria: ``riferimenti`` (nome_file → posizione nello
    storage) e ``lettore`` (streaming a blocchi); ``regole`` = fotografia del PdV.

    Returns:
        (valido: bool, risultati: list[ValidationResult])
        - Se tutti i step passano → (True, risultati)
        - Al primo step fallito → (False, risultati fino al fallimento)
    """
    # R08: copie locali dei file per riconoscimento del formato e firme, tolte
    # alla fine della verifica qualunque sia l'esito
    with tempfile.TemporaryDirectory(prefix="verifica-") as cartella:
        ctx = ValidationContext(
            pdv_id=pdv_id,
            tenant_id=tenant_id,
            indice=indice,
            riferimenti=dict(riferimenti or {}),
            lettore=lettore,
            regole=regole,
            cartella=Path(cartella),
            # R09 (D24): canale del versamento (atto di sistema di ciclo-vita)
            metadata={"canale": canale},
        )
        return await _esegui(ctx)


async def _esegui(ctx: ValidationContext) -> tuple[bool, list[ValidationResult]]:
    pdv_id, tenant_id, indice = ctx.pdv_id, ctx.tenant_id, ctx.indice
    risultati: list[ValidationResult] = []
    logger.info("validation_started", pdv_id=pdv_id, tenant_id=tenant_id,
                classe=indice.get("classe_documentale"))

    for validator in VALIDATION_PIPELINE:
        try:
            result = await validator.validate(ctx)
        except ErroreVerificaTransitorio:
            # Problema tecnico, non esito: niente rifiuto, il consumer ritenta
            raise
        except Exception as exc:
            logger.exception("validator_error",
                             pdv_id=pdv_id, step=validator.step, error=str(exc))
            result = validator.fail(
                ctx,
                codice=f"VLD-{validator.step}00",
                msg=f"Errore interno nello step {validator.step}: {exc}",
            )
        risultati.append(result)
        if not result.ok:
            logger.warning("validation_step_failed",
                           pdv_id=pdv_id, step=result.step,
                           nome=result.nome, codice=result.codice_errore,
                           msg=result.messaggio)
            return False, risultati

    logger.info("validation_passed", pdv_id=pdv_id,
                steps_ok=len(risultati))
    return True, risultati
