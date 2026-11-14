# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/avvisi.py — avvisi automatici del ciclo di vita all'ente (R09).

Riferimenti: decisioni D20 (ripresa automatica con notifica), D21 (proposta di
scarto in bozza con notifica), D23 (annullamento da approvare); REGOLE_SVILUPPO
§12.3 (outbox nella stessa transazione), §21.1 (l'operazione principale non
dipende dalla posta).

Un avviso è UN evento ``ciclo_vita.avviso`` (``libs.shared.avvisi``: elenco
degli eventi, webhook, e-mail al referente via notification) con il
``sottotipo`` e i riferimenti; si registra una volta per (ente, chiave).
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from libs.shared.avvisi import TIPO_CICLO_VITA, registra_avviso

PROPOSTA_SCARTO = "PROPOSTA_SCARTO"
RIPRESA_SCARTO = "RIPRESA_SCARTO"
ANNULLAMENTO_DA_APPROVARE = "ANNULLAMENTO_DA_APPROVARE"
#: Revisione R09 (W1): annullamento chiuso RESPINTO dal lavoratore prima di
#: applicarlo (versamento, persone o regola cambiati)
ANNULLAMENTO_RESPINTO = "ANNULLAMENTO_RESPINTO"
#: Revisione R14 (4): pratica sospesa (tentativi esauriti) ripresa dal conservatore
PRATICA_RIPRESA = "PRATICA_RIPRESA"
SOTTOTIPI = (PROPOSTA_SCARTO, RIPRESA_SCARTO, ANNULLAMENTO_DA_APPROVARE, ANNULLAMENTO_RESPINTO,
             PRATICA_RIPRESA)


async def avvisa(session: Any, tenant_id: uuid.UUID | str, sottotipo: str, riferimento: str,
                 dati: Mapping[str, Any]) -> bool:
    """Avviso all'ente nella transazione di ``session`` (False se già dato)."""
    if sottotipo not in SOTTOTIPI:
        raise ValueError(f"sottotipo di avviso sconosciuto: {sottotipo}")
    return await registra_avviso(session, tipo=TIPO_CICLO_VITA, tenant_id=tenant_id,
                                 chiave=f"{sottotipo}:{riferimento}",
                                 dati={"sottotipo": sottotipo, **dict(dati)})


__all__ = ["ANNULLAMENTO_DA_APPROVARE", "ANNULLAMENTO_RESPINTO", "PRATICA_RIPRESA",
           "PROPOSTA_SCARTO",
           "RIPRESA_SCARTO", "SOTTOTIPI",
           "avvisa"]
