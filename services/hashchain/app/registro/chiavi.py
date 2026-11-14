# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Chiavi e identità del registro delle impronte (contratto K2 di R04).

Servizio: hashchain. Riferimenti: REGOLE_SVILUPPO §19.3 (segreti solo da
ambiente), §19.4 (impostazioni pericolose fermano l'avvio).

  - chiave del registro: ``CATENA_CHIAVE_PRIVATA`` nel formato
    ``PRIVATE+KEY+verumdoc-registro+<keyhash>+<base64>`` (nota firmata C2SP);
    senza, il servizio non parte, tranne con ENV=test (chiave effimera in memoria);
  - origine dei checkpoint: ``verumdoc.it/registro/v1`` in produzione,
    ``verumdoc.it/registro/v1/<ENV>`` negli altri ambienti (un checkpoint di
    sviluppo non si scambia mai per uno di produzione);
  - verifier key del testimone (``TESTIMONE_VERIFICATORE``): obbligatoria se
    ``TESTIMONE_URL`` è impostato;
  - testimone (``TESTIMONE_URL``): obbligatorio con ENV=production (rev4
    rilievo 8: togliere una variabile non deve far sparire in silenzio la terza
    garanzia del piano §5); fuori da produzione è facoltativo e, se manca, i
    checkpoint sono dichiarati ``NON_CONFIGURATO``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import structlog

from libs.catena import nota

logger = structlog.get_logger(__name__)

NOME_CHIAVE_REGISTRO = "verumdoc-registro"
NOME_CHIAVE_TESTIMONE = "verumdoc-testimone"
ORIGINE_BASE = "verumdoc.it/registro/v1"
AMBIENTE_PRODUZIONE = "production"


class ConfigurazioneCatenaNonValida(RuntimeError):
    """Configurazione del registro incompleta o incoerente (avvio rifiutato)."""


def origine_registro(cfg: Any) -> str:
    """Origine dei checkpoint per l'ambiente (o quella impostata esplicitamente)."""
    if getattr(cfg, "catena_origine", ""):
        return str(cfg.catena_origine).strip()
    ambiente = str(cfg.env).strip().lower()
    return ORIGINE_BASE if ambiente == AMBIENTE_PRODUZIONE else f"{ORIGINE_BASE}/{ambiente}"


def produzione(cfg: Any) -> bool:
    """True se l'ambiente è quello di produzione."""
    return str(cfg.env).strip().lower() == AMBIENTE_PRODUZIONE


def testimone_facoltativo(cfg: Any) -> bool:
    """L'ambiente ammette un registro senza testimone (mai in produzione)."""
    return not produzione(cfg)


def problemi_configurazione(cfg: Any) -> list[str]:
    """Problemi della configurazione del registro (mai il valore dei segreti)."""
    problemi: list[str] = []
    test = str(cfg.env).lower() == "test"
    if produzione(cfg) and not str(cfg.testimone_url or "").strip():
        problemi.append("TESTIMONE_URL obbligatorio con ENV=production: il testimone "
                        "indipendente non è facoltativo in esercizio (PIANO §5.3)")
    privata = cfg.catena_chiave_privata.get_secret_value()
    if not privata and not test:
        problemi.append("CATENA_CHIAVE_PRIVATA non impostata (tools/dev/genera_segreti_dev.py)")
    elif privata:
        try:
            nota.analizza_firmatario(privata, nome_atteso=NOME_CHIAVE_REGISTRO)
        except nota.ErroreNota as exc:
            problemi.append(f"CATENA_CHIAVE_PRIVATA non valida: {exc}")
    if cfg.testimone_url:
        if not cfg.testimone_verificatore:
            problemi.append("TESTIMONE_URL impostato senza TESTIMONE_VERIFICATORE")
        else:
            try:
                v = nota.analizza_verificatore(cfg.testimone_verificatore)
                if v.nome != NOME_CHIAVE_TESTIMONE:
                    problemi.append(f"TESTIMONE_VERIFICATORE con nome {v.nome!r}, "
                                    f"atteso {NOME_CHIAVE_TESTIMONE!r}")
            except nota.ErroreNota as exc:
                problemi.append(f"TESTIMONE_VERIFICATORE non valido: {exc}")
        if not cfg.testimone_service_token.get_secret_value() and not test:
            problemi.append("TESTIMONE_URL impostato senza TESTIMONE_SERVICE_TOKEN")
    for nome in ("catena_checkpoint_secondi", "catena_controfirma_max_secondi",
                 "catena_sequenziatore_intervallo_secondi", "catena_lease_secondi",
                 "testimone_sonda_secondi"):
        if float(getattr(cfg, nome, 1.0)) <= 0:
            problemi.append(f"{nome.upper()} deve essere positivo")
    for nome in ("catena_sequenziatore_lotto", "catena_completamento_lotto",
                 "catena_worm_segmento_foglie"):
        if int(getattr(cfg, nome)) < 1:
            problemi.append(f"{nome.upper()} deve essere almeno 1")
    return problemi


def verifica_configurazione(cfg: Any) -> None:
    """All'avvio: solleva ConfigurazioneCatenaNonValida con l'elenco dei problemi."""
    problemi = problemi_configurazione(cfg)
    if problemi:
        raise ConfigurazioneCatenaNonValida("; ".join(problemi))


@lru_cache(maxsize=4)
def _firmatario(privata: str, test: bool) -> nota.Firmatario:
    if privata:
        return nota.analizza_firmatario(privata, nome_atteso=NOME_CHIAVE_REGISTRO)
    if not test:
        raise ConfigurazioneCatenaNonValida("CATENA_CHIAVE_PRIVATA non impostata")
    logger.warning("catena_chiave_effimera_di_test")
    return nota.analizza_firmatario(nota.genera_chiave(NOME_CHIAVE_REGISTRO)[0])


def firmatario_registro(cfg: Any) -> nota.Firmatario:
    """Firmatario del registro (chiave di servizio Ed25519)."""
    return _firmatario(cfg.catena_chiave_privata.get_secret_value(),
                       str(cfg.env).lower() == "test")


def verificatore_testimone(cfg: Any) -> nota.Verificatore | None:
    """Verifier key del testimone, se configurata."""
    if not cfg.testimone_verificatore:
        return None
    return nota.analizza_verificatore(cfg.testimone_verificatore)
