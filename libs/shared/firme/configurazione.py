# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme/configurazione.py — scelta del verificatore dalle variabili
d'ambiente (R08, D01). Un solo punto per validation, ciclo-vita e onboarding.

  FIRME_VERIFICATORE   dss | pyhanko            (predefinito pyhanko; in esercizio
                                                 il values di Helm imposta dss)
  FIRME_DSS_URL        http://verifica-firme:8080
  FIRME_DSS_TOKEN      token di servizio (aud verumdoc-verifica-firme), Secret
  FIRME_DSS_TIMEOUT_S  60
  FIRME_RIPIEGO_PDF    true: con dss, i PDF passano a pyHanko se dss non è raggiungibile
                       (revisione X7: ignorato, con un avviso nei log, se pyHanko non ha
                       trust anchor: il ripiego darebbe solo esiti indeterminati)
  FIRME_DIMENSIONE_MAX_MB  60: file più grandi non si inviano al verificatore (esito
                       «firme non verificate»), allineato a VERIFICA_DIMENSIONE_MASSIMA
  FIRME_TEMPO_MAX_S    120: tempo massimo di una verifica pyHanko; R12a (VD-C-147): in un
                       processo separato che oltre il tempo si termina
  FIRME_PROCESSO_SEPARATO  true (predefinito): verifica pyHanko in un processo terminabile
  FIRME_MEMORIA_MAX_MB 0 (nessun limite): limite di memoria del processo di verifica
  FIRME_RADICI_PEM     trust anchor di pyHanko: percorso di un file PEM o PEM in linea
                       (in collaudo la CA di prova; vuoto = nessuna catena attendibile)
  FIRME_REVOCHE_RETE   true: pyHanko scarica CRL/OCSP (in collaudo false)

``imposta_verificatore`` sostituisce il verificatore del processo (test).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import structlog

from libs.shared.firme.dss import VerificatoreDss
from libs.shared.firme.interfaccia import Verificatore
from libs.shared.firme.pyhanko_verificatore import VerificatorePyHanko, carica_certificati_pem
from libs.shared.firme.ripiego import VerificatoreConRipiego

logger = structlog.get_logger(__name__)
_verificatore: Verificatore | None = None
_VERO = {"1", "true", "si", "sì", "yes", "on"}


def _radici(valore: str) -> list:  # type: ignore[type-arg]
    valore = valore.strip()
    if not valore:
        return []
    if "-----BEGIN" in valore:
        return carica_certificati_pem(valore.encode("ascii"))
    percorso = Path(valore)
    file = sorted(percorso.glob("*.pem")) if percorso.is_dir() else [percorso]
    return [c for f in file for c in carica_certificati_pem(f.read_bytes())]


def crea_verificatore(ambiente: Mapping[str, str] | None = None) -> Verificatore:
    env = os.environ if ambiente is None else ambiente
    radici = _radici(env.get("FIRME_RADICI_PEM", ""))
    pyhanko = VerificatorePyHanko(
        radici, rete=env.get("FIRME_REVOCHE_RETE", "false").strip().lower() in _VERO,
        tempo_max_s=float(env.get("FIRME_TEMPO_MAX_S", "120")),
        processo_separato=env.get("FIRME_PROCESSO_SEPARATO", "true").strip().lower() in _VERO,
        memoria_max_mb=int(env.get("FIRME_MEMORIA_MAX_MB", "0") or 0))
    scelta = env.get("FIRME_VERIFICATORE", "pyhanko").strip().lower()
    if scelta == "pyhanko":
        return pyhanko
    if scelta != "dss":
        raise ValueError(f"FIRME_VERIFICATORE non valido: {scelta!r} (dss, pyhanko)")
    dss = VerificatoreDss(env.get("FIRME_DSS_URL", ""), env.get("FIRME_DSS_TOKEN", ""),
                          timeout_s=float(env.get("FIRME_DSS_TIMEOUT_S", "60")),
                          dimensione_max_byte=dimensione_max_byte(env))
    if env.get("FIRME_RIPIEGO_PDF", "true").strip().lower() in _VERO:
        if radici:
            return VerificatoreConRipiego(dss, pyhanko)
        logger.warning("verifica_firme_ripiego_disattivato",
                       motivo="FIRME_RIPIEGO_PDF senza FIRME_RADICI_PEM: pyHanko non ha trust "
                              "anchor")
    return dss


def dimensione_max_byte(ambiente: Mapping[str, str] | None = None) -> int:
    """Dimensione massima di un file inviato al verificatore (byte)."""
    env = os.environ if ambiente is None else ambiente
    return int(float(env.get("FIRME_DIMENSIONE_MAX_MB", "60")) * 1024 * 1024)


def verificatore(ambiente: Mapping[str, str] | None = None) -> Verificatore:
    """Verificatore del processo, creato alla prima richiesta dalla configurazione
    indicata (le Settings del servizio) o, senza, dalle variabili d'ambiente."""
    global _verificatore
    if _verificatore is None:
        _verificatore = crea_verificatore(ambiente)
    return _verificatore


def imposta_verificatore(nuovo: Verificatore | None) -> None:
    global _verificatore
    _verificatore = nuovo


__all__ = ["crea_verificatore", "dimensione_max_byte", "imposta_verificatore", "verificatore"]
