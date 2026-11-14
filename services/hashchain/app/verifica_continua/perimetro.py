# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Perimetro delle API delle verifiche: chi vede e chi chiede che cosa (REGOLE §14).

Unico punto di decisione per il router (§14.9):
  - personale del conservatore (SUPER_ADMIN, RSC_CONSERVATORE): tutti gli enti,
    filtro facoltativo per ente; può chiedere verifiche di qualsiasi PdA o ente;
  - utenti di un ente: solo il proprio ente (``tenant_id`` del token verificato);
    un ente o un PdA di altri risponde 404, mai 403 (§14.4);
  - chiedere una verifica: oltre al conservatore, l'ADMIN_TENANT sui PdA e
    sull'intero proprio ente. Scelta da confermare col committente (§14.1):
    gli altri ruoli dell'ente leggono soltanto;
  - token senza ``tenant_id`` interpretabile: nessun dato (§14.2, §14.3).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

RUOLI_CONSERVATORE: frozenset[str] = frozenset({"SUPER_ADMIN", "RSC_CONSERVATORE"})
RUOLI_RICHIESTA_ENTE: frozenset[str] = frozenset({"ADMIN_TENANT"})


class FuoriPerimetro(LookupError):
    """La risorsa non è nel perimetro: il router risponde 404."""


@dataclass(frozen=True)
class Perimetro:
    conservatore: bool
    ente: uuid.UUID | None  # ente dell'utente (None per il conservatore)
    ruolo: str
    utente: str | None

    def ente_effettivo(self, richiesto: uuid.UUID | None) -> uuid.UUID | None:
        """Ente su cui filtrare: quello richiesto (conservatore) o il proprio.
        Un ente diverso dal proprio per un utente d'ente → ``FuoriPerimetro``."""
        if self.conservatore:
            return richiesto
        if richiesto is not None and richiesto != self.ente:
            raise FuoriPerimetro("ente fuori perimetro")
        return self.ente

    def vede_richiesta(self, ente_richiesta: str | None) -> bool:
        return self.conservatore or (
            ente_richiesta is not None and self.ente is not None
            and ente_richiesta == str(self.ente))

    @property
    def puo_chiedere(self) -> bool:
        return self.conservatore or self.ruolo in RUOLI_RICHIESTA_ENTE


def perimetro_di(utente: dict[str, Any]) -> Perimetro:
    """Perimetro dal token verificato; ``FuoriPerimetro`` se l'ente non si ricava."""
    ruolo = str(utente.get("ruolo") or "")
    chi = str(utente.get("user_id") or utente.get("sub") or "").strip()[:100] or None
    if ruolo in RUOLI_CONSERVATORE:
        return Perimetro(True, None, ruolo, chi)
    try:
        ente = uuid.UUID(str(utente.get("tenant_id")))
    except (TypeError, ValueError):
        raise FuoriPerimetro("ente del token non interpretabile") from None
    return Perimetro(False, ente, ruolo, chi)
