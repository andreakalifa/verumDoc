# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Registro delle impronte a due livelli della hashchain (R04, contratti K1, K2, K3).

Moduli:
  - ``chiavi``: chiave del registro, origine dei checkpoint, verifier key del testimone;
  - ``archivio``: accesso al database (nodi, foglie, checkpoint, stato e lease);
  - ``worm``: scritture idempotenti sullo storage conservativo (foglie, checkpoint);
  - ``sequenziatore``: aggiunta delle foglie al registro (un solo detentore del lease);
  - ``checkpoint``: emissione, sigillo qualificato, invio al testimone, allarmi;
  - ``testimone``: client del protocollo K3;
  - ``prove``: prove d'inclusione (documento → PdA → registro) e di consistenza;
  - ``rdv``: rapporto di versamento definitivo v2 (K5), costruzione e verifica;
  - ``conservazione``: RdV v2 in WORM, CONSERVATO ed evento ``aip.conserved``;
  - ``ciclo``: ciclo di fondo che lega i passi.

Descrizione tecnica per il manuale: ``docs/architettura/catena.md``.
"""
