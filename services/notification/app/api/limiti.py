# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/api/limiti.py — limite di richieste al minuto per piano.

R03 f2 (REV-03, VD-C-099): elenco eventi e gestione dei webhook contano nel
limite al minuto del piano dell'ente, con token utente o chiave API, come sul
gateway. Componente unico: ``libs.shared.limiti_richieste``. Redis da
``REDIS_URL`` (in produzione obbligatorio: senza, il servizio non parte).
"""
from __future__ import annotations

from app.api.autenticazione import amministratore_webhook, lettore_eventi
from app.config import settings
from libs.shared.limiti_richieste import LimitatoreRichieste

#: Limitatore del servizio.
LIMITATORE = LimitatoreRichieste("notification", redis_url=settings.redis_url, env=settings.env)

#: Dipendenze dei router (sopra la stessa dipendenza d'accesso delle rotte, che
#: FastAPI esegue una volta sola): identità verificata, ruolo, richiesta contata.
limite_eventi = LIMITATORE.dipendenza(lettore_eventi)
limite_webhook = LIMITATORE.dipendenza(amministratore_webhook)
