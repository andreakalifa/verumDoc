# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""R07, revisione W5: con un limite d'inattività breve l'avviso «resta collegato»
(2 minuti) restava sempre aperto. Ora il minimo del motore è 5 minuti e l'anticipo
è al più metà del limite."""
from app.auth.sessione import AVVISO_INATTIVITA_SECONDI, avviso_inattivita


def test_avviso_al_piu_meta_del_limite() -> None:
    assert avviso_inattivita(1800) == AVVISO_INATTIVITA_SECONDI == 120
    assert avviso_inattivita(300) == 120
    assert avviso_inattivita(180) == 90
    assert avviso_inattivita(60) == 30
    assert avviso_inattivita(None) == 120
