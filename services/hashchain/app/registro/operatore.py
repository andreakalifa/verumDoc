# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Comandi dell'operatore del registro delle impronte (rev4 rilievo 1, N3, terzo giro T3-T4).

Uso (nel container della hashchain, es. ``kubectl exec`` o un Job dedicato):

    python -m app.registro.operatore rifiuti
    python -m app.registro.operatore gestisci-rifiuti --tutti --conferma-causa HTTP_403 \\
        --operatore "Mario Rossi" \\
        --motivo "403 per rotazione della chiave del testimone: nessuna biforcazione"
    python -m app.registro.operatore gestisci-rifiuti --allarme 3 --operatore … --motivo …
    python -m app.registro.operatore gestisci-rifiuto --dimensione 9 --operatore … --motivo …
    python -m app.registro.operatore controlla-worm
    python -m app.registro.operatore blocchi
    python -m app.registro.operatore sblocca-registro --blocco 2 --operatore … --motivo …
    python -m app.registro.operatore ricostruisci-da-worm --fino-a 4200 --prova
    python -m app.registro.operatore ricostruisci-da-worm --fino-a 4200 --operatore … --motivo …
    python -m app.registro.operatore esporta-audit
    python -m app.registro.operatore verifica-audit [--da 2026-10-23T00:00:00Z] [--a …]
    python -m app.registro.operatore verifica-audit --automatica   (R09: CronJob giornaliero)
    python -m app.registro.operatore adotta-segmento-audit --segmento registro/audit/… \
        --operatore … --motivo …

Un checkpoint RIFIUTATO dal testimone (403, 422, 423, controfirma non
verificabile, dimensione del testimone maggiore) è un allarme persistente:
finché resta aperto nessun PdA passa a CONSERVATO. I rifiuti consecutivi con la
stessa causa si raggruppano in UN allarme con un contatore. ``rifiuti`` elenca
gli allarmi aperti. ``gestisci-rifiuti`` registra in UNA operazione (una voce in
``audit_global``) che un operatore ha indagato l'incidente:
  - ``--allarme ID``: un allarme, di qualunque causa;
  - ``--tutti``: in blocco, ma SOLO le cause confermate una per una con
    ``--conferma-causa`` (T3); le cause gravi ``HTTP_423`` (biforcazione) e
    ``DIMENSIONE_TESTIMONE_MAGGIORE`` mai in blocco, solo con ``--allarme``.
``gestisci-rifiuto --dimensione`` chiude l'allarme a cui appartiene quel
checkpoint (non per le cause gravi). I checkpoint restano RIFIUTATI e non
saranno mai usati per completare la conservazione.

Blocchi del registro (T4): ``controlla-worm`` stampa le misure del runbook di
ripristino (D, E, C, X, N, T, soglia) e restituisce 0 solo se il registro è
coerente; ``blocchi`` elenca i blocchi persistenti aperti; ``sblocca-registro``
chiude un blocco ``WORM_CONTENUTO_DIVERSO`` dopo l'indagine (gli altri si
chiudono da soli quando il controllo passa).

Ricostruzione dal WORM (R05, VD-C-111, ``app.registro.ricostruzione``):
``ricostruisci-da-worm`` ricostruisce foglie, nodi e collegamenti dai segmenti e
dai checkpoint del WORM fino a ``--fino-a``, verificando radici e firme di ogni
checkpoint; con ``--prova`` non scrive nulla; con un qualunque problema non
scrive nulla. Revisione R05 (R5): ricostruisce SOLO foglie coperte da un
checkpoint firmato dal registro e controfirmato dal testimone; oltre si ferma,
oppure con ``--scarta-coda-non-coperta`` si ferma a quel checkpoint e scarta la
coda senza scriverla. Codice 0 solo se riuscita (o prova riuscita).

Audit nel WORM (R05, VD-C-112, ``app.registro.audit_worm``): ``esporta-audit``
esporta subito i periodi chiusi (lo fa anche il ciclo); ``verifica-audit``
confronta ``audit_global`` con i segmenti nel WORM e stampa le differenze
(codice 0 solo se nessuna); ``adotta-segmento-audit`` registra come esportato un
segmento già nel WORM che l'export non riesce a riscrivere (runbook audit_worm.md).

Non esiste un'API remota per tutto questo: è un intervento consapevole dal
servizio stesso. Codice d'uscita 0 se l'operazione è riuscita, 1 altrimenti.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from app.config import settings
from app.registro import coerenza
from app.registro.checkpoint import (
    CAUSE_GRAVI,
    GestioneNonAmmessa,
    allarmi_aperti,
    registra_gestione_allarmi,
    registra_gestione_rifiuto,
)
from libs.shared.db import close_db, get_async_session, init_db
from libs.shared.db.ambito import ambito_conservatore

MOTIVO_MINIMO = 20


def _scrivi(testo: str) -> None:
    sys.stdout.write(testo + "\n")


async def esegui(opzioni: argparse.Namespace) -> int:
    """Esegue il comando su un database già inizializzato."""
    if opzioni.comando == "controlla-worm":
        return await _controlla_worm()
    if opzioni.comando == "ricostruisci-da-worm":
        return await _ricostruisci(opzioni)
    if opzioni.comando == "esporta-audit":
        return await _esporta_audit()
    if opzioni.comando == "verifica-audit":
        return await _verifica_audit(opzioni)
    if opzioni.comando == "blocchi":
        async with get_async_session() as session:
            aperti = await coerenza.blocchi_aperti(session)
        if not aperti:
            _scrivi("Nessun blocco del registro aperto.")
        for b in aperti:
            _scrivi(f"blocco {b['id']}\t{b['causa']}\toccorrenze={b['occorrenze']}\t"
                    f"dal {b['aperto_il'].isoformat()}\t{json.dumps(b['dettagli'], default=str)}")
        return 0
    if opzioni.comando == "rifiuti":
        async with get_async_session() as session:
            allarmi = await allarmi_aperti(session)
        if not allarmi:
            _scrivi("Nessun rifiuto del testimone aperto.")
        for a in allarmi:
            _scrivi(f"allarme {a['id']}\t{a['causa']}\trifiuti={a['rifiuti']}\t"
                    f"dimensioni {a['prima_dimensione']}..{a['ultima_dimensione']}\t"
                    f"dal {a['aperto_il'].isoformat()} al {a['ultimo_il'].isoformat()}\t"
                    f"{a['ultimo_motivo']}")
        return 0
    motivo = (opzioni.motivo or "").strip()
    operatore = (opzioni.operatore or "").strip()
    if len(motivo) < MOTIVO_MINIMO or not operatore:
        _scrivi(f"ERRORE: servono --operatore e un --motivo di almeno {MOTIVO_MINIMO} caratteri")
        return 1
    adesso = datetime.now(UTC)
    if opzioni.comando == "sblocca-registro":
        return await _sblocca_registro(opzioni.blocco, operatore, motivo, adesso)
    if opzioni.comando == "adotta-segmento-audit":
        from app.registro import audit_worm

        try:
            esito = await audit_worm.adotta_segmento(opzioni.segmento, operatore=operatore,
                                                     motivo=motivo, adesso=adesso)
        except audit_worm.AdozioneNonAmmessa as exc:
            _scrivi(f"ERRORE: {exc}")
            return 1
        _scrivi(f"Segmento {esito['segmento']} registrato ({esito['righe']} righe): l'export "
                "riprende dal periodo successivo; verifica-audit mostra le differenze")
        return 0
    try:
        return await _gestisci(opzioni, operatore, motivo, adesso)
    except GestioneNonAmmessa as exc:
        _scrivi(f"ERRORE: {exc}")
        return 1


async def _gestisci(opzioni: argparse.Namespace, operatore: str, motivo: str,
                    adesso: datetime) -> int:
    if opzioni.comando == "gestisci-rifiuto":
        async with get_async_session() as session:
            fatto = await registra_gestione_rifiuto(
                session, dimensione=opzioni.dimensione, operatore=operatore, motivo=motivo,
                adesso=adesso)
        if not fatto:
            _scrivi(f"ERRORE: il checkpoint {opzioni.dimensione} non è un rifiuto aperto")
            return 1
        _scrivi(f"Allarme del checkpoint {opzioni.dimensione} registrato come gestito: i "
                "checkpoint rifiutati restano inutilizzabili, il completamento riprende con "
                "quelli controfirmati.")
        return 0
    if opzioni.tutti and not opzioni.conferma_causa:
        async with get_async_session() as session:
            cause = sorted({a["causa"] for a in await allarmi_aperti(session)})
        _scrivi("ERRORE: --tutti richiede una conferma esplicita per ogni causa: "
                + " ".join(f"--conferma-causa {c}" for c in cause if c not in CAUSE_GRAVI)
                + (f" (le cause {', '.join(c for c in cause if c in CAUSE_GRAVI)} solo con "
                   "--allarme ID)" if any(c in CAUSE_GRAVI for c in cause) else ""))
        return 1
    async with get_async_session() as session:
        esito = await registra_gestione_allarmi(
            session, operatore=operatore, motivo=motivo, adesso=adesso,
            allarme=None if opzioni.tutti else opzioni.allarme,
            cause=opzioni.conferma_causa)
    if esito is None:
        _scrivi("ERRORE: nessun allarme aperto da gestire con le cause confermate")
        return 1
    _scrivi(f"Gestiti {len(esito['allarmi'])} allarmi e {esito['checkpoint']} checkpoint "
            "rifiutati in una sola operazione registrata; il completamento riprende con i "
            "checkpoint controfirmati.")
    for a in esito["rimasti_aperti"]:
        _scrivi(f"ANCORA APERTO: allarme {a['id']} {a['causa']}")
    return 0


async def _sblocca_registro(blocco: int, operatore: str, motivo: str, adesso: datetime) -> int:
    """Solo ``WORM_CONTENUTO_DIVERSO``: gli altri blocchi si chiudono da soli quando
    il controllo di coerenza passa (dopo il ripristino corretto)."""
    async with get_async_session() as session:
        aperti = {b["id"]: b for b in await coerenza.blocchi_aperti(session)}
        if blocco not in aperti:
            _scrivi(f"ERRORE: il blocco {blocco} non è aperto")
            return 1
        if aperti[blocco]["causa"] != coerenza.CAUSA_WORM_DIVERSO:
            _scrivi(f"ERRORE: il blocco {blocco} ({aperti[blocco]['causa']}) si chiude da solo "
                    "quando il controllo di coerenza passa (runbook "
                    "ripristino_registro_da_backup.md)")
            return 1
        await coerenza.chiudi_blocco(session, blocco=blocco, operatore=operatore,
                                     motivo=motivo, adesso=adesso)
    _scrivi(f"Blocco {blocco} chiuso: il registro riprende a scrivere; se il contenuto è "
            "ancora diverso il blocco si riapre.")
    return 0


async def _controlla_worm() -> int:
    """Misure del runbook: D, E, C, X, N, T e soglia = max(N, X, T); 0 se coerente."""
    from app.registro.chiavi import origine_registro
    from app.registro.testimone import client_predefinito

    esito = await coerenza.verifica(testimone=client_predefinito(),
                                    origine=origine_registro(settings))
    for nome, valore in esito.come_dict().items():
        _scrivi(f"{nome}\t{valore}")
    _scrivi("ESITO\t" + ("COERENTE: D >= max(N, X, T) e contenuti uguali" if esito.coerente
                         else "NON COERENTE: il registro non deve sequenziare"))
    return 0 if esito.coerente else 1


async def _ricostruisci(opzioni: argparse.Namespace) -> int:
    """VD-C-111: ricostruzione dal WORM (vedi ``app.registro.ricostruzione``)."""
    from app.registro import ricostruzione

    motivo = (opzioni.motivo or "").strip()
    operatore = (opzioni.operatore or "").strip()
    if not opzioni.prova and (len(motivo) < MOTIVO_MINIMO or not operatore):
        _scrivi(f"ERRORE: senza --prova servono --operatore e un --motivo di almeno "
                f"{MOTIVO_MINIMO} caratteri")
        return 1
    r = await ricostruzione.ricostruisci(
        fino_a=opzioni.fino_a, prova=opzioni.prova, operatore=operatore, motivo=motivo,
        lotto=opzioni.lotto, chiavi_precedenti=opzioni.chiave_precedente,
        scarta_coda_non_coperta=opzioni.scarta_coda_non_coperta)
    for riga in r.righe():
        _scrivi(riga)
    if not r.riuscito:
        _scrivi("ESITO\tNON RICOSTRUIBILE: nulla è stato scritto" if r.foglie_scritte == 0
                else f"ESITO\tINTERROTTA dopo {r.foglie_scritte} foglie (prefisso verificato): "
                     "correggere e rieseguire")
        return 1
    _scrivi("ESITO\tPROVA RIUSCITA: nessuna scrittura" if opzioni.prova
            else f"ESITO\tRICOSTRUITO fino a {opzioni.fino_a}: eseguire controlla-worm")
    return 0


async def _esporta_audit() -> int:
    from app.registro import audit_worm

    esito = await audit_worm.esporta_periodi_chiusi(adesso=datetime.now(UTC))
    _scrivi(f"segmenti esportati\t{esito.esportati}")
    for p in esito.problemi:
        _scrivi(f"PROBLEMA\t{p}")
    return 0 if not esito.problemi else 1


async def modo_automatico(adesso: datetime) -> tuple[str, datetime | None]:
    """R09 (D27): COMPLETA il giorno del mese AUDIT_VERIFICA_COMPLETA_GIORNO (ora
    italiana), altrimenti INCREMENTALE sugli ultimi AUDIT_VERIFICA_FINESTRA_GIORNI.
    Parametri del motore letti con il login in sola lettura dell'auditor."""
    from datetime import timedelta

    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.impostazioni import giorno_locale
    from libs.verumdoc_rules.parametri_servizio import (
        AUDIT_VERIFICA_COMPLETA_GIORNO,
        AUDIT_VERIFICA_FINESTRA_GIORNI,
    )

    v = await parametri([AUDIT_VERIFICA_COMPLETA_GIORNO, AUDIT_VERIFICA_FINESTRA_GIORNI])
    if giorno_locale(adesso).day == int(v[AUDIT_VERIFICA_COMPLETA_GIORNO]):
        return "COMPLETA", None
    return "INCREMENTALE", adesso - timedelta(days=int(v[AUDIT_VERIFICA_FINESTRA_GIORNI]))


async def _verifica_audit(opzioni: argparse.Namespace) -> int:
    from app.registro import audit_worm

    da, incrementale = opzioni.da, False
    if getattr(opzioni, "automatica", False):
        modo, da = await modo_automatico(datetime.now(UTC))
        incrementale = modo == "INCREMENTALE"
        _scrivi(f"MODO\t{modo}" + (f" dal {da.isoformat()}" if da else ""))
    esito = await audit_worm.verifica(da=da, a=opzioni.a, incrementale=incrementale)
    for riga in esito.righe():
        _scrivi(riga)
    _scrivi("ESITO\t" + ("COERENTE: audit_global uguale ai segmenti nel WORM" if esito.coerente
                          else "DIFFERENZE: audit_global non corrisponde al WORM"))
    return 0 if esito.coerente else 1


def _istante(valore: str) -> datetime:
    istante = datetime.fromisoformat(valore.replace("Z", "+00:00"))
    if istante.tzinfo is None:
        raise argparse.ArgumentTypeError("istante senza fuso orario")
    return istante


def analizza(argomenti: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.registro.operatore")
    sotto = parser.add_subparsers(dest="comando", required=True)
    sotto.add_parser("rifiuti", help="allarmi del testimone aperti, con il numero di rifiuti")
    g = sotto.add_parser("gestisci-rifiuti",
                         help="registra in blocco la gestione di un incidente")
    quali = g.add_mutually_exclusive_group(required=True)
    quali.add_argument("--tutti", action="store_true")
    quali.add_argument("--allarme", type=int)
    g.add_argument("--conferma-causa", action="append", default=[],
                   help="con --tutti: una per ogni causa da chiudere (es. HTTP_403)")
    g.add_argument("--operatore", required=True)
    g.add_argument("--motivo", required=True)
    sotto.add_parser("blocchi", help="blocchi persistenti del registro aperti")
    b = sotto.add_parser("sblocca-registro",
                         help="chiude un blocco WORM_CONTENUTO_DIVERSO dopo l'indagine")
    b.add_argument("--blocco", type=int, required=True)
    b.add_argument("--operatore", required=True)
    b.add_argument("--motivo", required=True)
    sotto.add_parser("controlla-worm",
                     help="misure D, N, X, T del runbook di ripristino; 0 se coerente")
    u = sotto.add_parser("gestisci-rifiuto",
                         help="registra la gestione dell'allarme di un checkpoint")
    u.add_argument("--dimensione", type=int, required=True)
    u.add_argument("--operatore", required=True)
    u.add_argument("--motivo", required=True)
    r = sotto.add_parser("ricostruisci-da-worm",
                         help="ricostruisce il registro dai segmenti e dai checkpoint del WORM")
    r.add_argument("--fino-a", type=int, required=True, help="dimensione da raggiungere")
    r.add_argument("--prova", action="store_true", help="tutti i controlli, nessuna scrittura")
    r.add_argument("--lotto", type=int, default=10000, help="foglie per transazione")
    r.add_argument("--chiave-precedente", action="append", default=[],
                   help="verifier key di una chiave del registro precedente (ripetibile)")
    r.add_argument("--scarta-coda-non-coperta", action="store_true",
                   help="ricostruisce fino all'ultimo checkpoint firmato e controfirmato del "
                        "WORM e SCARTA (senza scriverle) le foglie oltre (revisione R05, R5)")
    r.add_argument("--operatore")
    r.add_argument("--motivo")
    sotto.add_parser("esporta-audit", help="esporta nel WORM i periodi chiusi di audit_global")
    v = sotto.add_parser("verifica-audit", help="confronta audit_global con i segmenti nel WORM")
    v.add_argument("--da", type=_istante, default=None, help="inizio (ISO 8601 con fuso)")
    v.add_argument("--a", type=_istante, default=None, help="fine (ISO 8601 con fuso)")
    v.add_argument("--automatica", action="store_true",
                   help="R09: completa il giorno AUDIT_VERIFICA_COMPLETA_GIORNO del mese, "
                        "altrimenti incrementale (AUDIT_VERIFICA_FINESTRA_GIORNI)")
    d = sotto.add_parser("adotta-segmento-audit",
                         help="registra un segmento dell'audit già nel WORM (export divergente)")
    d.add_argument("--segmento", required=True, help="chiave WORM registro/audit/…jsonl")
    d.add_argument("--operatore", required=True)
    d.add_argument("--motivo", required=True)
    return parser.parse_args(argomenti)


async def _main(argomenti: list[str]) -> int:  # pragma: no cover — CLI
    opzioni = analizza(argomenti)
    await init_db(settings.database_url)
    try:
        # R05 (VD-C-080): comandi dell'operatore sul registro globale
        with ambito_conservatore("comando dell'operatore del registro delle impronte"):
            return await esegui(opzioni)
    finally:
        await close_db()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main(sys.argv[1:])))
