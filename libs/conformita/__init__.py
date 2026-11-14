# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita — documentazione di conformità generata dalla configurazione (R15, VD-C-177).

Riferimenti: REGOLE_SVILUPPO §0 (il manuale non dichiara ciò che il codice non fa), §28.1
(cataloghi generati), §28.3 (allegati del manuale generati dai registri); Linee guida AgID
sulla formazione, gestione e conservazione dei documenti informatici (2021), paragrafo
«Manuale di conservazione» (contenuti minimi, lettere a–l); Regolamento AgID sui criteri
per la fornitura dei servizi di conservazione (Det. 455/2021), Allegato A (RO7: manuale con
piano della sicurezza e modello organizzativo). Architettura: docs/architettura/documentazione.md.

Pacchetto PURO nella parte che compone i documenti (``modello``, ``documenti``,
``rendering``, ``versioni``): riceve i FATTI già raccolti e non legge orologio, rete,
ambiente o database. Le raccolte stanno a parte:

  - ``repository``  fatti dichiarati nel repository (registri, permessi, route, Helm,
                    Terraform, runbook), fotografati in ``dati/fonti_repository.json``
                    (la console non ha il repository: legge la fotografia del rilascio);
  - ``database``    configurazione in esercizio (sola lettura, transazione READ ONLY);
  - ``conservatore`` dati anagrafici e responsabili dalle variabili ``CONSERVATORE_*``.

Regola cardine: nessun valore inventato. Un dato che manca diventa un blocco
``DaCompilare`` con CHI deve fornirlo e DOVE si inserisce; un requisito normativo il cui
testo non è stato riscontrato si dichiara «da verificare sul testo ufficiale».
"""
from __future__ import annotations

from libs.conformita.modello import (
    DA_COMPILARE,
    DA_VERIFICARE,
    DaCompilare,
    DaVerificare,
    Documento,
    Elenco,
    Nota,
    Paragrafo,
    Sezione,
    Tabella,
)

__all__ = ["DA_COMPILARE", "DA_VERIFICARE", "DaCompilare", "DaVerificare", "Documento",
           "Elenco", "Nota", "Paragrafo", "Sezione", "Tabella"]
