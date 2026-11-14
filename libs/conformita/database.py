# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/database.py — configurazione in esercizio letta dal database (R15).

SOLA LETTURA: la prima istruzione della transazione è ``SET TRANSACTION READ ONLY`` (una
scrittura per errore fallisce nel database, non solo nel codice); l'ambito della Row Level
Security è quello del conservatore (``app.ambito``), impostato nella stessa transazione.
Da riga di comando si usa il login dell'auditor (gruppo ``verumdoc_auditor``, migrazione
``20261111_058``: SELECT sulle sole tabelle e colonne lette qui); dalla console il login di
dashboard-api.

Si legge SOLO configurazione del servizio (classi documentali del servizio, profili,
impostazioni del conservatore, fornitori d'identità, di firma e marca, canali PEC): niente
dati di esercizio che cambiano di continuo (contatori, checkpoint) e nessun dato personale.
Così a parità di configurazione la generazione produce lo stesso documento e non una
versione nuova. Un errore di lettura fa fallire la generazione (mai un documento parziale
presentato come completo).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

FORMATO = "verumdoc-fatti-database-1"

#: Tabelle e colonne lette (per i GRANT dell'auditor e per l'elenco delle fonti)
LETTURE: dict[str, tuple[str, ...]] = {
    "classi_documentali": ("codice", "nome", "tenant_id", "tipologia_xml",
                           "anni_conservazione", "conservazione_permanente", "formati_ammessi",
                           "metadati_obbligatori", "max_dimensione_mb", "attiva"),
    "profili_tipologia": ("codice", "versione", "valido_dal", "valido_al", "adozione",
                          "norm_ref"),
    "impostazioni_valori": ("livello", "tenant_id", "classe_id", "codice", "valore",
                            "created_at"),
    "fornitori_identita": ("tipo", "nome", "attivo"),
    "fornitori_firma": ("codice", "ruolo", "tipo", "nome", "attivo", "priorita"),
    "canali_pec": ("codice", "tipo", "nome", "attivo", "priorita"),
}

_CLASSI = (
    "SELECT codice, nome, tenant_id IS NULL AS globale, tipologia_xml::text AS tipologia, "
    "anni_conservazione, conservazione_permanente, formati_ammessi, metadati_obbligatori, "
    "max_dimensione_mb FROM public.classi_documentali WHERE attiva ORDER BY codice, tenant_id")
_PROFILI = ("SELECT codice, versione, valido_dal, valido_al, adozione, norm_ref "
            "FROM public.profili_tipologia ORDER BY codice, versione")
_IMPOSTAZIONI = (
    "SELECT DISTINCT ON (codice) codice, valore FROM public.impostazioni_valori "
    "WHERE livello = 'CONSERVATORE' AND tenant_id IS NULL AND classe_id IS NULL "
    "ORDER BY codice, created_at DESC")
_IDENTITA = "SELECT tipo, nome, attivo FROM public.fornitori_identita ORDER BY tipo"
_FIRMA = ("SELECT codice, ruolo, tipo, nome, attivo, priorita FROM public.fornitori_firma "
          "ORDER BY ruolo, priorita, codice")
_PEC = ("SELECT codice, tipo, nome, attivo, priorita FROM public.canali_pec "
        "ORDER BY priorita, codice")


def _json(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, list | tuple):
        return [_json(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json(x) for k, x in v.items()}
    return v


async def _righe(connessione: Any, sql: str) -> list[dict[str, Any]]:
    from sqlalchemy import text

    risultato = await connessione.execute(text(sql))
    return [{k: _json(v) for k, v in dict(r).items()} for r in risultato.mappings().all()]


async def raccogli(connessione: Any) -> dict[str, Any]:
    """Fatti della configurazione. ``connessione`` è una AsyncConnection o AsyncSession di
    SQLAlchemy in una transazione NUOVA (la prima istruzione la rende di sola lettura)."""
    from sqlalchemy import text

    await connessione.execute(text("SET TRANSACTION READ ONLY"))
    await connessione.execute(text(
        "SELECT set_config('app.ambito', 'conservatore', true), "
        "set_config('app.ente', '', true)"))
    classi = await _righe(connessione, _CLASSI)
    globali = [{k: v for k, v in c.items() if k != "globale"} for c in classi if c["globale"]]
    return {
        "formato": FORMATO,
        "disponibile": True,
        "classi_globali": globali,
        "classi_enti": sum(1 for c in classi if not c["globale"]),
        "profili": await _righe(connessione, _PROFILI),
        "impostazioni": {r["codice"]: r["valore"]
                         for r in await _righe(connessione, _IMPOSTAZIONI)},
        "fornitori_identita": await _righe(connessione, _IDENTITA),
        "fornitori_firma": await _righe(connessione, _FIRMA),
        "canali_pec": await _righe(connessione, _PEC),
    }


def vuoti() -> dict[str, Any]:
    """Fatti di un servizio senza database (edizione del repository) o con database vuoto:
    ogni dato che verrebbe da qui risulterà «DA COMPILARE»."""
    return {"formato": FORMATO, "disponibile": False, "classi_globali": [], "classi_enti": 0,
            "profili": [], "impostazioni": {}, "fornitori_identita": [],
            "fornitori_firma": [], "canali_pec": []}


__all__ = ["FORMATO", "LETTURE", "raccogli", "vuoti"]
