# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/api/limiti.py — limite di richieste al minuto per piano (VD-C-099, REV-03).

Le API del ciclo di vita contano nel limite al minuto del piano dell'ente,
come gateway, distribution e notification (componente unico
``libs.shared.limiti_richieste``; Redis da ``REDIS_URL``, obbligatorio in
produzione).
"""
from __future__ import annotations

from app.api.autenticazione import identita_verificata
from app.config import settings
from libs.shared.limiti_richieste import LimitatoreRichieste

#: Limitatore del servizio.
LIMITATORE = LimitatoreRichieste("ciclo-vita", redis_url=settings.redis_url, env=settings.env)

#: Dipendenza del router: identità verificata e richiesta contata.
limite_ciclo_vita = LIMITATORE.dipendenza(identita_verificata)
