# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/domain/autorizzazioni.py — verifica PURA della catena delle autorizzazioni
di una pratica di scarto (revisione 4 di R05, rilievo U2).

Il lavoratore la usa prima di cancellare qualunque cosa, SENZA fidarsi dello
stato che l'API ha scritto nella riga della pratica: ricostruisce l'iter dalla
cronologia (append-only, continua per costruzione: migrazioni 037 e 044) e lo
confronta con la macchina a stati (``app.domain.stati.MACCHINE``, la stessa
che decide le transizioni manuali: un cambio normativo resta una riga lì).

Controlli (ognuno produce un difetto leggibile, codice stabile in testa):
  - la cronologia parte dallo stato iniziale, creata da un ruolo che può
    avviare la pratica, ed è CONTINUA (``da`` = ``a`` del passo precedente);
  - ogni passo è una transizione della macchina, chiesta da un ruolo ammesso
    (con l'utente) o dal SISTEMA se automatica; la variante PA/non PA è quella
    della natura ATTUALE dell'ente (nessun salto del nulla osta);
  - gli allegati obbligatori di ogni passo sono citati, sono della pratica, del
    tipo giusto e citati una volta sola;
  - l'ultimo passo porta allo stato attuale, che è uno stato eseguibile;
  - per un ente che richiede il nulla osta della Soprintendenza c'è il passo
    NULLA_OSTA (D.lgs. 42/2004, art. 21; Linee guida AgID §4.11).

Modulo PURO: niente I/O. L'esistenza degli allegati nel WORM con l'impronta
registrata e la coerenza col piano approvato le controlla
``app.application.autorizzazioni``.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from app.domain import stati as st

#: Codice d'errore della pratica bloccata (``ultimo_errore.codice``) e dell'audit
CODICE_BLOCCO = "LC-409-AUTORIZZAZIONI"
#: Stati da cui il lavoratore esegue uno scarto (prima esecuzione e ripresa)
STATI_ESEGUIBILI = frozenset({st.ESEGUIBILE, st.ESEGUITA_CON_ECCEZIONI})


def _id(valore: Any) -> str:
    return str(valore)


def difetti_iter(tipo: str, cronologia: Sequence[Mapping[str, Any]],
                 allegati: Mapping[str, Mapping[str, Any]], *, richiede_nulla_osta: bool,
                 stato_attuale: str, ruoli_avvio: Collection[str],
                 creazione_del_sistema: bool = False,
                 richiede_autorizzazione: bool = True) -> list[str]:
    """Difetti della catena delle autorizzazioni (lista vuota = catena valida).

    ``cronologia``: righe di ``pratiche_stati`` in ordine di id (``da_stato``,
    ``a_stato``, ``ruolo``, ``utente_id``, ``allegati``, ``created_at``);
    ``allegati``: righe di ``pratiche_allegati`` della pratica per id;
    ``richiede_nulla_osta``: valore EFFETTIVO (colonna della pratica o natura
    attuale dell'ente: il più prudente).

    R09 (D21): ``creazione_del_sistema`` = esiste il FATTO ``PROPOSTA_GENERATA``
    del lavoratore per la pratica (``esiti_esecuzione``, scrivibile solo dal suo
    ruolo): solo allora la creazione può essere del SISTEMA (proposta di scarto
    automatica, senza utente); i passi successivi restano delle persone."""
    m = st.macchina(tipo)
    difetti: list[str] = []
    if stato_attuale not in STATI_ESEGUIBILI:
        difetti.append(f"STATO_NON_ESEGUIBILE: la pratica è {stato_attuale}")
    if not cronologia:
        return [*difetti, "CRONOLOGIA_ASSENTE: nessun passo registrato (pratica non nata "
                          "dalla proposta né autorizzata)"]
    primo = cronologia[0]
    if primo.get("da_stato") is not None or primo.get("a_stato") != m.iniziale:
        difetti.append(f"CRONOLOGIA_INIZIO: il primo passo è {primo.get('da_stato')} → "
                       f"{primo.get('a_stato')}, atteso la creazione in {m.iniziale}")
    if primo.get("ruolo") == st.SISTEMA and primo.get("utente_id") is None:
        if not creazione_del_sistema:
            difetti.append("CREAZIONE_SISTEMA_NON_PROVATA: creata dal SISTEMA senza il fatto "
                           "PROPOSTA_GENERATA del lavoratore")
    elif primo.get("ruolo") not in ruoli_avvio or primo.get("utente_id") is None:
        difetti.append(f"CRONOLOGIA_INIZIO: creata dal ruolo {primo.get('ruolo')} "
                       f"(ammessi: {', '.join(sorted(ruoli_avvio))}, con l'utente)")
    citati: set[str] = set()
    for i, passo in enumerate(cronologia[1:], start=1):
        da, a = passo.get("da_stato"), passo.get("a_stato")
        precedente = cronologia[i - 1].get("a_stato")
        nome = f"passo {i} ({da} → {a})"
        if da != precedente:
            difetti.append(f"CRONOLOGIA_DISCONTINUA: {nome} non continua da {precedente}")
        t = m.cerca(str(da), str(a))
        if t is None:
            difetti.append(f"TRANSIZIONE_NON_PREVISTA: {nome}")
            continue
        if t.automatica:
            if passo.get("ruolo") != st.SISTEMA:
                difetti.append(f"RUOLO_NON_AMMESSO: {nome} è automatica (SISTEMA), "
                               f"registrata dal ruolo {passo.get('ruolo')}")
        elif passo.get("ruolo") not in t.ruoli or passo.get("utente_id") is None:
            difetti.append(f"RUOLO_NON_AMMESSO: {nome} chiesta dal ruolo {passo.get('ruolo')} "
                           f"(ammessi: {', '.join(sorted(t.ruoli))}, con l'utente)")
        if t.solo_non_pa and richiede_nulla_osta:
            difetti.append(f"NULLA_OSTA_SALTATO: {nome} è ammessa solo per un ente che non "
                           "richiede il nulla osta della Soprintendenza")
        if t.solo_pa and not richiede_nulla_osta:
            difetti.append(f"VARIANTE_INCOERENTE: {nome} è prevista solo per una pubblica "
                           "amministrazione")
        if t.solo_senza_autorizzazione and richiede_autorizzazione:
            difetti.append(f"AUTORIZZAZIONE_SALTATA: {nome} è ammessa solo se la pratica non "
                           "richiede l'autorizzazione del produttore")
        tipi_citati: set[str] = set()
        for allegato_id in passo.get("allegati") or []:
            chiave = _id(allegato_id)
            allegato = allegati.get(chiave)
            if allegato is None:
                difetti.append(f"ALLEGATO_ESTRANEO: {nome} cita {chiave}, non della pratica")
                continue
            if chiave in citati:
                difetti.append(f"ALLEGATO_RIUSATO: {nome} cita {chiave} già usato")
            citati.add(chiave)
            # (che esistesse già quando il passo è stato scritto lo garantisce il
            # trigger della cronologia, migrazione 044: niente confronti fra orologi)
            tipi_citati.add(str(allegato.get("tipo")))
        for richiesto in t.allegati:
            if richiesto not in tipi_citati:
                difetti.append(f"ALLEGATO_MANCANTE: {nome} senza l'allegato {richiesto}")
    ultimo = cronologia[-1].get("a_stato")
    if ultimo != stato_attuale:
        difetti.append(f"STATO_NON_DALLA_CRONOLOGIA: la cronologia arriva a {ultimo}, la "
                       f"pratica dichiara {stato_attuale}")
    raggiunti = {p.get("a_stato") for p in cronologia}
    if st.AUTORIZZATA_PRODUTTORE not in raggiunti:
        difetti.append("AUTORIZZAZIONE_PRODUTTORE_ASSENTE: nessun passo AUTORIZZATA_PRODUTTORE")
    if richiede_nulla_osta and st.NULLA_OSTA not in raggiunti:
        difetti.append("NULLA_OSTA_ASSENTE: l'ente richiede il nulla osta della Soprintendenza "
                       "archivistica (D.lgs. 42/2004, art. 21) e la cronologia non lo registra")
    return difetti


def allegati_autorizzativi(tipo: str, cronologia: Sequence[Mapping[str, Any]],
                           allegati: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Id degli allegati OBBLIGATORI citati dai passi dell'iter (autorizzazione
    del produttore, nulla osta): quelli che il lavoratore rilegge dal WORM."""
    m = st.macchina(tipo)
    risultato: list[str] = []
    for passo in cronologia[1:]:
        t = m.cerca(str(passo.get("da_stato")), str(passo.get("a_stato")))
        if t is None or not t.allegati:
            continue
        for allegato_id in passo.get("allegati") or []:
            a = allegati.get(_id(allegato_id))
            if a is not None and str(a.get("tipo")) in t.allegati \
                    and _id(allegato_id) not in risultato:
                risultato.append(_id(allegato_id))
    return risultato


#: Revisione R06 (V2): stato in cui il lavoratore scrive il FATTO di una pratica
#: puntuale (annullamento: dopo l'annullamento logico; rettifica: prima del
#: versamento del PdV di rettifica)
STATI_FATTO_PUNTUALE: Mapping[str, str] = {st.ANNULLAMENTO: st.ESEGUITA,
                                           st.RETTIFICA: st.RICHIESTA}
#: Revisione R09 (Z3): stati in cui il lavoratore APPLICA l'annullamento logico
#: (dopo la riverifica; l'API mette solo in attesa)
STATI_APPLICA_ANNULLAMENTO = frozenset({st.RICHIESTA, st.APPROVATA})
#: Difetto che rimette in attesa (non blocca) un annullamento non ancora applicato
APPROVAZIONE_ASSENTE = "APPROVAZIONE_ASSENTE"


def difetti_iter_puntuale(tipo: str, cronologia: Sequence[Mapping[str, Any]], *,
                          stato_attuale: str, creata_da: Any, creata_ruolo: str | None,
                          ruoli_ammessi: Collection[str],
                          approvazione_richiesta: bool = False,
                          ruoli_approvazione: Collection[str] = (),
                          applica: bool = False) -> list[str]:
    """Revisione R06 (V2): difetti della catena di una pratica di ANNULLAMENTO o
    RETTIFICA prima che il lavoratore ne scriva il fatto (lista vuota = valida).

    Come per lo scarto la cronologia (append-only) deve partire dalla creazione
    in RICHIESTA da parte del richiedente della pratica (stesso utente e ruolo,
    ruolo ammesso dalla configurazione), essere continua, fatta di transizioni
    della macchina (automatiche: SISTEMA) e arrivare allo stato attuale, che è
    quello in cui si scrive il fatto.

    R09 (D23), quattro occhi: ogni approvazione (RICHIESTA → APPROVATA) è di un
    utente DIVERSO dal richiedente con un ruolo ammesso dall'ente; con
    ``approvazione_richiesta`` (parametri ATTUALI dell'ente: fail-closed)
    l'approvazione deve esserci.

    Revisione R09: con ``applica`` (annullamento non ancora applicato, Z3) gli
    stati ammessi sono RICHIESTA e APPROVATA; con l'approvazione richiesta la
    richiesta deve essere di una persona, mai di una chiave API (Z1)."""
    m = st.macchina(tipo)
    difetti: list[str] = []
    if applica and tipo == st.ANNULLAMENTO:
        if stato_attuale not in STATI_APPLICA_ANNULLAMENTO:
            difetti.append(f"STATO_NON_ESEGUIBILE: la pratica è {stato_attuale} (atteso "
                           f"{' o '.join(sorted(STATI_APPLICA_ANNULLAMENTO))})")
    else:
        atteso = STATI_FATTO_PUNTUALE.get(tipo)
        if atteso is None or stato_attuale != atteso:
            difetti.append(f"STATO_NON_ESEGUIBILE: la pratica è {stato_attuale} (atteso "
                           f"{atteso})")
    if creata_da is None:
        difetti.append("RICHIEDENTE_ASSENTE: la pratica non indica l'utente che l'ha chiesta")
    if creata_ruolo not in ruoli_ammessi:
        difetti.append(f"RUOLO_NON_AMMESSO: chiesta dal ruolo {creata_ruolo} (ammessi: "
                       f"{', '.join(sorted(ruoli_ammessi))})")
    if not cronologia:
        return [*difetti, "CRONOLOGIA_ASSENTE: nessun passo registrato (pratica non nata "
                          "dalla richiesta di un utente)"]
    primo = cronologia[0]
    if primo.get("da_stato") is not None or primo.get("a_stato") != m.iniziale:
        difetti.append(f"CRONOLOGIA_INIZIO: il primo passo è {primo.get('da_stato')} → "
                       f"{primo.get('a_stato')}, atteso la creazione in {m.iniziale}")
    if creata_da is None or _id(primo.get("utente_id")) != _id(creata_da) \
            or primo.get("ruolo") != creata_ruolo:
        difetti.append("CRONOLOGIA_INIZIO: la creazione non è del richiedente della pratica "
                       "(utente o ruolo diversi)")
    for i, passo in enumerate(cronologia[1:], start=1):
        da, a = passo.get("da_stato"), passo.get("a_stato")
        precedente = cronologia[i - 1].get("a_stato")
        nome = f"passo {i} ({da} → {a})"
        if da != precedente:
            difetti.append(f"CRONOLOGIA_DISCONTINUA: {nome} non continua da {precedente}")
        t = m.cerca(str(da), str(a))
        if t is None:
            difetti.append(f"TRANSIZIONE_NON_PREVISTA: {nome}")
        elif t.automatica and passo.get("ruolo") != st.SISTEMA:
            difetti.append(f"RUOLO_NON_AMMESSO: {nome} è automatica (SISTEMA), registrata dal "
                           f"ruolo {passo.get('ruolo')}")
    approvazioni = [p for p in cronologia
                    if p.get("da_stato") == st.RICHIESTA and p.get("a_stato") == st.APPROVATA]
    for passo in approvazioni:
        if passo.get("utente_id") is None or creata_da is None \
                or _id(passo.get("utente_id")) == _id(creata_da):
            difetti.append("QUATTRO_OCCHI: l'annullamento è approvato dalla stessa persona che "
                           "l'ha chiesto (o senza utente)")
        if passo.get("ruolo") not in ruoli_approvazione:
            difetti.append(f"RUOLO_APPROVAZIONE_NON_AMMESSO: approvato dal ruolo "
                           f"{passo.get('ruolo')} (ammessi: "
                           f"{', '.join(sorted(ruoli_approvazione)) or 'nessuno'})")
    if approvazione_richiesta and creata_ruolo == st.API_SYSTEM:
        difetti.append("RICHIEDENTE_NON_PERSONA: con la doppia approvazione l'annullamento "
                       "lo chiede una persona, non una chiave API")
    if approvazione_richiesta and not approvazioni:
        difetti.append(f"{APPROVAZIONE_ASSENTE}: l'ente chiede la doppia approvazione e la "
                       "cronologia non la registra")
    ultimo = cronologia[-1].get("a_stato")
    if ultimo != stato_attuale:
        difetti.append(f"STATO_NON_DALLA_CRONOLOGIA: la cronologia arriva a {ultimo}, la "
                       f"pratica dichiara {stato_attuale}")
    return difetti


__all__ = ["APPROVAZIONE_ASSENTE", "CODICE_BLOCCO", "STATI_APPLICA_ANNULLAMENTO",
           "STATI_ESEGUIBILI", "STATI_FATTO_PUNTUALE",
           "allegati_autorizzativi", "difetti_iter", "difetti_iter_puntuale"]
