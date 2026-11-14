# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Modulo: libs/shared/db/ruolo_prova.py — login del ruolo dei servizi per i test d'integrazione

Riferimenti: VD-C-080 e VD-C-113 (R05, agente r05-sicurezza); REGOLE_SVILUPPO
§23.3 (infrastruttura vera dove conta).

I test d'integrazione dei servizi girano su PostgreSQL vero. Fino a R04 il codice
dei servizi entrava come SUPERUTENTE, che ignora la Row Level Security: un errore
di ambito non si sarebbe mai visto. Da R05 ``tests/infra_reale.py`` di ogni
servizio inizializza il database del servizio con un login NON superutente,
membro di ``verumdoc_applicazione`` (come in esercizio), e lascia il
superutente solo alla preparazione dei dati e ai controlli dei test (``sql()``)
e alla manutenzione delle partizioni (``DATABASE_URL_MANUTENZIONE``).

Server di prova con autenticazione ``trust`` (nessuna password): il login si
crea una volta per cluster, in modo idempotente e sicuro con più suite in
parallelo.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

#: Login dei test con i soli diritti di ``verumdoc_applicazione``.
RUOLO_SERVIZIO_PROVA = "vd_prova_servizio"
#: Gruppo dei servizi (migrazione 20261023_031).
GRUPPO_APPLICAZIONE = "verumdoc_applicazione"
#: Revisione 3 di R05 (T5): login di prova del lavoratore di ciclo-vita, membro
#: anche di ``verumdoc_ciclo_vita_lavoratore`` (migrazione 20261023_043): nei
#: test API e lavoratore girano nello stesso processo; i diritti dei due login
#: separati li prova ``tests/test_r05_revisione3_login_separati.py``.
RUOLO_LAVORATORE_CICLO_VITA_PROVA = "vd_prova_ciclo_vita_lavoratore"
GRUPPO_LAVORATORE_CICLO_VITA = "verumdoc_ciclo_vita_lavoratore"
#: Revisione R06 (V4): gruppo dei login dei servizi del portale, gli unici con
#: diritti su ``sessioni_attive`` (migrazione 20261030_045). Il login di prova
#: dei servizi ne è membro (i test del server SFTP non toccano la tabella; i
#: diritti di un login del solo ``verumdoc_applicazione`` li prova
#: ``services/gateway/tests/test_r06_revisione_sessioni_privilegi.py``).
GRUPPO_SESSIONI_PORTALE = "verumdoc_sessioni_portale"
#: R10 (D08): gruppo dell'unico login con diritti sulle credenziali locali
#: (password, codici a tempo, passkey: migrazione 20261102_049), cioè il gateway.
#: Il login di prova ne è membro (i test del gateway le usano); che il gruppo
#: dei servizi da solo non le legga lo prova
#: ``services/gateway/tests/test_r10_accesso_locale.py``.
GRUPPO_CREDENZIALI_ACCESSO = "verumdoc_credenziali_accesso"
#: R12a (VD-C-148, revisione): gruppo del solo login della hashchain, unico con diritti
#: sull'elenco degli oggetti del registro (migrazione 20261105_052). Il login di prova
#: predefinito ne è membro (i test della hashchain lo usano).
GRUPPO_REGISTRO_WORM = "verumdoc_registro_worm"
#: R13a (migrazione 20261107_054): documenti del conservatore da sigillare (login di
#: aip-builder, hashchain e dashboard-api) e registro dei fornitori di firma (login
#: di onboarding). Il login di prova predefinito ne è membro; il login del servizio
#: di firma ha SOLO ``verumdoc_firma`` (lettura dei fornitori e dei parametri).
GRUPPO_FIRME_DOCUMENTI = "verumdoc_firme_documenti"
#: revisione R13a (5): SOLO la hashchain aggiorna i documenti (sigillo, rimarcatura)
GRUPPO_FIRME_DOCUMENTI_SIGILLO = "verumdoc_firme_documenti_sigillo"
GRUPPO_FIRMA_FORNITORI = "verumdoc_firma_fornitori"
GRUPPO_FIRMA = "verumdoc_firma"
#: R13b (migrazione 20261108_055): canali PEC (console), lavoratore della posta, chi
#: accoda i messaggi PEC, canale personale della campanella. Il login di prova
#: predefinito ne è membro; i diritti separati li prova
#: ``services/notification/tests/test_r13b_pec_db.py``.
GRUPPO_PEC_CANALI = "verumdoc_pec_canali"
GRUPPO_PEC = "verumdoc_pec"
GRUPPO_PEC_ACCODA = "verumdoc_pec_accoda"
GRUPPO_NOTIFICHE_PERSONALI = "verumdoc_notifiche_personali"
#: R14 (migrazione 20261109_056): pulizia delle notifiche personali e lease dei lavori
#: periodici (login del lavoratore della posta). Il login di prova predefinito ne è membro.
GRUPPO_NOTIFICHE_PULIZIA = "verumdoc_notifiche_pulizia"
#: R16 (migrazione 20261110_057): documenti firmati dal responsabile con la firma personale
#: (login di dashboard-api). Il login di prova predefinito ne è membro; i diritti per colonna
#: li prova ``services/dashboard-api/tests/test_r16_firma_responsabile.py``.
GRUPPO_FIRME_RESPONSABILE = "verumdoc_firme_responsabile"
#: R15 (migrazione 20261111_058): versioni della documentazione del servizio (login di
#: dashboard-api) e archivio WORM delle versioni (login della hashchain). Il login di prova
#: predefinito ne è membro; i diritti separati li prova
#: ``services/gateway/tests/test_r15_migrazione_058.py``.
GRUPPO_DOCUMENTAZIONE = "verumdoc_documentazione"
GRUPPO_DOCUMENTAZIONE_ARCHIVIO = "verumdoc_documentazione_archivio"
#: R17 (migrazione 20261112_059): registrazione delle conferme firmate dal gateway (SOLO il
#: login dell'API di ciclo-vita). Il login di prova predefinito ne è membro; i diritti li
#: prova ``services/ciclo-vita/tests/test_r17_conferme_decisione.py``.
GRUPPO_CONFERME_REGISTRA = "verumdoc_conferme_registra"


def url_con_utente(url: str, utente: str) -> str:
    """Lo stesso URL con un altro utente e senza password."""
    parti = urlsplit(url)
    host = parti.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parti.scheme, f"{utente}@{host}", parti.path, parti.query, parti.fragment))


async def assicura_ruolo_servizio(url_superutente: str,
                                  ruolo: str = RUOLO_SERVIZIO_PROVA,
                                  gruppi: tuple[str, ...] = (), *,
                                  solo_gruppi: bool = False) -> None:
    """Crea (se manca) il login di prova membro di ``verumdoc_applicazione`` (e
    dei ``gruppi`` indicati, costanti dei test). ``solo_gruppi``: SOLO i gruppi
    indicati (R13a: login del servizio di firma, senza il gruppo dei servizi)."""
    import asyncpg

    dsn = url_superutente.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        # nome del ruolo: costante del modulo o dei test, mai un dato esterno
        crea = ("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "  # noqa: S608
                f"'{ruolo}') THEN CREATE ROLE {ruolo} LOGIN NOSUPERUSER NOBYPASSRLS; END IF; "
                "EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; END $$")
        await conn.execute(crea)
        # Revisione 2 di R05 (S7): come i login dei servizi in esercizio
        # (guardia all'avvio): una transazione dimenticata si chiude da sola
        await conn.execute(f"ALTER ROLE {ruolo} SET idle_in_transaction_session_timeout "
                           "= '15min'")
        # R10: le credenziali solo al login di prova predefinito (quello del gateway)
        candidati = (GRUPPO_SESSIONI_PORTALE,) + (
            (GRUPPO_CREDENZIALI_ACCESSO, GRUPPO_REGISTRO_WORM, GRUPPO_FIRME_DOCUMENTI,
             GRUPPO_FIRME_DOCUMENTI_SIGILLO, GRUPPO_FIRMA_FORNITORI, GRUPPO_PEC_CANALI,
             GRUPPO_PEC, GRUPPO_PEC_ACCODA, GRUPPO_NOTIFICHE_PERSONALI,
             GRUPPO_NOTIFICHE_PULIZIA, GRUPPO_FIRME_RESPONSABILE, GRUPPO_DOCUMENTAZIONE,
             GRUPPO_DOCUMENTAZIONE_ARCHIVIO, GRUPPO_CONFERME_REGISTRA)
            if ruolo == RUOLO_SERVIZIO_PROVA else ())
        portale = tuple([g for g in candidati if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", g)])
        base = () if solo_gruppi else (GRUPPO_APPLICAZIONE, *portale)
        for gruppo in (*base, *gruppi):
            if await conn.fetchval(
                    "SELECT pg_has_role($1, $2, 'MEMBER')", ruolo, gruppo):
                continue
            try:
                await conn.execute(f"GRANT {gruppo} TO {ruolo}")
            except asyncpg.exceptions.InternalServerError:
                # «tuple concurrently updated»: un'altra suite l'ha appena concesso
                if not await conn.fetchval(
                        "SELECT pg_has_role($1, $2, 'MEMBER')", ruolo, gruppo):
                    raise
    finally:
        await conn.close()


async def url_ruolo_servizio(url_superutente: str, ruolo: str = RUOLO_SERVIZIO_PROVA,
                             gruppi: tuple[str, ...] = ()) -> str:
    """URL del database di prova con il login dei servizi (creato se manca)."""
    await assicura_ruolo_servizio(url_superutente, ruolo, gruppi)
    return url_con_utente(url_superutente, ruolo)


__all__ = ["GRUPPO_APPLICAZIONE", "GRUPPO_CONFERME_REGISTRA", "GRUPPO_CREDENZIALI_ACCESSO",
           "GRUPPO_DOCUMENTAZIONE",
           "GRUPPO_DOCUMENTAZIONE_ARCHIVIO", "GRUPPO_FIRMA",
           "GRUPPO_FIRMA_FORNITORI", "GRUPPO_FIRME_DOCUMENTI", "GRUPPO_FIRME_RESPONSABILE",
           "GRUPPO_NOTIFICHE_PERSONALI",
           "GRUPPO_NOTIFICHE_PULIZIA",
           "GRUPPO_PEC", "GRUPPO_PEC_ACCODA", "GRUPPO_PEC_CANALI",
           "GRUPPO_FIRME_DOCUMENTI_SIGILLO", "GRUPPO_LAVORATORE_CICLO_VITA",
           "GRUPPO_SESSIONI_PORTALE",
           "RUOLO_LAVORATORE_CICLO_VITA_PROVA", "RUOLO_SERVIZIO_PROVA",
           "assicura_ruolo_servizio", "url_con_utente", "url_ruolo_servizio"]
