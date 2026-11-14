# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Fornitore dei valori del motore delle regole letti dal database (gateway).

Servizio: gateway. Riferimenti: decisione VD-P-01, REGOLE_SVILUPPO §8.2, §8.3, §8.14;
piano R03-R05 §6.1; contratto R05 Q1. Il motore (``libs.verumdoc_rules``) è puro:
questo modulo legge PRIMA, in modo asincrono, tutti i valori che servono per un
contesto (cliente + classe + accordo) e li consegna al motore. Query e
conversione delle righe sono quelle UNICHE di ``libs.verumdoc_rules.lettura``,
condivise con onboarding (console, anteprima, validatore preventivo) e con
``tools/confronta_regole.py`` (§9.1, §9.3).

Livelli valorizzati:
  - CONSERVATORE: variabili d'ambiente del gateway (``IDEMPOTENZA_OBBLIGATORIA``,
    ``IDEMPOTENZA_TRANSIZIONE_FINO_AL``) e righe ``impostazioni_valori`` di livello
    CONSERVATORE (la riga prevale sulla variabile);
  - ENTE: righe ``impostazioni_valori`` del cliente;
  - CLASSE: righe ``impostazioni_valori`` della classe e le colonne di
    ``classi_documentali`` (``formati_ammessi`` e ``metadati_obbligatori`` se non
    vuote, ``max_dimensione_mb`` sempre), che prevalgono sulle righe;
  - ACCORDO (R05): l'accordo di versamento in vigore indicato dall'indice
    (campo ``accordo``) oppure quello dell'ente per la classe o per tutte le
    classi (``libs.verumdoc_rules.accordi.seleziona_accordo``);
  - PDV (R05): la personalizzazione motivata dell'indice (campo ``override``).
Profilo di metadati: la versione del profilo della classe valida nel giorno
(italiano) del versamento, composta con il profilo di tipologia dell'Allegato 5
scelto con ``PROFILO_TIPOLOGIA``; nessuno dei due = comportamento di prima.

Quattro letture indicizzate per versamento (valori, profili della classe,
accordi dell'ente, profili di tipologia del conservatore): nessuna scansione.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from app.config import settings
from libs.verumdoc_rules import REGISTRO, AmbienteRegole, Livello, ProfiloMetadati
from libs.verumdoc_rules.accordi import AccordoVersamento
from libs.verumdoc_rules.catalogo_v1 import (
    IDEMPOTENZA_OBBLIGATORIA,
    IDEMPOTENZA_TRANSIZIONE_FINO_AL,
)
from libs.verumdoc_rules.impostazioni import giorno_locale
from libs.verumdoc_rules.lettura import (
    SQL_ACCORDI_ENTE,
    SQL_PROFILI_CLASSE,
    SQL_PROFILI_TIPOLOGIA,
    SQL_VALORI_LIVELLI,
    accordi_da_righe,
    livelli_da_righe,
    prepara_ambiente,
    profili_classe_da_righe,
    profili_tipologia_da_righe,
)
from libs.verumdoc_rules.lettura import (
    valori_colonne_classe as _valori_colonne,
)
from libs.verumdoc_rules.override import OverrideVersamento, valida_override


@dataclass(frozen=True)
class ClasseLetta:
    """Riga di ``classi_documentali`` che serve al versamento."""

    id: uuid.UUID
    codice: str
    tenant_id: uuid.UUID | None
    attiva: bool
    formati_ammessi: tuple[str, ...] = ()
    metadati_obbligatori: tuple[str, ...] = ()
    max_dimensione_mb: int | None = None
    tipologia_xml: str | None = None


@dataclass
class ValoriLetti:
    """Valori per livello, versioni di profilo, accordi e profili di tipologia
    letti per un contesto."""

    livelli: dict[Livello, dict[str, Any]] = field(default_factory=dict)
    profili: list[ProfiloMetadati] = field(default_factory=list)
    accordi: list[AccordoVersamento] = field(default_factory=list)
    profili_tipologia: dict[str, list[ProfiloMetadati]] = field(default_factory=dict)


def _json(testo: str | None) -> Any:
    """Le colonne jsonb si leggono come testo (``::text``) e si decodificano qui:
    così un valore stringa JSON non si confonde con il JSON ancora da decodificare."""
    return None if testo is None else json.loads(testo)


def _lista(testo: str | None) -> tuple[str, ...]:
    return tuple(str(v) for v in (_json(testo) or ()))


def classe_da_riga(riga: Any) -> ClasseLetta:
    return ClasseLetta(
        id=riga["id"],
        codice=riga["codice"],
        tenant_id=riga["tenant_id"],
        attiva=bool(riga["attiva"]),
        formati_ammessi=_lista(riga["formati_ammessi"]),
        metadati_obbligatori=_lista(riga["metadati_obbligatori"]),
        max_dimensione_mb=riga["max_dimensione_mb"],
        tipologia_xml=str(riga["tipologia_xml"]) if riga["tipologia_xml"] is not None else None,
    )


async def leggi_classe(session: Any, codice: str, tenant_id: uuid.UUID) -> ClasseLetta | None:
    """Classe visibile al cliente: la sua, altrimenti quella di base con lo stesso codice."""
    riga = (
        (
            await session.execute(
                text(
                    "SELECT id, codice, tenant_id, attiva, "
                    "formati_ammessi::text AS formati_ammessi, "
                    "metadati_obbligatori::text AS metadati_obbligatori, "
                    "max_dimensione_mb, tipologia_xml::text AS tipologia_xml "
                    "FROM public.classi_documentali "
                    "WHERE codice = :c AND (tenant_id = :t OR tenant_id IS NULL) "
                    "ORDER BY tenant_id NULLS LAST LIMIT 1"
                ),
                {"c": codice, "t": tenant_id},
            )
        )
        .mappings()
        .first()
    )
    return classe_da_riga(riga) if riga else None


def valori_conservatore_da_ambiente() -> dict[str, Any]:
    """Livello CONSERVATORE dalle variabili d'ambiente (solo quelle impostate)."""
    valori: dict[str, Any] = {}
    if settings.idempotenza_obbligatoria is not None:
        valori[IDEMPOTENZA_OBBLIGATORIA] = settings.idempotenza_obbligatoria
    if settings.idempotenza_transizione_fino_al is not None:
        valori[IDEMPOTENZA_TRANSIZIONE_FINO_AL] = settings.idempotenza_transizione_fino_al
    return valori


def valori_colonne_classe(classe: ClasseLetta) -> dict[str, Any]:
    """Livello CLASSE dalle colonne di ``classi_documentali`` (§8.14: lista vuota = eredita)."""
    return _valori_colonne(classe.formati_ammessi, classe.metadati_obbligatori,
                           classe.max_dimensione_mb)


async def leggi_valori(
    session: Any, *, tenant_id: uuid.UUID, classe: ClasseLetta, giorno: date
) -> ValoriLetti:
    """Letture del contesto con le query uniche di ``libs.verumdoc_rules.lettura``."""
    parametri = {"t": tenant_id, "c": classe.id, "g": giorno}
    righe = (await session.execute(text(SQL_VALORI_LIVELLI), parametri)).mappings().all()
    profili = (await session.execute(text(SQL_PROFILI_CLASSE), parametri)).mappings().all()
    accordi = (await session.execute(text(SQL_ACCORDI_ENTE), parametri)).mappings().all()
    tipologie = (await session.execute(text(SQL_PROFILI_TIPOLOGIA))).mappings().all()
    return ValoriLetti(
        livelli=livelli_da_righe(righe, conservatore_ambiente=valori_conservatore_da_ambiente(),
                                 colonne_classe=valori_colonne_classe(classe)),
        profili=profili_classe_da_righe(profili, classe.codice),
        accordi=accordi_da_righe(accordi),
        profili_tipologia=profili_tipologia_da_righe(tipologie),
    )


def ambiente_da_valori(
    valori: ValoriLetti, *, tenant_id: uuid.UUID, classe: ClasseLetta, adesso: datetime,
    accordo: str | None = None, override: OverrideVersamento | None = None,
) -> AmbienteRegole:
    """Ambiente puro del motore per il contesto (cliente, classe, accordo, istante).

    Solleva ``AccordoNonValido`` se l'indice indica un accordo che non vale."""
    return prepara_ambiente(
        registro=REGISTRO, livelli=valori.livelli, adesso=adesso, tenant_id=str(tenant_id),
        classe_id=str(classe.id), profili_classe=valori.profili,
        profili_tipologia_db=valori.profili_tipologia, accordi=valori.accordi,
        accordo_richiesto=accordo, override=override,
    )


async def carica_ambiente(
    session: Any, *, tenant_id: uuid.UUID, classe: ClasseLetta, adesso: datetime,
    accordo: str | None = None, override: Mapping[str, Any] | None = None,
) -> AmbienteRegole:
    """Legge i valori dal database e prepara l'ambiente del motore delle regole.

    ``accordo`` e ``override`` vengono dall'indice (R05 Q1). Solleva
    ``OverrideNonAmmesso`` (personalizzazione non ammessa, colpa del versante),
    ``AccordoNonValido`` (accordo dell'indice non valido, colpa del versante),
    ``ValoreImpostazioneNonValido``, ``ProfiloNonValido`` o ``AccordiAmbigui`` se
    la configurazione salvata non è valida (errore del servizio)."""
    personalizzazione = valida_override(override, REGISTRO)
    valori = await leggi_valori(
        session, tenant_id=tenant_id, classe=classe, giorno=giorno_locale(adesso)
    )
    ambiente = ambiente_da_valori(valori, tenant_id=tenant_id, classe=classe, adesso=adesso,
                                  accordo=accordo, override=personalizzazione)
    # Risoluzione anticipata di TUTTE le impostazioni: una configurazione rotta
    # emerge prima di trasferire i file, e la fotografia del PdV è completa (§8.3).
    for codice in REGISTRO.codici():
        ambiente.effettiva(codice)
    return ambiente
