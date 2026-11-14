# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
R10 (D30) — ruoli personalizzati nelle regole delle pratiche (modulo puro).

Con un ruolo personalizzato serve il permesso (oltre alle regole per ruolo
dell'ente); le azioni mai delegabili (approvazione dell'annullamento, avvio
delle pratiche massive, conservatore) non si ottengono mai.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain import stati as st
from app.domain.permessi import (
    Identita,
    motivo_blocco_annullamento,
    motivo_blocco_approvazione,
    motivo_blocco_avvio,
    motivo_blocco_rettifica,
)

ENTE = "5f0c1a2e-8b4d-4c7a-9e21-3d6f8a0b1c11"


def _id(ruolo: str = "OPERATORE", permessi: set[str] | None = None, rp: str | None = "r1",
        utente: str = "u1") -> Identita:
    return Identita(tenant_id=ENTE, user_id=utente, ruolo=ruolo, metodo="jwt",
                    ruolo_personalizzato=rp, permessi=frozenset(permessi or ()) if rp else None)


def test_annullamento_e_rettifica_chiedono_il_permesso() -> None:
    ruoli = ("ADMIN_TENANT", "OPERATORE")
    assert motivo_blocco_annullamento(_id(permessi={"archivio:consulta"}), conservato=False,
                                      ruoli_prima=ruoli, ruoli_dopo=ruoli)
    assert motivo_blocco_annullamento(_id(permessi={"annullamento:richiedi"}),
                                      conservato=False, ruoli_prima=ruoli,
                                      ruoli_dopo=ruoli) is None
    # il ruolo base deve comunque essere ammesso dall'ente
    assert motivo_blocco_annullamento(_id("VIEWER", {"annullamento:richiedi"}),
                                      conservato=False, ruoli_prima=ruoli, ruoli_dopo=ruoli)
    assert motivo_blocco_rettifica(_id(permessi=set()), ruoli)
    assert motivo_blocco_rettifica(_id(permessi={"rettifica:richiedi"}), ruoli) is None
    # ruolo fisso: come prima
    assert motivo_blocco_rettifica(_id(rp=None), ruoli) is None


def test_mai_delegabili() -> None:
    adesso = datetime.now(UTC)
    # un token «ADMIN_TENANT» con rp (non emesso dal gateway, ma la regola regge)
    assert motivo_blocco_approvazione(
        _id("ADMIN_TENANT", {"annullamento:approva"}, utente="u2"), tenant_pratica=ENTE,
        richiedente="u1", ruoli_approvazione=("ADMIN_TENANT",),
        approvatore_creato_il=adesso - timedelta(days=1), pratica_creata_il=adesso)
    assert motivo_blocco_avvio(st.SCARTO, _id("ADMIN_TENANT", set()), ENTE)
    assert not _id("RSC_CONSERVATORE", set()).conservatore
    assert _id("RSC_CONSERVATORE", rp=None).conservatore
