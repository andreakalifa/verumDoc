# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Impostazioni della verifica continua dell'integrità (variabili ``VERIFICA_*``).

Servizio: hashchain, processo separato ``python -m app.verifica_continua``.
Riferimenti: piano R03–R05 §2 (1 milione di documenti al giorno) e §5 punto 4;
REGOLE_SVILUPPO §8.1 (nessuna soglia cablata: tutto impostabile con
predefinito), §12.4 (ritardi come lista), §12.9 (lease), §19.4.

Il ciclo (``VERIFICA_CICLO_GIORNI``, predefinito 365) è una scelta di servizio
del conservatore, non un valore della norma: le Linee guida chiedono la verifica
periodica dell'integrità degli oggetti conservati; la cadenza massima ammessa e
quella dichiarata nel manuale vanno confermate dal responsabile del servizio
(decisione VD-N da registrare). Database e storage sono quelli del servizio
(``app.config.settings``: ``DATABASE_URL``, ``S3_*``).
"""
from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ImpostazioniVerifica(BaseSettings):
    """Valori letti dall'ambiente con prefisso ``VERIFICA_`` (es. ``VERIFICA_CICLO_GIORNI``)."""

    model_config = SettingsConfigDict(env_prefix="VERIFICA_", case_sensitive=False,
                                      extra="ignore")

    # ── Rotazione ─────────────────────────────────────────────────────────
    ciclo_giorni: int = Field(365, ge=1, le=3650, description=(
        "Ogni PdA si rilegge almeno una volta in questo numero di giorni. R09 (D05): "
        "vale solo senza il parametro VERIFICA_CICLO_GIORNI del motore (conservatore, "
        "ente o classe; massimo 365)"))
    prima_entro_giorni: int = Field(30, ge=1, le=3650, description=(
        "Un PdA nuovo riceve la prima verifica in un istante distribuito "
        "uniformemente (per impronta dell'id) entro questo numero di giorni"))
    # ── Lavoro e lease ────────────────────────────────────────────────────
    lotto: int = Field(20, ge=1, le=1000, description="PdA presi in carico per giro")
    lease_secondi: int = Field(900, ge=30, description=(
        "Durata del lease su un lotto: scaduto, il lotto torna disponibile alle altre repliche"))
    concorrenza: int = Field(4, ge=1, le=64, description="PdA verificati in parallelo per replica")
    # ── Budget (per replica: il totale è repliche × budget) ───────────────
    letture_al_secondo: float = Field(50.0, ge=0, description=(
        "Oggetti letti al secondo da storage (0 = senza limite)"))
    byte_al_secondo: int = Field(100 * 1024 * 1024, ge=0, description=(
        "Byte letti al secondo da storage (0 = senza limite)"))
    blocco_lettura_byte: int = Field(1024 * 1024, ge=4096, description=(
        "Dimensione dei blocchi di lettura in streaming (impronta a blocchi)"))
    # ── Errori di lettura ─────────────────────────────────────────────────
    ritardi_errore_lettura_secondi: list[int] = Field(
        default_factory=lambda: [60, 300, 1500, 7200], description=(
            "Attese crescenti fra i tentativi dopo ERRORE_LETTURA; l'ultima si ripete"))
    soglia_errore_lettura: int = Field(3, ge=1, description=(
        "ERRORE_LETTURA consecutivi oltre i quali scatta l'allarme (errore ripetuto)"))
    # ── Semina e riconciliazione della programmazione ─────────────────────
    semina_intervallo_secondi: int = Field(300, ge=5)
    semina_sovrapposizione_minuti: int = Field(120, ge=0, description=(
        "A ogni giro si rilegge questo intervallo prima della posizione raggiunta: "
        "copre i PdA scritti in ritardo con una data di versamento precedente"))
    semina_lotto: int = Field(5000, ge=100)
    semina_lotti_per_giro: int = Field(20, ge=1)
    riconciliazione_giorni: int = Field(7, ge=1, description=(
        "Ogni quanti giorni ripassare l'intera tabella dei PdA (rete di sicurezza §22.3)"))
    riconciliazione_lotti_per_giro: int = Field(20, ge=1)
    preparazione_lotto: int = Field(5000, ge=1, description=(
        "PdA marcati per transazione nella preparazione di una verifica d'ente"))
    # ── Manutenzione ──────────────────────────────────────────────────────
    manutenzione_intervallo_secondi: int = Field(3600, ge=60)
    partizioni_mesi_avanti: int = Field(3, ge=1, le=24)
    copertura_conserva_giorni: int = Field(730, ge=1, description=(
        "Giorni di copertura conservati per giorno; i più vecchi si accorpano"))
    riepilogo_intervallo_secondi: int = Field(300, ge=10, description=(
        "Ogni quanto ricalcolare il riepilogo di tutti gli enti (letto dalle API)"))
    # ── Controlli ─────────────────────────────────────────────────────────
    retention_tolleranza_secondi: int = Field(86400, ge=0, description=(
        "Retention più breve della scadenza di conservazione oltre questa tolleranza "
        "= RETENTION_ASSENTE"))
    manifest_max_byte: int = Field(64 * 1024 * 1024, ge=1024, description=(
        "Il manifest del PdA si tiene in memoria fino a questa dimensione per leggere "
        "l'impronta attesa dell'IPdA"))
    # ── Incidenti (R09, D05) ──────────────────────────────────────────────
    incidente_campione: bool = Field(True, description=(
        "Un incidente di storage avvia una verifica a campione (false: solo l'allarme)"))
    incidente_pausa_ore: int = Field(6, ge=1, le=720, description=(
        "Dopo un incidente di storage (anomalia o errori di lettura ripetuti) parte una "
        "verifica a campione (VERIFICA_CAMPIONE_INCIDENTE_PDA del motore); al più una ogni "
        "tante ore"))
    # ── API ───────────────────────────────────────────────────────────────
    richiesta_max_pda: int = Field(1000, ge=1, le=100_000)
    # ── Processo ──────────────────────────────────────────────────────────
    attesa_inattivo_secondi: float = Field(15.0, ge=0.1)
    metriche_porta: int = Field(9108, ge=0, le=65535, description="0 = metriche non esposte")
    detentore: str = Field("", max_length=200, description=(
        "Nome della replica nei lease e nel registro (predefinito: host e processo)"))

    @field_validator("ritardi_errore_lettura_secondi")
    @classmethod
    def _ritardi_validi(cls, valore: list[int]) -> list[int]:
        if not valore or any(r < 1 for r in valore):
            raise ValueError("servono uno o più ritardi positivi (secondi)")
        return valore


impostazioni = ImpostazioniVerifica()
