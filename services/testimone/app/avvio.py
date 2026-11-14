# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/testimone/app/avvio.py — controlli d'avvio del testimone (REGOLE §19.4).

Il testimone NON parte, in nessun ambiente, se manca qualcosa che gli serve per dire la
verità: senza la propria chiave non può controfirmare, senza la chiave del
registro non può verificare, senza impronte di token chiunque potrebbe
inviargli checkpoint. Nessun ripiego silenzioso.

In ``production`` in più: database non locale e senza credenziali d'esempio,
e la chiave del testimone deve essere diversa da quella del registro (stessa
chiave = nessuna indipendenza).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import Settings
from app.domain.checkpoint import (
    ConfigurazioneNonValida,
    ContestoTestimone,
    carica_chiave_testimone,
    controlla_verificatori,
)
from app.osservabilita import MetricheTestimone

_IMPRONTA = re.compile(r"^[0-9a-f]{64}$")
_ORIGINE = re.compile(r"^[^\s+]+$")


@dataclass(frozen=True)
class ConfigurazioneAvvio:
    env: str
    database_url: str
    chiave_privata: str
    nome_chiave: str
    chiavi_registro: list[str]
    origini: list[str]
    impronte_token: list[str]
    max_corpo_byte: int
    max_righe_prova: int

    @classmethod
    def da_impostazioni(cls, s: Settings) -> ConfigurazioneAvvio:
        return cls(
            env=s.env.lower(),
            database_url=s.database_url,
            chiave_privata=s.testimone_chiave_privata.get_secret_value(),
            nome_chiave=s.testimone_nome_chiave,
            chiavi_registro=s.chiavi_registro,
            origini=s.origini_ammesse,
            impronte_token=s.impronte_token_registro,
            max_corpo_byte=s.testimone_max_corpo_byte,
            max_righe_prova=s.testimone_max_righe_prova,
        )


def problemi_configurazione(c: ConfigurazioneAvvio) -> list[str]:
    """Elenco dei problemi che impediscono l'avvio (vuoto = si parte)."""
    problemi: list[str] = []
    chiave = None
    try:
        chiave = carica_chiave_testimone(c.chiave_privata, c.nome_chiave)
    except ConfigurazioneNonValida as exc:
        problemi.append(str(exc))
    try:
        controlla_verificatori(c.chiavi_registro)
    except ConfigurazioneNonValida as exc:
        problemi.append(str(exc))
    if chiave is not None and any(
        v.split("+", 2)[-1] == chiave.verificatore.split("+", 2)[-1] for v in c.chiavi_registro
    ):
        problemi.append(
            "La chiave del testimone coincide con una chiave del registro: nessuna indipendenza"
        )
    if not c.origini:
        problemi.append("TESTIMONE_ORIGINI_AMMESSE vuota")
    for o in c.origini:
        if not _ORIGINE.match(o):
            problemi.append(f"Origine non valida: {o!r}")
    if not c.impronte_token:
        problemi.append("TESTIMONE_IMPRONTE_TOKEN_REGISTRO non impostata")
    for i in c.impronte_token:
        if not _IMPRONTA.match(i):
            problemi.append(
                "TESTIMONE_IMPRONTE_TOKEN_REGISTRO: ogni voce è uno SHA-256 "
                "esadecimale (64 caratteri)"
            )
            break
    if c.max_corpo_byte < 1024 or c.max_righe_prova < 64:
        problemi.append("Limiti del corpo troppo bassi per una prova di consistenza")
    if c.env == "production":
        url = c.database_url.lower()
        if "dev_only" in url or "localhost" in url or "127.0.0.1" in url:
            problemi.append("DATABASE_URL di sviluppo in produzione")
    return problemi


def carica_contesto(c: ConfigurazioneAvvio, metriche: MetricheTestimone) -> ContestoTestimone:
    """Contesto delle richieste; ``ConfigurazioneNonValida`` con tutti i problemi."""
    problemi = problemi_configurazione(c)
    if problemi:
        raise ConfigurazioneNonValida("Il testimone non parte: " + "; ".join(problemi))
    return ContestoTestimone(
        chiave=carica_chiave_testimone(c.chiave_privata, c.nome_chiave),
        verificatori_registro=list(c.chiavi_registro),
        origini=list(c.origini),
        impronte_token=list(c.impronte_token),
        max_corpo_byte=c.max_corpo_byte,
        max_righe_prova=c.max_righe_prova,
        metriche=metriche,
    )


__all__ = ["ConfigurazioneAvvio", "carica_contesto", "problemi_configurazione"]
