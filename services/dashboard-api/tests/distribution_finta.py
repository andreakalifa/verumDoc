# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Distribution finta per la ricerca della dashboard (R03 f2, REV-06): trasporto
httpx che risponde a ``POST /api/v2/distribution/search`` come distribution
(stessi campi di ``AIPSearchResult``), con risultati scelti dal test.

Registra corpo e intestazioni ricevuti; ``risultati`` è la lista degli
``aip_id`` restituiti (in ordine) per l'ente del corpo; ``pagina`` limita le
voci e produce un cursore opaco ``"p<n>"``; ``errore`` = (stato, corpo,
intestazioni) per simulare un rifiuto.
"""
from __future__ import annotations

import json
from typing import Any

import httpx


class DistributionFinta:
    def __init__(self) -> None:
        self.richieste: list[tuple[dict[str, Any], dict[str, str]]] = []
        self.risultati: dict[str, list[str]] = {}
        self.errore: tuple[int, dict[str, Any], dict[str, str]] | None = None

    def trasporto(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._gestisci)

    def _gestisci(self, richiesta: httpx.Request) -> httpx.Response:
        assert richiesta.url.path == "/api/v2/distribution/search", richiesta.url.path
        corpo = json.loads(richiesta.content)
        self.richieste.append((corpo, dict(richiesta.headers)))
        if self.errore is not None:
            stato, dati, intestazioni = self.errore
            return httpx.Response(stato, json=dati, headers=intestazioni)
        tutti = self.risultati.get(corpo.get("tenant_id"), [])
        inizio = int(str(corpo.get("cursore") or "p0")[1:])
        limite = int(corpo.get("limit") or 20)
        pagina = tutti[inizio: inizio + limite]
        altri = inizio + limite < len(tutti)
        return httpx.Response(200, json={
            "total": len(tutti), "total_esatto": True,
            "items": [{"aip_id": a} for a in pagina],
            "cursore": f"p{inizio + limite}" if altri else None, "altri": altri,
            "campi_testo": ["testo_completo", "oggetto"],
        })
