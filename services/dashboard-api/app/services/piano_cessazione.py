# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/dashboard-api/app/services/piano_cessazione.py
Piano di cessazione generato dai dati reali — VD-C-013.

Struttura dell'Allegato B al Regolamento AgID sui servizi di conservazione
(Det. 455/2021, modificata dalla Det. 629/2021): 1 introduzione e responsabili,
2 definizioni, 3 riferimenti normativi, 4 terze parti, 5 analisi dei rischi,
6 programmazione, 7 processo di cessazione, 8 cancellazione degli archivi.

Come per il manuale: dati del conservatore solo da configurazione («da
compilare» se mancano), elenchi di consistenza per produttore letti dal DB,
funzioni non realizzate (export massivo, PEC con ricevute, cancellazione con
prova di distruzione) marcate «da realizzare» nel testo e in `stato_sezione`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from app.services.manuale import (
    GENERATA,
    NON_DISPONIBILE,
    SEZ_DA_COMPILARE,
    DatiConservatore,
    _impronta,
    _mancanti,
    dati_conservatore_strutturati,
    mostra,
    riepilogo,
    sezione,
    stato_firma_documento,
)

logger = structlog.get_logger(__name__)

RIFERIMENTI_NORMATIVI_CESSAZIONE = [
    "Regolamento AgID sui criteri per la fornitura dei servizi di conservazione "
    "(Det. 455/2021, mod. Det. 629/2021), art. 8 e Allegato B",
    "Linee guida AgID sulla formazione, gestione e conservazione dei documenti "
    "informatici (maggio 2021)",
    "D.Lgs. 82/2005 (CAD), art. 44",
    "Reg. (UE) 2016/679 (GDPR), artt. 28 e 32",
    "UNI ISO 31000 (gestione del rischio); ETSI TS 119 511 §7.12, ETSI EN 319 401",
    "UNI 11386:2020 (SInCRO) per i pacchetti scambiati con il subentrante",
]

DEFINIZIONI = {
    "PdV": "pacchetto di versamento inviato dal produttore",
    "PdA": "pacchetto di archiviazione conservato dal sistema",
    "PdD": "pacchetto di distribuzione consegnato all'utente o al conservatore subentrante",
    "conservatore cessante": "il soggetto che cessa il servizio di conservazione",
    "conservatore subentrante": "il soggetto che prende in carico i PdA",
    "elenco di consistenza": "elenco, per produttore, dei PdA con volumi e numero di documenti",
}


def elenchi_consistenza(righe: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Elenchi di consistenza per produttore (dal DB): numero di PdA e volume."""
    return [
        {
            "produttore": r.get("ragione_sociale"),
            "codice_fiscale": r.get("codice_fiscale"),
            "stato_cliente": r.get("stato"),
            "numero_pda": int(r.get("numero_pda") or 0),
            "volume_byte": int(r.get("volume_byte") or 0),
            "numero_documenti": int(r.get("numero_documenti") or 0),
        }
        for r in righe or []
    ]


SQL_CONSISTENZA = (
    "SELECT t.id::text AS tenant_id, t.ragione_sociale, t.codice_fiscale, t.stato::text AS stato "
    "FROM public.tenants t ORDER BY t.ragione_sociale"
)


async def leggi_consistenza() -> tuple[list[dict[str, Any]] | None, str | None]:
    """Elenchi di consistenza per produttore dal DB; in errore (None, motivo).

    R04 dati: numero di PdA, di documenti e volume dai contatori per ente
    (``libs.shared.db.contatori``), sulla sessione di sola lettura: prima tre
    ``COUNT``/``SUM`` per ente su ``aip`` e ``documenti`` (centinaia di milioni
    di righe all'anno)."""
    from sqlalchemy import text

    from libs.shared.db import get_async_session_lettura
    from libs.shared.db.contatori import consistenza_per_ente

    try:
        async with get_async_session_lettura() as session:
            risultato = await session.execute(text(SQL_CONSISTENZA))
            righe = [dict(r) for r in risultato.mappings().all()]
            contatori = await consistenza_per_ente(session)
        vuoto = {"numero_pda": 0, "numero_documenti": 0, "volume_byte": 0}
        for r in righe:
            r.update(contatori.get(r.pop("tenant_id"), vuoto))
        return righe, None
    except Exception as exc:  # noqa: BLE001 — la sezione diventa «non_disponibile»
        logger.warning("piano_cessazione_consistenza_non_disponibile", errore=type(exc).__name__)
        return None, f"lettura degli elenchi di consistenza non riuscita ({type(exc).__name__})"


def genera_piano_cessazione(
    dati: DatiConservatore,
    *,
    adesso: datetime,
    consistenza: list[dict[str, Any]] | None = None,
    errore_consistenza: str | None = None,
) -> dict[str, Any]:
    """Genera il Piano di cessazione (Allegato B) dai dati reali."""
    c = dati_conservatore_strutturati(dati)
    elenchi = elenchi_consistenza(consistenza)

    if errore_consistenza:
        sez_analisi = sezione(
            "7.2",
            "Analisi preliminare dei PdA",
            f"Elenchi di consistenza non disponibili: {errore_consistenza}.",
            stato=NON_DISPONIBILE,
        )
    elif not elenchi:
        sez_analisi = sezione(
            "7.2",
            "Analisi preliminare dei PdA",
            "Nessun produttore con PdA conservati: elenchi di consistenza vuoti.",
            stato=GENERATA,
            dati={"elenchi_consistenza": []},
            campi_mancanti=[],
        )
    else:
        totale_pda = sum(e["numero_pda"] for e in elenchi)
        totale_byte = sum(e["volume_byte"] for e in elenchi)
        sez_analisi = sezione(
            "7.2",
            "Analisi preliminare dei PdA",
            f"Elenchi di consistenza generati dal sistema per {len(elenchi)} produttori: "
            f"{totale_pda} PdA, {totale_byte} byte complessivi. Presenza di dati personali, "
            "aggregazioni, piani di classificazione e di scarto per produttore: da indicare.",
            dati={
                "elenchi_consistenza": elenchi,
                "totale_pda": totale_pda,
                "totale_byte": totale_byte,
            },
            campi_mancanti=["dati personali e piani di classificazione per produttore"],
            stato=SEZ_DA_COMPILARE,
        )

    processo = [
        sezione(
            "7.1",
            "Comunicazioni",
            "La cessazione si comunica ad AgID e ai clienti almeno 60 giorni prima, con "
            "comunicazione firmata dal legale rappresentante (Regolamento, art. 8). Le "
            f"comunicazioni partono dalla PEC del conservatore {mostra(c['pec'])}.",
            funzioni=["notifiche_email", "firma_marca_qualificate"],
            campi_mancanti=_mancanti(c, ["pec"]),
        ),
        sez_analisi,
        sezione(
            "7.3",
            "Trasferimento dei PdA",
            "Export massivo dei PdA come PdD di migrazione conformi a UNI 11386:2020, "
            "con le misure dell'art. 32 GDPR.",
            funzioni=["esibizione", "ipda_sincro"],
        ),
        sezione(
            "7.4",
            "Infrastruttura di migrazione",
            "Canale di trasferimento verso il subentrante e accesso garantito ai dati "
            "per un periodo congruo dopo la cessazione.",
            funzioni=["esibizione"],
            campi_mancanti=["canale di trasferimento"],
        ),
        sezione(
            "7.5",
            "Controlli del conservatore cessante",
            "Verifica delle impronte di ogni PdA dopo il trasferimento e verbale firmato.",
            funzioni=["verifiche_periodiche", "firma_marca_qualificate"],
        ),
        sezione(
            "7.6",
            "Presa in carico da parte del subentrante",
            "Ricevuta di presa in carico del subentrante per ogni produttore.",
            campi_mancanti=["conservatore subentrante", "modalità di presa in carico"],
        ),
    ]

    sezioni = [
        sezione(
            "1",
            "Introduzione e responsabili",
            f"Piano di cessazione del servizio di conservazione di "
            f"{mostra(c['denominazione'])} (C.F. {mostra(c['codice_fiscale'])}). "
            f"Responsabile del servizio di conservazione: "
            f"{mostra(c['responsabile_servizio_nome'])}.",
            campi_mancanti=_mancanti(
                c, ["denominazione", "codice_fiscale", "responsabile_servizio_nome", "pec"]
            ),
            dati={"conservatore": c},
        ),
        sezione(
            "2", "Definizioni", "Definizioni usate nel piano.", dati={"definizioni": DEFINIZIONI}
        ),
        sezione(
            "3",
            "Riferimenti normativi",
            "Norme e standard applicati.",
            dati={"riferimenti": RIFERIMENTI_NORMATIVI_CESSAZIONE},
        ),
        sezione(
            "4",
            "Terze parti",
            "Fornitori coinvolti, compresi i responsabili del trattamento ex art. 28 GDPR.",
            campi_mancanti=["elenco delle terze parti e dei responsabili del trattamento"],
        ),
        sezione(
            "5",
            "Analisi dei rischi",
            "Analisi dei rischi della cessazione secondo UNI ISO 31000, compresi i rischi "
            "per i dati personali.",
            campi_mancanti=["analisi dei rischi"],
        ),
        sezione(
            "6",
            "Programmazione",
            "Matrice RACI, verifiche e cronoprogramma delle attività.",
            campi_mancanti=["matrice RACI", "cronoprogramma"],
        ),
        {
            "numero": "7",
            "titolo": "Processo di cessazione",
            "stato_sezione": _stato_aggregato(processo),
            "contenuto": "Fasi del processo di cessazione (sottosezioni 7.1–7.6).",
            "funzioni": [],
            "dati_da_compilare": [],
            "dati": None,
            "sottosezioni": processo,
        },
        sezione(
            "8",
            "Cancellazione degli archivi",
            "Cancellazione degli archivi al termine del trasferimento, con prova della "
            "distruzione. Gli oggetti sotto Object Lock COMPLIANCE non si cancellano prima "
            "della scadenza della retention: la procedura va definita con il subentrante.",
            funzioni=["scarto", "backup_dr"],
        ),
    ]

    documento: dict[str, Any] = {
        "conservatore": c,
        "sezioni": sezioni,
        "riepilogo_sezioni": riepilogo(sezioni),
    }
    completo = all(s["stato_sezione"] == GENERATA for s in sezioni)
    documento["meta"] = {
        "titolo": "Piano di cessazione del servizio di conservazione",
        "riferimento": "Allegato B — Regolamento AgID Det. 455/2021",
        "versione": dati.piano_cessazione_versione.strip() or "bozza non versionata",
        "stato_documento": "completo" if completo else "bozza",
        "data_generazione": adesso.isoformat(),
        "impronta_contenuto_sha256": _impronta(documento),
        "stato_firma": stato_firma_documento(),
        "normativa": RIFERIMENTI_NORMATIVI_CESSAZIONE,
    }
    return documento


def _stato_aggregato(sottosezioni: list[dict[str, Any]]) -> str:
    """Lo stato peggiore tra le sottosezioni."""
    ordine = ["da_realizzare", "non_disponibile", "parziale", "da_compilare", "generata"]
    stati = {s["stato_sezione"] for s in sottosezioni}
    for s in ordine:
        if s in stati:
            return s
    return GENERATA
