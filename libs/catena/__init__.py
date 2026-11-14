# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/catena — catena delle impronte di VerumDoc (R04, contratto K4).

Pacchetto puro (libreria standard + ``cryptography`` solo per ``nota``) usato da
hashchain (registro e checkpoint), aip-builder (albero del PdA), testimone e
verificatore pubblico. Moduli:

  - ``rfc6962``: albero di Merkle RFC 6962, prove d'inclusione e di consistenza
    e loro verifica (RFC 9162 §2.1.3.2, §2.1.4.2);
  - ``albero``: le stesse operazioni dai NODI SALVATI (registro), in O(log n) letture;
  - ``canonico``: JSON canonico delle foglie;
  - ``foglie``: dati delle foglie dei due livelli (PdA e registro globale);
  - ``nota``: checkpoint C2SP tlog-checkpoint in nota firmata C2SP signed-note
    con Ed25519 (richiede ``cryptography``; non è importato qui).

Descrizione per il manuale e per i clienti: ``docs/architettura/catena.md``.
"""
