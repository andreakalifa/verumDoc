# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/domain/stati.py — macchine a stati delle pratiche del ciclo di vita.

Riferimenti: contratto Q2 di R05; REGOLE_SVILUPPO §11.1 (transizioni come dato,
un solo punto ``transizione``), §11.2 (ogni stato raggiungibile), §11.7 (scarto:
proposta, autorizzazioni, esecuzione, verbale), §12.5 (stato atteso).
Linee guida AgID §4.11 (scarto) e, per le PA, nulla osta della Soprintendenza
archivistica (D.lgs. 42/2004, art. 21).

Modulo PURO: nessun I/O, nessun orologio. Le transizioni ammesse, i ruoli che
le possono chiedere e gli allegati obbligatori sono DATI (``MACCHINE``): un
cambio normativo (es. un'autorizzazione in più) è una riga qui, non un ramo di
codice. ``motivo_blocco_transizione`` è l'unica funzione che decide se una
transizione si può fare (REGOLE §9.7); la usano il servizio e le
``azioni_consentite`` restituite all'interfaccia.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

# ── Tipi di pratica ────────────────────────────────────────────────────
RETTIFICA = "RETTIFICA"
ANNULLAMENTO = "ANNULLAMENTO"
SCARTO = "SCARTO"
RIVERSAMENTO_USCITA = "RIVERSAMENTO_USCITA"
RIVERSAMENTO_INGRESSO = "RIVERSAMENTO_INGRESSO"
RESTITUZIONE = "RESTITUZIONE"
TIPI = (RETTIFICA, ANNULLAMENTO, SCARTO, RIVERSAMENTO_USCITA, RIVERSAMENTO_INGRESSO,
        RESTITUZIONE)

# ── Ruoli ──────────────────────────────────────────────────────────────
SUPER_ADMIN = "SUPER_ADMIN"
RSC = "RSC_CONSERVATORE"
ADMIN_TENANT = "ADMIN_TENANT"
OPERATORE = "OPERATORE"
API_SYSTEM = "API_SYSTEM"
#: Ruoli del conservatore (perimetro: tutti gli enti, solo metadati delle pratiche)
RUOLI_CONSERVATORE = frozenset({SUPER_ADMIN, RSC})
#: «Ruolo» delle transizioni automatiche del lavoratore (stessa funzione delle manuali)
SISTEMA = "SISTEMA"

# ── Stati ──────────────────────────────────────────────────────────────
BOZZA = "BOZZA"
PROPOSTA = "PROPOSTA"
AUTORIZZATA_PRODUTTORE = "AUTORIZZATA_PRODUTTORE"
NULLA_OSTA = "NULLA_OSTA"
ESEGUIBILE = "ESEGUIBILE"
ESEGUITA = "ESEGUITA"
#: Revisione 2 di R05 (S3): esecuzione dello scarto CONCLUSA con eccezioni (PdA
#: la cui cancellazione, iniziata, non si è potuta completare: legal hold
#: arrivato durante lo scarto, errore persistente dello storage). Verbale e
#: tombstone per ciò che è stato cancellato; uscita: ripresa dell'esecuzione dei
#: soli PdA incompleti quando il blocco cessa (runbook scarto_incompleto).
ESEGUITA_CON_ECCEZIONI = "ESEGUITA_CON_ECCEZIONI"
#: R09 (D20): scarto concluso con eccezioni CHIUSO dal conservatore (motivo
#: obbligatorio, riverificato dal lavoratore, nel registro delle impronte): i PdA
#: rimasti in eliminazione incompleta restano tali (versioni rimaste verificate
#: dalla verifica continua, D26); nessuna ripresa successiva
CHIUSA_CON_ECCEZIONI = "CHIUSA_CON_ECCEZIONI"
RESPINTA = "RESPINTA"
RICHIESTA = "RICHIESTA"
#: R09 (D23): annullamento approvato da una seconda persona (quattro occhi)
APPROVATA = "APPROVATA"
IN_VERSAMENTO = "IN_VERSAMENTO"
COMPLETATA = "COMPLETATA"

# ── Lavori del lavoratore chiesti da una transizione «di esecuzione» ──────
LAVORO_ESEGUI = "ESEGUI"
#: R09 (D20): chiusura con eccezioni (fatti, foglie, transizione)
LAVORO_CHIUDI = "CHIUDI"
#: Revisione R09 (Z3): annullamento logico applicato dal LAVORATORE dopo la
#: riverifica (l'API mette solo in attesa: RICHIESTA o APPROVATA)
LAVORO_ANNULLA = "ANNULLA"

#: R14 (VD-C-133): stato di errore ESPLICITO di una pratica il cui lavoro è fallito
#: ``LAVORATORE_TENTATIVI_MASSIMI`` volte: lavoro tolto, ``ultimo_errore`` con questo
#: codice, il lavoro sospeso e l'ultimo errore; nessun nuovo tentativo automatico,
#: allarme PraticaSospesa, ripresa manuale (docs/runbooks/pratica_sospesa.md)
CODICE_TENTATIVI_ESAURITI = "LC-409-TENTATIVI-ESAURITI"


def lavoro_sospeso(pratica: Mapping[str, object]) -> str | None:
    """Il lavoro sospeso dopo i tentativi esauriti (``None`` se la pratica non è
    sospesa). Funzione pura, unica per API, metriche e lavoratore (REGOLE §9.2)."""
    if pratica.get("lavoro") is not None:
        return None
    errore = pratica.get("ultimo_errore")
    if not isinstance(errore, Mapping) or errore.get("codice") != CODICE_TENTATIVI_ESAURITI:
        return None
    sospeso = errore.get("lavoro_sospeso")
    return str(sospeso) if sospeso else None


# ── Allegati ───────────────────────────────────────────────────────────
ALLEGATO_AUTORIZZAZIONE = "AUTORIZZAZIONE_PRODUTTORE"
ALLEGATO_NULLA_OSTA = "NULLA_OSTA"
ALLEGATO_VERBALE = "VERBALE_SCARTO"
ALLEGATO_ATTO = "ATTO"
ALLEGATO_MANIFESTO = "MANIFESTO"
ALLEGATO_PDD_RICEVUTO = "PDD_RICEVUTO"
ALLEGATO_EXPORT_REGISTRI = "EXPORT_REGISTRI"
ALLEGATO_ALTRO = "ALTRO"
#: R08 (D22): documento firmato di una richiesta di annullamento (migrazione 047)
ALLEGATO_RICHIESTA_ANNULLAMENTO = "RICHIESTA_ANNULLAMENTO"
#: Allegati che gli utenti possono caricare (gli altri li produce il servizio)
ALLEGATI_CARICABILI = frozenset({ALLEGATO_AUTORIZZAZIONE, ALLEGATO_NULLA_OSTA, ALLEGATO_ALTRO})
#: Allegati che devono SEMPRE essere documenti firmati (contratto Q2:
#: «autorizzazione del produttore firmata»). Da R08 la firma è VERIFICATA
#: (``app.application.firme``: livello CICLO_VITA_FIRME_LIVELLO); il nulla osta
#: dipende da NULLA_OSTA_FIRMA, la richiesta di annullamento da
#: ANNULLAMENTO_RICHIESTA_FIRMATA.
ALLEGATI_FIRMATI = frozenset({ALLEGATO_AUTORIZZAZIONE})


@dataclass(frozen=True)
class Transizione:
    """Una transizione ammessa: ruoli che la chiedono, allegati obbligatori.

    ``esecuzione``: la transizione non si applica subito ma chiede al lavoratore
    di eseguire la pratica (stato invariato finché l'esecuzione non termina, poi
    il lavoratore applica la transizione con il ruolo ``SISTEMA``).
    ``solo_pa`` / ``solo_non_pa``: vale solo se la pratica richiede (o non
    richiede) il nulla osta della Soprintendenza."""

    da: str
    a: str
    ruoli: frozenset[str]
    allegati: tuple[str, ...] = ()
    esecuzione: bool = False
    solo_pa: bool = False
    solo_non_pa: bool = False
    automatica: bool = False
    #: R09: lavoro chiesto al lavoratore da una transizione «di esecuzione»
    lavoro: str = LAVORO_ESEGUI
    #: R09 (D20): motivo obbligatorio (almeno ``MOTIVO_MINIMO`` caratteri)
    motivo_obbligatorio: bool = False
    #: R09 (D23): transizione con un endpoint proprio (approvazione dell'annullamento:
    #: ruoli dai parametri dell'ente e quattro occhi), mai dalla transizione generica
    dedicata: bool = False
    #: R13b (VD-C-162, opzione predisposta): vale solo se la pratica richiede (o non
    #: richiede) l'autorizzazione del produttore, fissata alla creazione
    solo_con_autorizzazione: bool = False
    solo_senza_autorizzazione: bool = False


@dataclass(frozen=True)
class Macchina:
    """Macchina a stati di un tipo di pratica."""

    tipo: str
    iniziale: str
    finali: frozenset[str]
    transizioni: tuple[Transizione, ...] = field(default_factory=tuple)
    #: Revisione 2 di R05 (S3): stati che CONCLUDONO l'esecuzione (data di
    #: conclusione, nessun lavoro) ma hanno ancora un'uscita (ripresa)
    concluse_con_eccezioni: frozenset[str] = frozenset()

    def stati(self) -> set[str]:
        tutti = {self.iniziale, *self.finali}
        for t in self.transizioni:
            tutti.update((t.da, t.a))
        return tutti

    def uscite(self, stato: str) -> list[Transizione]:
        return [t for t in self.transizioni if t.da == stato]

    def cerca(self, da: str, a: str) -> Transizione | None:
        return next((t for t in self.transizioni if t.da == da and t.a == a), None)


_AVVIO = frozenset({RSC, ADMIN_TENANT})
_PRODUTTORE = frozenset({ADMIN_TENANT})
_CONSERVATORE = frozenset({RSC, SUPER_ADMIN})
_RESPINGE = frozenset({RSC, SUPER_ADMIN, ADMIN_TENANT})
_NULLA_OSTA = frozenset({ADMIN_TENANT, RSC})
#: R09 (D23): ruoli che possono approvare (insieme massimo: i ruoli effettivi sono
#: il parametro ANNULLAMENTO_RUOLI_APPROVAZIONE dell'ente). Revisione R09 (Z1,
#: VD-P-90): solo ADMIN_TENANT, i cui account li crea il conservatore; un
#: OPERATORE lo crea l'ADMIN dell'ente (un «alias» con cui approvarsi da solo)
RUOLI_APPROVAZIONE = frozenset({ADMIN_TENANT})
#: R09 (D20): lunghezza minima del motivo di una chiusura con eccezioni
MOTIVO_MINIMO = 20


def _respinte(*stati: str) -> tuple[Transizione, ...]:
    return tuple(Transizione(s, RESPINTA, _RESPINGE) for s in stati)


MACCHINE: Mapping[str, Macchina] = {
    SCARTO: Macchina(
        SCARTO, BOZZA, frozenset({ESEGUITA, RESPINTA, CHIUSA_CON_ECCEZIONI}),
        (
            Transizione(BOZZA, PROPOSTA, _AVVIO),
            Transizione(PROPOSTA, AUTORIZZATA_PRODUTTORE, _PRODUTTORE,
                        allegati=(ALLEGATO_AUTORIZZAZIONE,)),
            # PA: nulla osta della Soprintendenza archivistica caricato come file
            Transizione(AUTORIZZATA_PRODUTTORE, NULLA_OSTA, _NULLA_OSTA,
                        allegati=(ALLEGATO_NULLA_OSTA,), solo_pa=True),
            Transizione(NULLA_OSTA, ESEGUIBILE, _CONSERVATORE, solo_pa=True),
            Transizione(AUTORIZZATA_PRODUTTORE, ESEGUIBILE, _CONSERVATORE, solo_non_pa=True),
            Transizione(ESEGUIBILE, ESEGUITA, _CONSERVATORE, esecuzione=True),
            *_respinte(BOZZA, PROPOSTA, AUTORIZZATA_PRODUTTORE, NULLA_OSTA, ESEGUIBILE),
            # Revisione 2 di R05 (S3): conclusione con eccezioni (lavoratore) e
            # ripresa dei PdA incompleti (conservatore, mai RESPINTA)
            Transizione(ESEGUIBILE, ESEGUITA_CON_ECCEZIONI, frozenset({SISTEMA}),
                        automatica=True),
            Transizione(ESEGUITA_CON_ECCEZIONI, ESEGUITA, _CONSERVATORE, esecuzione=True),
            Transizione(ESEGUITA_CON_ECCEZIONI, ESEGUITA_CON_ECCEZIONI, frozenset({SISTEMA}),
                        automatica=True),
            # R09 (D20): chiusura definitiva con eccezioni (conservatore, motivo
            # obbligatorio): la applica il lavoratore dopo fatti e foglie
            Transizione(ESEGUITA_CON_ECCEZIONI, CHIUSA_CON_ECCEZIONI, _CONSERVATORE,
                        esecuzione=True, lavoro=LAVORO_CHIUDI, motivo_obbligatorio=True),
        ),
        concluse_con_eccezioni=frozenset({ESEGUITA_CON_ECCEZIONI}),
    ),
    RESTITUZIONE: Macchina(
        RESTITUZIONE, BOZZA, frozenset({ESEGUITA, RESPINTA}),
        (
            Transizione(BOZZA, PROPOSTA, _AVVIO),
            Transizione(PROPOSTA, AUTORIZZATA_PRODUTTORE, _PRODUTTORE,
                        allegati=(ALLEGATO_AUTORIZZAZIONE,)),
            Transizione(AUTORIZZATA_PRODUTTORE, ESEGUIBILE, _CONSERVATORE),
            Transizione(ESEGUIBILE, ESEGUITA, _CONSERVATORE, esecuzione=True),
            *_respinte(BOZZA, PROPOSTA, AUTORIZZATA_PRODUTTORE, ESEGUIBILE),
        ),
    ),
    RIVERSAMENTO_USCITA: Macchina(
        RIVERSAMENTO_USCITA, PROPOSTA, frozenset({ESEGUITA, RESPINTA}),
        (
            # comportamento di prima: decide il conservatore (VD-P-47)
            Transizione(PROPOSTA, ESEGUIBILE, _CONSERVATORE, solo_senza_autorizzazione=True),
            # R13b (VD-C-162, opzione RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE =
            # RICHIESTA): autorizzazione firmata del produttore legata alla pratica,
            # riverificata dal lavoratore come per la restituzione
            Transizione(PROPOSTA, AUTORIZZATA_PRODUTTORE, _PRODUTTORE,
                        allegati=(ALLEGATO_AUTORIZZAZIONE,), solo_con_autorizzazione=True),
            Transizione(AUTORIZZATA_PRODUTTORE, ESEGUIBILE, _CONSERVATORE,
                        solo_con_autorizzazione=True),
            Transizione(ESEGUIBILE, ESEGUITA, _CONSERVATORE, esecuzione=True),
            *_respinte(PROPOSTA, AUTORIZZATA_PRODUTTORE, ESEGUIBILE),
        ),
    ),
    RIVERSAMENTO_INGRESSO: Macchina(
        RIVERSAMENTO_INGRESSO, PROPOSTA, frozenset({ESEGUITA, RESPINTA}),
        (
            Transizione(PROPOSTA, ESEGUIBILE, _CONSERVATORE),
            Transizione(ESEGUIBILE, ESEGUITA, _CONSERVATORE, esecuzione=True),
            *_respinte(PROPOSTA, ESEGUIBILE),
        ),
    ),
    RETTIFICA: Macchina(
        RETTIFICA, RICHIESTA, frozenset({COMPLETATA, RESPINTA}),
        (
            Transizione(RICHIESTA, IN_VERSAMENTO, frozenset({SISTEMA}), automatica=True),
            Transizione(IN_VERSAMENTO, COMPLETATA, frozenset({SISTEMA}), automatica=True),
            Transizione(IN_VERSAMENTO, RESPINTA, frozenset({SISTEMA}), automatica=True),
            Transizione(RICHIESTA, RESPINTA, frozenset({SISTEMA}), automatica=True),
        ),
    ),
    ANNULLAMENTO: Macchina(
        ANNULLAMENTO, RICHIESTA, frozenset({ESEGUITA, RESPINTA}),
        (
            Transizione(RICHIESTA, ESEGUITA, frozenset({SISTEMA}), automatica=True),
            # R09 (D23): doppia approvazione (seconda persona dell'ente, endpoint
            # proprio); approvata, l'annullamento logico è del SISTEMA
            Transizione(RICHIESTA, APPROVATA, RUOLI_APPROVAZIONE, dedicata=True),
            Transizione(APPROVATA, ESEGUITA, frozenset({SISTEMA}), automatica=True),
            # respinta dalla seconda persona; revisione R09 (W1): dal lavoratore
            # (SISTEMA) se qualcosa è cambiato prima dell'applicazione, o dall'ente
            # e dal conservatore su una pratica bloccata (endpoint dell'approvazione)
            Transizione(RICHIESTA, RESPINTA, RUOLI_APPROVAZIONE | _CONSERVATORE | {SISTEMA},
                        dedicata=True),
            Transizione(APPROVATA, RESPINTA, _RESPINGE | {SISTEMA}, dedicata=True),
        ),
    ),
}

#: Allegati per tipo che il servizio produce alla conclusione (verbale, atti, manifesti)
ALLEGATI_PRODOTTI: Mapping[str, tuple[str, ...]] = {
    SCARTO: (ALLEGATO_VERBALE,),
    ANNULLAMENTO: (ALLEGATO_ATTO,),
    RETTIFICA: (ALLEGATO_ATTO,),
    RIVERSAMENTO_USCITA: (ALLEGATO_MANIFESTO,),
    RESTITUZIONE: (ALLEGATO_MANIFESTO, ALLEGATO_EXPORT_REGISTRI),
    RIVERSAMENTO_INGRESSO: (ALLEGATO_PDD_RICEVUTO,),
}


@dataclass(frozen=True)
class ContestoTransizione:
    """Chi chiede la transizione e su quale pratica (dati già letti dal DB)."""

    ruolo: str
    stesso_ente: bool
    richiede_nulla_osta: bool
    #: tipi degli allegati della pratica non ancora usati da una transizione
    allegati_disponibili: frozenset[str] = frozenset()
    #: tipi degli allegati citati dalla richiesta
    allegati_citati: frozenset[str] = frozenset()
    #: un lavoro del servizio è chiesto o in corso (verifica, esecuzione)
    lavoro_in_corso: bool = False
    #: il lavoratore ha INIZIATO l'esecuzione (revisione R05, R1): da qui la
    #: pratica arriva per forza alla sua conclusione (verbale e foglie per ciò
    #: che è stato fatto); nessuna transizione manuale è più ammessa
    esecuzione_avviata: bool = False
    #: R09 (D20): motivo della richiesta (transizioni con motivo obbligatorio)
    motivo: str | None = None
    #: R13b (VD-C-162): la pratica richiede l'autorizzazione del produttore (colonna
    #: ``richiede_autorizzazione_produttore``, fissata alla creazione; predefinito no)
    richiede_autorizzazione: bool = False


def macchina(tipo: str) -> Macchina:
    try:
        return MACCHINE[tipo]
    except KeyError:
        raise ValueError(f"tipo di pratica sconosciuto: {tipo!r}") from None


def motivo_blocco_transizione(tipo: str, da: str, a: str,
                              ctx: ContestoTransizione) -> tuple[str, str] | None:
    """``None`` se la transizione ``da → a`` si può chiedere, altrimenti
    ``(codice, messaggio)`` del catalogo errori. Ordine dei controlli:
    transizione esistente (LC-409-TRANSIZIONE), variante PA/non PA, ruolo
    (LC-403-RUOLO; un ruolo dell'ente vale solo sulle pratiche del suo ente),
    lavoro del servizio (LC-409-IN-ESECUZIONE), allegati obbligatori
    (LC-422-ALLEGATI).

    Revisione R05 (R1): con l'esecuzione AVVIATA dal lavoratore nessuna
    transizione è ammessa, nemmeno RESPINTA: uno scarto che ha già cancellato
    un PdA deve concludersi con verbale e tombstone. Prima che il lavoratore la
    prenda, un'esecuzione chiesta si può ancora respingere."""
    m = macchina(tipo)
    t = m.cerca(da, a)
    if t is None or t.automatica or t.dedicata:
        ammessi = sorted(x.a for x in m.uscite(da) if not (x.automatica or x.dedicata))
        return ("LC-409-TRANSIZIONE",
                f"Transizione {da} → {a} non ammessa per una pratica di {tipo.lower()}"
                + (f" (ammesse: {', '.join(ammessi)})" if ammessi else " (stato finale)"))
    if t.solo_pa and not ctx.richiede_nulla_osta:
        return ("LC-409-TRANSIZIONE",
                "Il nulla osta della Soprintendenza è previsto solo per le pubbliche "
                "amministrazioni: la pratica passa direttamente a ESEGUIBILE")
    if t.solo_non_pa and ctx.richiede_nulla_osta:
        return ("LC-409-TRANSIZIONE",
                "Per una pubblica amministrazione serve prima il nulla osta della "
                "Soprintendenza archivistica (stato NULLA_OSTA)")
    if t.solo_con_autorizzazione and not ctx.richiede_autorizzazione:
        return ("LC-409-TRANSIZIONE",
                "L'autorizzazione del produttore non è richiesta per questa pratica: decide "
                "il conservatore (ESEGUIBILE)")
    if t.solo_senza_autorizzazione and ctx.richiede_autorizzazione:
        return ("LC-409-TRANSIZIONE",
                "Per questa pratica serve prima l'autorizzazione firmata del produttore "
                "(stato AUTORIZZATA_PRODUTTORE)")
    if ctx.ruolo not in t.ruoli:
        return ("LC-403-RUOLO", f"Il ruolo {ctx.ruolo or 'sconosciuto'} non può chiedere "
                                f"la transizione {da} → {a} "
                                f"(ammessi: {', '.join(sorted(t.ruoli))})")
    if ctx.ruolo not in RUOLI_CONSERVATORE and not ctx.stesso_ente:
        return ("LC-404", "Pratica non trovata")
    if ctx.esecuzione_avviata:
        return ("LC-409-IN-ESECUZIONE",
                "L'esecuzione della pratica è iniziata: il servizio la porta a termine "
                "(verbale ed esiti per elemento); non si può più respingere né cambiare stato")
    if ctx.lavoro_in_corso and a != RESPINTA:
        return ("LC-409-IN-ESECUZIONE",
                "Un lavoro della pratica è in corso: attendere che finisca")
    if t.motivo_obbligatorio and len((ctx.motivo or "").strip()) < MOTIVO_MINIMO:
        return ("LC-422-MOTIVO",
                f"Per {a} serve un motivo di almeno {MOTIVO_MINIMO} caratteri (resta nel "
                "registro delle impronte e nell'audit)")
    mancanti = [x for x in t.allegati
                if x not in ctx.allegati_citati or x not in ctx.allegati_disponibili]
    if mancanti:
        return ("LC-422-ALLEGATI",
                f"Allegati obbligatori per {a}: {', '.join(mancanti)} (caricarli con "
                "POST /pratiche/{id}/allegati e citarli nella transizione)")
    return None


def azioni_consentite(tipo: str, stato: str, ctx: ContestoTransizione) -> list[dict[str, object]]:
    """Transizioni manuali dallo stato attuale con, per ognuna, il motivo di
    blocco (REGOLE §9.6): l'interfaccia non ricalcola permessi né stati.
    Gli allegati si considerano «citati» se disponibili."""
    ctx_ui = ContestoTransizione(ctx.ruolo, ctx.stesso_ente, ctx.richiede_nulla_osta,
                                 ctx.allegati_disponibili, ctx.allegati_disponibili,
                                 ctx.lavoro_in_corso, ctx.esecuzione_avviata,
                                 motivo="x" * MOTIVO_MINIMO,
                                 richiede_autorizzazione=ctx.richiede_autorizzazione)
    azioni = []
    for t in macchina(tipo).uscite(stato):
        if t.automatica or t.dedicata or (t.solo_pa and not ctx.richiede_nulla_osta) or (
                t.solo_non_pa and ctx.richiede_nulla_osta) or (
                t.solo_con_autorizzazione and not ctx.richiede_autorizzazione) or (
                t.solo_senza_autorizzazione and ctx.richiede_autorizzazione):
            continue
        blocco = motivo_blocco_transizione(tipo, stato, t.a, ctx_ui)
        azioni.append({"nuovo_stato": t.a, "allegati_richiesti": list(t.allegati),
                       "esecuzione": t.esecuzione,
                       "motivo_minimo": MOTIVO_MINIMO if t.motivo_obbligatorio else None,
                       "consentita": blocco is None,
                       "motivo_blocco": None if blocco is None else blocco[1]})
    return azioni


def stati_non_raggiungibili(macchine: Iterable[Macchina] = MACCHINE.values()) -> list[str]:
    """Stati non iniziali senza una transizione che li raggiunga (REGOLE §11.2)."""
    mancanti = []
    for m in macchine:
        raggiunti = {t.a for t in m.transizioni}
        for s in m.stati() - {m.iniziale}:
            if s not in raggiunti:
                mancanti.append(f"{m.tipo}.{s}")
    return mancanti



#: R06 (VD-C-128): pratiche «in attesa di» chi guarda, per ruolo: coppie
#: (tipo o None = qualunque, stato) in cui decide il ruolo. Conservatore:
#: rendere eseguibile ed eseguire, approvare i riversamenti; produttore
#: (ADMIN_TENANT): proporre e autorizzare scarti e restituzioni.
#: R13b (VD-C-162): una coppia può portare un terzo elemento, la condizione sulla
#: colonna ``richiede_autorizzazione_produttore`` (None = qualunque).
CoppiaAttesa = tuple[str | None, str] | tuple[str | None, str, bool | None]
IN_ATTESA_CONSERVATORE: tuple[CoppiaAttesa, ...] = (
    (None, ESEGUIBILE), (None, AUTORIZZATA_PRODUTTORE), (None, NULLA_OSTA),
    (RIVERSAMENTO_USCITA, PROPOSTA, False), (RIVERSAMENTO_INGRESSO, PROPOSTA),
    # R09 (D20): riprendere o chiudere con eccezioni
    (SCARTO, ESEGUITA_CON_ECCEZIONI),
)
IN_ATTESA_PRODUTTORE: tuple[CoppiaAttesa, ...] = (
    (SCARTO, BOZZA), (SCARTO, PROPOSTA), (RESTITUZIONE, PROPOSTA),
    # R13b (VD-C-162): riversamento in uscita con l'autorizzazione richiesta
    (RIVERSAMENTO_USCITA, PROPOSTA, True),
    # R09 (D23): annullamento in attesa della seconda approvazione
    (ANNULLAMENTO, RICHIESTA),
)


def in_attesa_di(ruolo: str) -> tuple[CoppiaAttesa, ...]:
    """Coppie (tipo, stato) in cui la pratica aspetta il ruolo (vuoto: nessuna)."""
    if ruolo in RUOLI_CONSERVATORE:
        return IN_ATTESA_CONSERVATORE
    if ruolo == ADMIN_TENANT:
        return IN_ATTESA_PRODUTTORE
    return ()

__all__ = [
    "ALLEGATI_CARICABILI", "ALLEGATI_FIRMATI", "ALLEGATI_PRODOTTI", "ContestoTransizione",
    "LAVORO_ANNULLA", "LAVORO_CHIUDI", "LAVORO_ESEGUI", "MOTIVO_MINIMO", "RUOLI_APPROVAZIONE",
    "MACCHINE", "Macchina",
    "IN_ATTESA_CONSERVATORE", "IN_ATTESA_PRODUTTORE",
    "RUOLI_CONSERVATORE", "TIPI", "Transizione", "azioni_consentite", "in_attesa_di", "macchina",
    "motivo_blocco_transizione", "stati_non_raggiungibili",
]
