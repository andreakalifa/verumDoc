# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/pec — canali PEC del conservatore (R13b, VD-C-078).

Una sola definizione (REGOLE §9.1) dei tipi di canale, dei campi della loro
configurazione (tabella ``canali_pec``, mai segreti), dei segreti attesi (Secret
del lavoratore della posta, ``PEC_SEGRETI_JSON``, legati agli host), della
verifica S/MIME delle ricevute e della loro associazione al messaggio: la usano
la console (notification, validazione al salvataggio) e il lavoratore della posta
(invio, lettura delle ricevute).
"""
