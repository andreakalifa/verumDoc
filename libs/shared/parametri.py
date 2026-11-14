# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/parametri.py — lettura dei parametri del servizio dal database (R07).

Riferimenti: decisioni D13-D19, D28; REGOLE_SVILUPPO §8.1, §8.2 (una sola catena
di risoluzione), §9.3 (un concetto, una query).

I servizi leggono i parametri portati in console (``libs.verumdoc_rules.
parametri_servizio``, più la fine della transizione dell'idempotenza) con UNA
query sulle righe CONSERVATORE ed ENTE di ``impostazioni_valori`` e li
risolvono con il motore (``Risolutore``): stessa catena, stessi limiti e stessa
regola «l'ente può solo ridurre» della console. Le modifiche programmate (D28)
le scrive in ``impostazioni_valori`` il lavoro periodico di onboarding alla
decorrenza: qui non serve sapere nulla delle date.

Cache breve per processo (``PARAMETRI_CACHE_SECONDI``, predefinito 30 s):
una modifica applicata vale entro quel tempo in ogni replica.

Database non inizializzato nel processo (strumenti, test unitari): predefiniti.
Errore del database o valore salvato non valido: predefinito con errore nel log
(i parametri regolano servizio e capacità; i controlli di sicurezza che li usano
decidono da sé se fallire chiusi, vedi ``limite_inattivita_ruolo``).
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import structlog

from libs.verumdoc_rules.catalogo_v4 import REGISTRO as REGISTRO_VERSAMENTO
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    ValoreImpostazioneNonValido,
    codice_limite,
)
from libs.verumdoc_rules.parametri_servizio import (
    INATTIVITA_PREDEFINITA,
    INATTIVITA_RUOLO_SCONOSCIUTO,
    REGISTRO_SERVIZIO,
    codice_inattivita,
)

logger = structlog.get_logger(__name__)

#: Registro dei codici che i servizi leggono con questo modulo.
REGISTRO_PARAMETRI = RegistroImpostazioni([*REGISTRO_SERVIZIO, *(
    v for v in REGISTRO_VERSAMENTO if v.codice == "IDEMPOTENZA_TRANSIZIONE_FINO_AL")])

SQL_PARAMETRI = (
    "SELECT livello, codice, valore::text AS valore FROM public.impostazioni_valori "
    "WHERE codice = ANY(CAST(:codici AS text[])) AND classe_id IS NULL "
    "  AND (livello = 'CONSERVATORE' OR (livello = 'ENTE' AND tenant_id = CAST(:t AS uuid)))"
)

_CACHE_SECONDI = 30.0
_cache: dict[tuple[str | None, tuple[str, ...]], tuple[float, dict[Livello, dict[str, Any]]]] = {}
_MASSIMO_VOCI = 10_000


def imposta_cache_secondi(secondi: float) -> None:
    """Durata della cache (0 = nessuna cache: test e lavori periodici)."""
    global _CACHE_SECONDI
    _CACHE_SECONDI = max(0.0, float(secondi))


def svuota_cache() -> None:
    _cache.clear()


def _tenant(tenant_id: Any) -> str | None:
    if tenant_id in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(tenant_id)))
    except (TypeError, ValueError):
        return None


def livelli_da_righe(righe: Iterable[Mapping[str, Any]], *, decodificati: bool = False
                     ) -> dict[Livello, dict[str, Any]]:
    """``{livello: {codice: valore}}``; ``valore`` è il testo JSON della colonna
    (``valore::text``) o, con ``decodificati``, già il valore (jsonb aggregato)."""
    livelli: dict[Livello, dict[str, Any]] = {Livello.CONSERVATORE: {}, Livello.ENTE: {}}
    for r in righe:
        valore = r["valore"]
        livelli[Livello(r["livello"])][r["codice"]] = (
            valore if decodificati or not isinstance(valore, str) else json.loads(valore))
    return livelli


def con_limiti(codici: Iterable[str]) -> list[str]:
    """I codici con i loro ``LIMITE.<codice>`` (revisione W1): servono alla risoluzione."""
    elenco = list(dict.fromkeys(codici))
    return elenco + [codice_limite(c) for c in elenco if codice_limite(c) in REGISTRO_PARAMETRI
                     and codice_limite(c) not in elenco]


def _scarta(codice: str, livello: Livello, motivo: str, errore: str) -> None:
    from libs.shared.db.metriche import incrementa

    logger.error("parametro_scartato", codice=codice, livello=livello.value, motivo=motivo,
                 errore=errore[:200])
    incrementa("verumdoc_parametri_scartati", codice=codice, livello=livello.value,
               motivo=motivo)


def risolvi(livelli: Mapping[Livello, Mapping[str, Any]], codici: Iterable[str],
            *, ambiente: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Valori effettivi dai livelli letti. ``ambiente``: valori del conservatore
    da variabili d'ambiente (la riga del database prevale). Un valore salvato non
    valido vale come assente (errore nel log): si usa il livello superiore.

    Revisione W3: una riga a un livello che la voce non ammette (es. una riga
    ENTE di una voce solo del conservatore, scritta a mano) è ignorata, con
    errore nel log e metrica ``verumdoc_parametri_scartati_total{motivo="livello"}``."""
    conservatore = {**(ambiente or {}), **dict(livelli.get(Livello.CONSERVATORE, {}))}
    puliti: dict[Livello, dict[str, Any]] = {Livello.CONSERVATORE: {}, Livello.ENTE: {}}
    for livello, valori in ((Livello.CONSERVATORE, conservatore),
                            (Livello.ENTE, livelli.get(Livello.ENTE, {}))):
        for codice, valore in valori.items():
            if codice not in REGISTRO_PARAMETRI:
                continue
            voce = REGISTRO_PARAMETRI[codice]
            if livello not in voce.livelli_ammessi():
                _scarta(codice, livello, "livello", "livello non ammesso per la voce")
                continue
            try:
                voce.converti(valore, livello)
            except ValoreImpostazioneNonValido as exc:
                _scarta(codice, livello, "valore", str(exc))
                continue
            puliti[livello][codice] = valore
    risolutore = Risolutore(REGISTRO_PARAMETRI, FornitoreDaDizionari(puliti))
    contesto = ContestoRisoluzione(adesso=datetime.now(UTC))
    return {c: risolutore.effettiva(contesto, c).valore for c in codici}


async def leggi_livelli(codici: Iterable[str], tenant_id: Any = None, *,
                        session: Any = None) -> dict[Livello, dict[str, Any]]:
    """Righe CONSERVATORE ed ENTE (dell'ente, se indicato) dei codici: una query.

    Con ``session`` usa quella (stessa transazione e stesso ambito del chiamante);
    senza, apre una sessione nell'ambito dell'ente (RLS: le righe globali sono
    leggibili da tutti, quelle dell'ente solo nel suo ambito) con cache breve."""
    from sqlalchemy import text

    elenco = tuple(sorted(set(con_limiti(codici))))
    tid = _tenant(tenant_id)
    parametri = {"codici": list(elenco), "t": tid}
    if session is not None:
        righe = (await session.execute(text(SQL_PARAMETRI), parametri)).mappings().all()
        return livelli_da_righe(righe)
    chiave = (tid, elenco)
    adesso = time.monotonic()
    voce = _cache.get(chiave)
    if voce is not None and voce[0] > adesso:
        return voce[1]
    from libs.shared.db import get_async_session
    from libs.shared.security.ambito_dati import ambito_conservatore, ambito_ente

    contesto = ambito_ente(tid) if tid else ambito_conservatore(
        "parametri del conservatore", registra=False)
    with contesto:
        async with get_async_session() as s:
            righe = (await s.execute(text(SQL_PARAMETRI), parametri)).mappings().all()
    livelli = livelli_da_righe(righe)
    if len(_cache) >= _MASSIMO_VOCI:
        _cache.clear()
    if _CACHE_SECONDI > 0:
        _cache[chiave] = (adesso + _CACHE_SECONDI, livelli)
    return livelli


async def parametri(codici: Iterable[str], tenant_id: Any = None, *, session: Any = None,
                    ambiente: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Valori effettivi dei codici per l'ente (o per il solo conservatore).

    Database non inizializzato: predefiniti. Errore del database: predefiniti
    con avviso nel log."""
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import DBNonInizializzato

    elenco = list(codici)
    try:
        livelli = await leggi_livelli(elenco, tenant_id, session=session)
    except DBNonInizializzato:
        livelli = {}
    except SQLAlchemyError as exc:
        logger.warning("parametri_non_leggibili", errore=type(exc).__name__,
                       tenant_id=_tenant(tenant_id))
        livelli = {}
    return risolvi(livelli, elenco, ambiente=ambiente)


#: R07 (D15): ``STAGING_SCARTATI_GIORNI`` dell'ente del PdV ``p``. UN frammento per
#: gateway (pulizia dello staging) e dashboard-api («Rielabora»): parametri da
#: ``parametri_staging``. Stessa regola del motore (revisione W1/W3): il valore
#: dell'ente vale solo se più breve del TETTO, cioè del limite del conservatore per
#: l'ente (``LIMITE.STAGING_SCARTATI_GIORNI``, riga ENTE) o, senza, del valore del
#: conservatore (:giorni); senza valore dell'ente vale il valore del conservatore,
#: sempre entro il tetto. Righe non numeriche: assenti; fuori intervallo: ricondotte.
SQL_GIORNI_STAGING = (
    "CROSS JOIN LATERAL (SELECT LEAST("
    "  COALESCE((SELECT LEAST(:massimo, GREATEST(:minimo, (v.valore::text)::numeric::int)) "
    "    FROM public.impostazioni_valori v WHERE v.livello = 'ENTE' "
    "    AND v.tenant_id = p.tenant_id AND v.classe_id IS NULL "
    "    AND v.codice = 'LIMITE.STAGING_SCARTATI_GIORNI' AND jsonb_typeof(v.valore) = 'number'), "
    "    :giorni), "
    "  COALESCE((SELECT LEAST(:massimo, GREATEST(:minimo, (v.valore::text)::numeric::int)) "
    "    FROM public.impostazioni_valori v WHERE v.livello = 'ENTE' "
    "    AND v.tenant_id = p.tenant_id AND v.classe_id IS NULL "
    "    AND v.codice = 'STAGING_SCARTATI_GIORNI' AND jsonb_typeof(v.valore) = 'number'), "
    "    :giorni)) AS giorni) g "
)


async def parametri_staging() -> dict[str, int]:
    """Parametri di ``SQL_GIORNI_STAGING``: giorni del conservatore e intervallo."""
    from libs.verumdoc_rules.parametri_servizio import STAGING_SCARTATI_GIORNI

    voce = REGISTRO_PARAMETRI[STAGING_SCARTATI_GIORNI]
    giorni = int((await parametri([STAGING_SCARTATI_GIORNI]))[STAGING_SCARTATI_GIORNI])
    return {
        "giorni": giorni, "minimo": int(voce.minimo or 1), "massimo": int(voce.massimo or giorni)}


def codice_inattivita_ruolo(ruolo: str | None) -> str:
    """Codice del limite d'inattività del ruolo (ruolo non noto: il più breve)."""
    ruolo = str(ruolo or "")
    return codice_inattivita(ruolo if ruolo in INATTIVITA_PREDEFINITA
                             else INATTIVITA_RUOLO_SCONOSCIUTO)


async def limite_inattivita_ruolo(ruolo: str | None, tenant_id: Any, *,
                                  session: Any = None) -> int:
    """D14: limite d'inattività (secondi) della sessione del ruolo nell'ente.

    Errore del database: SQLAlchemyError al chiamante (l'accesso, che deve
    comunque scrivere la sessione, fallisce chiuso)."""
    from libs.shared.db import DBNonInizializzato

    codice = codice_inattivita_ruolo(ruolo)
    try:
        livelli = await leggi_livelli([codice], tenant_id, session=session)
    except DBNonInizializzato:
        livelli = {}
    return int(risolvi(livelli, [codice])[codice]) * 60


async def orizzonte_permanente_anni(ambiente: int | None = None) -> int:
    """R09 (D04, VD-C-070): anni di blocco WORM delle classi permanenti (e degli
    oggetti del registro e del RdV): ``RETENTION_PERMANENTE_ANNI`` del motore
    (riga del conservatore; predefinito 30). ``ambiente`` è il vecchio
    ``RETENTION_PERMANENTE_ORIZZONTE_ANNI`` dei servizi: vale solo senza la riga
    del database. Il rinnovo automatico (lavoratore di ciclo-vita) estende il
    blocco quando ne mancano meno di ``RETENTION_RINNOVO_SOGLIA_ANNI``."""
    from libs.verumdoc_rules.parametri_servizio import RETENTION_PERMANENTE_ANNI

    valori = await parametri([RETENTION_PERMANENTE_ANNI], ambiente=(
        {RETENTION_PERMANENTE_ANNI: int(ambiente)} if ambiente else None))
    return int(valori[RETENTION_PERMANENTE_ANNI])


__all__ = [
    "REGISTRO_PARAMETRI",
    "SQL_GIORNI_STAGING",
    "SQL_PARAMETRI",
    "codice_inattivita_ruolo",
    "con_limiti",
    "imposta_cache_secondi",
    "leggi_livelli",
    "limite_inattivita_ruolo",
    "livelli_da_righe",
    "orizzonte_permanente_anni",
    "parametri",
    "parametri_staging",
    "risolvi",
    "svuota_cache",
]
