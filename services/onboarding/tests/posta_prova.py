# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R12a (VD-C-152): coppia X25519 della posta generata a runtime per i test
(nessuna chiave nel repository); ``conftest.py`` mette la pubblica in
``POSTA_CHIAVE_PUBBLICA`` prima di importare l'app."""
from libs.shared.security.sigillo import genera_coppia

POSTA_PRIVATA, POSTA_PUBBLICA = genera_coppia()
