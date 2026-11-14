# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/distribution/app/auth/limiti.py — limite di richieste al minuto per piano.

R03 f2 (REV-03, VD-C-099): come sul gateway, ogni richiesta autenticata (token
utente o chiave API) conta nel limite al minuto del piano dell'ente; ricerca
e richiesta di un PdD hanno in più un limite proprio (operazioni costose).
Componente unico: ``libs.shared.limiti_richieste``. Redis da ``REDIS_URL``.
"""
from __future__ import annotations

from app.auth.dipendenze import utente_corrente
from app.config import settings
from libs.shared.limiti_richieste import LimitatoreRichieste

#: Limitatore del servizio (all'import: in produzione senza REDIS_URL non parte).
LIMITATORE = LimitatoreRichieste("distribution", redis_url=settings.redis_url, env=settings.env)

#: Da mettere nelle ``dependencies`` dei router: autentica e conta la richiesta.
limite_richieste = LIMITATORE.dipendenza(utente_corrente)
#: Limiti aggiuntivi delle operazioni costose.
limite_ricerca = LIMITATORE.dipendenza_categoria(utente_corrente, "ricerca")
limite_pdd = LIMITATORE.dipendenza_categoria(utente_corrente, "pdd")
