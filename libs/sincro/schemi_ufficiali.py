# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/sincro/schemi_ufficiali.py — impronte SHA-256 ATTESE dei file degli schemi
ufficiali (R11, revisione T4; decisione VD-P-111).

Uno schema è «ufficiale» SOLO se la sua cartella contiene esattamente questi file
con queste impronte: il flag ``ufficiale`` del descrittore ``schema.json`` è al più
informativo e non basta. Il dizionario è VUOTO finché la UNI 11386:2020 non è
acquistata (D03, VD-N-01). Dopo l'acquisto:

  1. il conservatore calcola le impronte dei file ricevuti da UNI
     (``python tools/sincro/verifica_ipda.py impronte --cartella …``);
  2. le scrive qui con una modifica di codice sottoposta a revisione e una
     decisione ``VD-N`` firmata dal responsabile del servizio, che cita la fonte
     dell'acquisto;
  3. solo allora ``registra --ufficiale`` è accettato.

Chi può scrivere nella cartella degli schemi o nell'immagine non può quindi
dichiarare ufficiale uno schema qualunque: servirebbe cambiare questo file, cioè
il codice rivisto.

Forma: ``{versione: {percorso relativo del file: sha256 esadecimale}}``.
"""
from __future__ import annotations

IMPRONTE_UFFICIALI: dict[str, dict[str, str]] = {}

__all__ = ["IMPRONTE_UFFICIALI"]
