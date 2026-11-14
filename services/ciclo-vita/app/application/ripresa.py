# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/ripresa.py — ripresa di una pratica sospesa (revisione R14 (4), VD-C-133).

Una pratica il cui lavoro è fallito ``LAVORATORE_TENTATIVI_MASSIMI`` volte è SOSPESA
(``LC-409-TENTATIVI-ESAURITI``: lavoro tolto, lavoro sospeso in ``ultimo_errore``). Corretta
la causa, il conservatore la riprende da qui invece che con un comando SQL:

  - solo un ruolo FISSO del conservatore con token utente (mai una chiave API né un ruolo
    personalizzato): permesso ``pratica:riprendi``, mai delegabile (catalogo e vincolo del
    database);
  - pratica bloccata (``FOR UPDATE``, lo stesso lock delle transizioni e dell'avvio) e
    versione attesa (REGOLE §12.5): due riprese insieme non passano;
  - si riprende solo una pratica davvero sospesa, rimettendo il SUO lavoro sospeso con i
    tentativi azzerati; versione + 1, voce d'audit con motivo e ultimo errore, evento
    ``ciclo_vita.avviso`` (sottotipo ``PRATICA_RIPRESA``) per l'ente, nella stessa
    transazione;
  - il lavoratore, prima di eseguire, riverifica da sé la catena delle autorizzazioni:
    la ripresa non salta alcun controllo.

R17 (VD-P-164): la ripresa è una decisione sensibile (rimette in esecuzione un lavoro
distruttivo): porta la conferma firmata dal gateway dopo una conferma dell'identità recente
(``app.application.conferme``); il lavoratore la pretende dopo ogni sospensione che ha
registrato lui.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.application import avvisi, conferme
from app.application import pratiche as pr
from app.domain import stati as st
from app.domain.permessi import Identita
from app.infrastructure import repository as repo
from libs.shared.errori import ErroreApi
from libs.shared.security.audit import registra_audit
from libs.verumdoc_rules.permessi import PRATICA_RIPRENDI

logger = structlog.get_logger(__name__)



def motivo_blocco_ripresa(identita: Identita, pratica: dict[str, Any]) -> tuple[str, str] | None:
    """``None`` se la ripresa si può chiedere, altrimenti (codice, messaggio). Funzione
    unica per l'endpoint e per ``ripresa_consentita`` del dettaglio (REGOLE §9.7)."""
    if not identita.conservatore or identita.metodo != "jwt" \
            or not identita.concesso(PRATICA_RIPRENDI):
        return ("LC-403-RUOLO", "Solo il conservatore (ruolo fisso, con il proprio accesso) "
                                "riprende una pratica sospesa")
    if st.lavoro_sospeso(pratica) is None:
        return ("LC-409-NON-SOSPESA", "La pratica non è sospesa: nulla da riprendere")
    return None


async def riprendi(identita: Identita, pratica_id: str, *, versione: int, motivo: str,
                   adesso: datetime | None = None,
                   conferma: dict[str, Any] | None = None) -> dict[str, Any]:
    from libs.shared.db import get_async_session

    pid = pr.uuid_o_404(pratica_id)
    adesso = adesso or pr.adesso_utc()
    if len(motivo.strip()) < st.MOTIVO_MINIMO:
        raise ErroreApi("LC-422-MOTIVO", f"Per riprendere serve un motivo di almeno "
                                         f"{st.MOTIVO_MINIMO} caratteri (resta nell'audit)")
    try:
        async with get_async_session() as session:
            pratica = await repo.leggi_pratica(session, pid, per_aggiornare=True)
            if pratica is None or not identita.conservatore:
                raise pr.non_trovata()
            blocco = motivo_blocco_ripresa(identita, pratica)
            if blocco is not None:
                raise ErroreApi(blocco[0], blocco[1])
            if int(pratica["versione"]) != versione:
                raise ErroreApi("LC-409-STATO-CAMBIATO",
                                f"La pratica è alla versione {pratica['versione']}, non {versione}",
                                dettagli={"errore": "STATO_CAMBIATO",
                                          "stato_attuale": pratica["stato"],
                                          "versione": pratica["versione"]})
            lavoro = st.lavoro_sospeso(pratica)
            errore = dict(pratica.get("ultimo_errore") or {})
            # R17 (VD-P-164): la ripresa rimette un lavoro distruttivo: conferma del gateway,
            # che il lavoratore pretende dopo ogni sospensione registrata da lui
            conferma_id = await conferme.registra(
                session, identita, azione=conferme.cd.PRATICA_RIPRENDI, pratica=pratica,
                conferma=conferma, campi={"lavoro": lavoro}, versione_attesa=versione,
                adesso=adesso)
            dati = pr.con_conferma(pratica, conferme.cd.PRATICA_RIPRENDI, conferma_id)["dati"]
            dati["ripresa"] = {"lavoro": lavoro, "da": identita.user_id,
                               "il": adesso.isoformat()}
            campi = {"lavoro": lavoro, "tentativi": 0, "prossimo_tentativo": adesso,
                     "ultimo_errore": None, "versione": int(pratica["versione"]) + 1,
                     "dati": dati}
            await repo.aggiorna_pratica(session, pid, adesso, **campi)
            payload = {"pratica_id": str(pid), "tipo": pratica["tipo"],
                       "stato": pratica["stato"], "lavoro": lavoro,
                       "versione": campi["versione"], "il": adesso.isoformat()}
            await avvisi.avvisa(session, pratica["tenant_id"], avvisi.PRATICA_RIPRESA,
                                f"{pid}:{campi['versione']}", payload)
            await registra_audit(session, evento="ciclo_vita.pratica_ripresa",
                                 tenant_id=str(pratica["tenant_id"]), user_id=identita.user_id,
                                 dettagli={**payload, "sottotipo": avvisi.PRATICA_RIPRESA,
                                           "ruolo": identita.ruolo, "motivo": motivo,
                                           "tentativi": errore.get("tentativi"),
                                           "ultimo_errore": errore.get("ultimo")})
    except SQLAlchemyError as exc:
        raise pr.errore_db("ripresa_pratica", exc) from None
    logger.warning("pratica_ripresa", pratica_id=str(pid), lavoro=lavoro, ruolo=identita.ruolo)
    return pr.rappresenta({**pratica, **campi})


__all__ = ["motivo_blocco_ripresa", "riprendi"]
