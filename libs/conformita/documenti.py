# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/documenti.py — composizione dei documenti di conformità (R15, VD-C-177).

  ``manuale``          Manuale di conservazione: contenuti minimi del paragrafo «Manuale di
                       conservazione» delle Linee guida (lettere a–l) e allegati generati
                       (classi e tempi, formati, metadati e profili, ruoli × permessi,
                       processo, verifiche, firme e marche, continuità, interoperabilità,
                       impostazioni con riferimento normativo);
  ``piano_sicurezza``  Piano della sicurezza del sistema di conservazione (Regolamento,
                       Allegato A, RO7): asset, minacce, misure realizzate con il codice che
                       le attua, cifratura, accessi, audit, copie e ripristino, incidenti,
                       fornitori, rischi residui (= criticità aperte del registro);
  ``guida``            GUIDA_FUNZIONALITA: che cosa fa ogni ruolo e da quali pagine;
  ``catalogo_route``   catalogo delle route del frontend (ruoli, abilitazione, menu).

Funzioni PURE: ricevono i fatti (``Fatti``: repository, database, variabili del
conservatore) e restituiscono un ``Documento``. Nessun dato è inventato: ciò che manca è un
blocco ``DaCompilare`` con chi lo fornisce; un requisito non riscontrato è ``DaVerificare``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from libs.conformita import conservatore as cons
from libs.conformita.funzioni import FUNZIONI, REALIZZATA, Funzione, stato_effettivo
from libs.conformita.modello import (
    CATALOGO_ROUTE,
    GUIDA,
    MANUALE,
    PIANO_SICUREZZA,
    Blocco,
    DaCompilare,
    DaVerificare,
    Documento,
    Elenco,
    Nota,
    Paragrafo,
    Sezione,
    Tabella,
    blocchi_campi,
)
from libs.conformita.requisiti import REQUISITI

CHI_RESPONSABILE = "il responsabile del servizio di conservazione"
CHI_SICUREZZA = "il responsabile della sicurezza dei sistemi"
CHI_DPO = "il responsabile della protezione dei dati"
CHI_ARCHIVISTA = "il responsabile della funzione archivistica di conservazione"
CHI_INFRA = "l'operatore dell'infrastruttura (pipeline di rilascio)"

STATI_APERTI = ("Aperta", "Parzialmente chiusa")


@dataclass
class Fatti:
    """Fatti raccolti: ``repo`` (fotografia del repository), ``db`` (configurazione in
    esercizio, ``database.vuoti()`` senza database), ``ambiente`` (variabili
    ``CONSERVATORE_*``)."""

    repo: dict[str, Any]
    db: dict[str, Any]
    ambiente: Mapping[str, str] = field(default_factory=dict)

    @property
    def con_database(self) -> bool:
        return bool(self.db.get("disponibile"))

    def aperte(self) -> list[dict[str, str]]:
        return [c for c in self.repo["criticita"] if c["stato"] in STATI_APERTI]

    def codici_aperti(self) -> set[str]:
        return {c["codice"] for c in self.aperte()}

    def impostazione(self, codice: str) -> tuple[Any, str]:
        """(valore effettivo al livello del conservatore, origine)."""
        if codice in self.db.get("impostazioni", {}):
            return self.db["impostazioni"][codice], "conservatore"
        voce = next((v for v in self.repo["impostazioni"] if v["codice"] == codice), None)
        if voce is None:
            raise KeyError(codice)
        if not self.con_database:
            return voce["predefinito"], "predefinito del codice (database non letto)"
        return voce["predefinito"], "predefinito del codice"


# ── aiuti ──────────────────────────────────────────────────────────────

def _testo(v: Any) -> str:
    if isinstance(v, bool):
        return "sì" if v else "no"
    if isinstance(v, list | tuple):
        return ", ".join(str(x) for x in v) if v else "(nessuno)"
    if v is None:
        return "(nessuno)"
    return str(v)


def _verifica(codice: str) -> list[Blocco]:
    """Blocco «da verificare» se il requisito non è stato riscontrato."""
    r = REQUISITI[codice]
    if r.riscontrato:
        return []
    return [DaVerificare(f"{r.riferimento} — {r.sintesi}", r.nota or "testo non riscontrato")]


def _riferimenti(*codici: str) -> Nota:
    return Nota("Requisiti: " + "; ".join(
        f"{REQUISITI[c].fonte}, {REQUISITI[c].riferimento}" for c in codici) + ".")


def _funzione(f: Funzione, aperte: set[str]) -> tuple[str, ...]:
    stato = stato_effettivo(f, aperte)
    prove = ", ".join(f"`{p}`" for p in f.prove)
    note = "; ".join(x for x in (f.nota, ", ".join(c for c in f.criticita)) if x)
    return (f.descrizione, stato, prove, note or "—")


def _tabella_funzioni(chiavi: tuple[str, ...], fatti: Fatti) -> Tabella:
    aperte = fatti.codici_aperti()
    return Tabella(("Funzione", "Stato", "Prove", "Limiti e riferimenti"),
                   tuple(_funzione(FUNZIONI[k], aperte) for k in chiavi))


def _tabella_impostazioni(fatti: Fatti, codici: tuple[str, ...], didascalia: str) -> Tabella:
    righe = []
    for c in codici:
        valore, origine = fatti.impostazione(c)
        voce = next(v for v in fatti.repo["impostazioni"] if v["codice"] == c)
        righe.append((f"`{c}`", voce["descrizione"], _testo(valore), origine,
                      voce["norm_ref"] or "scelta di servizio"))
    return Tabella(("Impostazione", "Significato", "Valore", "Origine", "Riferimento"),
                   tuple(righe), didascalia)


def _gruppo(fatti: Fatti, gruppo: str) -> tuple[str, ...]:
    return tuple(v["codice"] for v in fatti.repo["impostazioni"] if v["gruppo"] == gruppo)


def _avvertenze(fatti: Fatti) -> tuple[str, ...]:
    if fatti.con_database:
        return ()
    return ("Edizione generata senza il database di esercizio: ogni dato della configurazione "
            "in esercizio risulta da compilare. La versione da consegnare si genera dalla "
            "console, pagina «Documentazione del servizio».",)


FONTE_CONS = "config:CONSERVATORE_*"


def _fonti_db(*tabelle: str) -> tuple[str, ...]:
    return tuple(f"db:{t}" for t in tabelle)


# ── classi, firme, identità: dati del database ─────────────────────────

def _blocchi_classi(fatti: Fatti) -> list[Blocco]:
    classi = fatti.db.get("classi_globali") or []
    if not classi:
        return [DaCompilare("db.classi_documentali", "classi documentali del servizio con "
                            "tipologia, tempo di conservazione, formati e metadati",
                            CHI_ARCHIVISTA, "console, «Classi documentali»")]
    righe = tuple((c["codice"], c["nome"], c.get("tipologia") or "—",
                   "permanente" if c.get("conservazione_permanente")
                   else f"{c.get('anni_conservazione')} anni",
                   _testo(c.get("formati_ammessi") or []),
                   _testo(c.get("metadati_obbligatori") or []),
                   f"{c['max_dimensione_mb']} MB" if c.get("max_dimensione_mb") else "—")
                  for c in classi)
    blocchi: list[Blocco] = [Tabella(
        ("Codice", "Classe", "Tipologia SInCRO", "Conservazione", "Formati ammessi",
         "Metadati obbligatori", "Dimensione massima"), righe,
        "Classi documentali attive del servizio (database)")]
    if fatti.db.get("classi_enti"):
        blocchi.append(Paragrafo(f"Gli enti hanno inoltre {fatti.db['classi_enti']} classi "
                                 "documentali proprie attive, descritte nelle specificità del "
                                 "contratto di ciascun ente."))
    return blocchi


def _fornitori(fatti: Fatti, ruolo: str) -> list[dict[str, Any]]:
    return [f for f in fatti.db.get("fornitori_firma") or []
            if f.get("ruolo") == ruolo and f.get("attivo")]


def _blocchi_firma(fatti: Fatti) -> list[Blocco]:
    blocchi: list[Blocco] = []
    for ruolo, descrizione in (("FIRMA", "fornitore qualificato di firma o sigillo elettronico"),
                               ("MARCA", "fornitore qualificato di marca temporale (TSA)")):
        attivi = _fornitori(fatti, ruolo)
        if not attivi:
            blocchi.append(DaCompilare(
                f"db.fornitori_{ruolo.lower()}", descrizione + " attivo",
                CHI_RESPONSABILE + ", dopo il contratto con il prestatore qualificato "
                "(VD-P-05)", "console, «Fornitori di firma e marca»"))
            continue
        blocchi.append(Tabella(("Codice", "Tipo", "Nome", "Priorità"), tuple(
            (f["codice"], f["tipo"], f["nome"], str(f["priorita"])) for f in attivi),
            f"Fornitori attivi — {descrizione} (database)"))
    return blocchi


def _blocchi_identita(fatti: Fatti) -> list[Blocco]:
    fornitori = fatti.db.get("fornitori_identita") or []
    if not fornitori:
        return [DaCompilare("db.fornitori_identita", "fornitori d'identità registrati "
                            "(SPID/CIE, password con codice a tempo, passkey) e loro stato",
                            CHI_RESPONSABILE, "console, «Accesso e notifiche»")]
    return [Tabella(("Tipo", "Nome", "Attivo"), tuple(
        (f["tipo"], f["nome"], _testo(bool(f["attivo"]))) for f in fornitori),
        "Fornitori d'identità registrati (database)")]


def _blocchi_pec(fatti: Fatti) -> list[Blocco]:
    canali = [c for c in fatti.db.get("canali_pec") or [] if c.get("attivo")]
    if not canali:
        return [DaCompilare("db.canali_pec", "canale PEC del conservatore attivo",
                            CHI_RESPONSABILE + ", dopo il contratto con il gestore PEC",
                            "console, «Canali PEC»")]
    return [Tabella(("Codice", "Tipo", "Nome", "Priorità"), tuple(
        (c["codice"], c["tipo"], c["nome"], str(c["priorita"])) for c in canali),
        "Canali PEC attivi (database)")]


# ── matrice ruoli × permessi (codice) ──────────────────────────────────

def matrice_permessi(repo: dict[str, Any]) -> Tabella:
    p = repo["permessi"]
    ruoli = list(p["modelli"])
    nomi = repo["frontend"]["nomi_ruoli"]
    righe = []
    for codice, descrizione in p["permessi"].items():
        celle = tuple("sì" if codice in p["modelli"][r] else "—" for r in ruoli)
        delega = "mai" if codice in p["mai_delegabili"] else "sì"
        righe.append((f"`{codice}`", descrizione, *celle, delega))
    return Tabella(("Permesso", "Azione", *(nomi.get(r, r) for r in ruoli),
                    "Delegabile a un ruolo personalizzato"), tuple(righe),
                   "Ruoli fissi × permessi (`libs/verumdoc_rules/permessi.py`)")


# ── Manuale di conservazione ──────────────────────────────────────────

def manuale(fatti: Fatti) -> Documento:
    d = cons.dati(fatti.ambiente)
    repo = fatti.repo
    aperte = fatti.codici_aperti()
    s: list[Sezione] = []

    s.append(Sezione("man.0", "0", "Informazioni sul documento", [
        Paragrafo("Questo manuale è generato dalla configurazione reale del servizio, dal "
                  "database e dai registri del repository: ogni tabella tecnica viene dai "
                  "registri, non è scritta a mano (REGOLE_SVILUPPO §28.3). I dati che il "
                  "sistema non conosce sono indicati come dati da compilare, con chi li deve "
                  "fornire."),
        Paragrafo("Il manuale comprende il piano della sicurezza (documento «Piano della "
                  "sicurezza del sistema di conservazione», generato insieme) e il modello "
                  "organizzativo (sezione 2), come chiede il Regolamento (Allegato A, RO7)."),
        *_verifica("LG-MAN-NUM"),
        _riferimenti("RO7"),
    ], ("RO7", "LG-MAN-NUM"), ("repo:docs/audit/allegati/ricerca_normativa.md",)))

    s.append(Sezione("man.a", "1", "Soggetti responsabili del sistema di conservazione", [
        *blocchi_campi([
            ("Conservatore", d["denominazione"]),
            ("Codice fiscale", d["codice_fiscale"]),
            ("Partita IVA", d["partita_iva"]),
            ("Sede legale", d["sede_legale"]),
            ("PEC", d["pec"]),
            ("Responsabile del servizio di conservazione", d["responsabile_servizio_nome"]),
            ("Responsabile della funzione archivistica di conservazione",
             d["responsabile_funzione_archivistica_nome"]),
        ]),
        DaCompilare("manuale.storico_responsabili", "soggetti che nel tempo hanno avuto la "
                    "responsabilità del sistema, con date di nomina e cessazione",
                    cons.CHI_CONSERVATORE, "atti di nomina (il sistema non li registra)"),
        DaCompilare("manuale.deleghe", "deleghe in essere: soggetti, funzioni e ambiti",
                    cons.CHI_CONSERVATORE, "atti di delega"),
        _riferimenti("LG-MAN-A"),
    ], ("LG-MAN-A",), (FONTE_CONS,)))

    ruoli = repo["permessi"]["modelli"]
    nomi = repo["frontend"]["nomi_ruoli"]
    s.append(Sezione("man.b", "2", "Struttura organizzativa, funzioni e responsabilità", [
        Paragrafo("Ruoli richiesti al conservatore (Regolamento, Allegato A, RO1) e ruoli "
                  "ulteriori dell'organizzazione:"),
        *blocchi_campi([
            ("Responsabile del servizio di conservazione", d["responsabile_servizio_nome"]),
            ("Responsabile della funzione archivistica di conservazione",
             d["responsabile_funzione_archivistica_nome"]),
            ("Responsabile della sicurezza dei sistemi", d["responsabile_sicurezza_nome"]),
            ("Responsabile della protezione dei dati", d["responsabile_protezione_dati_nome"]),
        ]),
        Paragrafo("Ruoli nel sistema (ruoli fissi del codice; i permessi di ciascuno sono "
                  "nell'allegato 4, generato da `libs/verumdoc_rules/permessi.py`):"),
        Tabella(("Ruolo", "Codice", "Permessi"), tuple(
            (nomi.get(r, r), r, str(len(v))) for r, v in ruoli.items())),
        Paragrafo("Gli enti possono comporre ruoli personalizzati su "
                  + " o ".join(repo["permessi"]["ruoli_base"]) + " con i soli permessi che "
                  "il conservatore rende concedibili; le azioni mai delegabili restano ai "
                  "ruoli fissi (allegato 4)."),
        DaCompilare("manuale.modello_organizzativo", "modello organizzativo: organigramma, "
                    "funzioni interne ed esterne, obblighi dei soggetti",
                    cons.CHI_CONSERVATORE, "allegato del manuale (Regolamento, Allegato A, RO7)"),
        _riferimenti("LG-MAN-B", "RO1"),
    ], ("LG-MAN-B", "RO1"), (FONTE_CONS, "repo:libs/verumdoc_rules/permessi.py")))

    s.append(Sezione("man.c", "3", "Oggetti conservati, formati e metadati", [
        Paragrafo("Le tipologie conservate sono le classi documentali del servizio "
                  "(allegato 1); i formati ammessi sono nel catalogo dei formati (allegato 2); "
                  "i metadati per tipologia nei profili dell'Allegato 5 (allegato 3)."),
        *_blocchi_classi(fatti),
        _tabella_funzioni(("verifica_pdv",), fatti),
        DaCompilare("manuale.eccezioni_formati", "eccezioni concordate ai formati e ai "
                    "metadati (per ente o per classe)", CHI_ARCHIVISTA,
                    "specificità del contratto dell'ente"),
        _riferimenti("LG-MAN-C"),
    ], ("LG-MAN-C",), _fonti_db("classi_documentali")))

    s.append(Sezione("man.d", "4", "Presa in carico dei PdV e rapporto di versamento", [
        Paragrafo("I PdV si versano con le API REST (anche a sessioni di caricamento), con "
                  "SFTP e tramite PDND; ogni canale usa lo stesso caso d'uso di versamento. "
                  "Il rapporto di versamento riporta identificativo, data in UTC, impronte dei "
                  "documenti e le prove della catena delle impronte (allegato 5)."),
        _tabella_funzioni(("versamento", "rapporto_versamento", "firma_marca"), fatti),
        *_blocchi_firma(fatti),
        *_verifica("LG-PROC-RIFIUTI"),
        _riferimenti("LG-MAN-D", "RG1"),
    ], ("LG-MAN-D", "RG1", "LG-PROC-RIFIUTI"), _fonti_db("fornitori_firma")))

    s.append(Sezione("man.e", "5", "Processo di conservazione e trattamento dei PdA", [
        Paragrafo("Il PdA è descritto da un indice SInCRO, scritto nello storage immutabile "
                  "con retention pari al tempo di conservazione della classe, registrato nella "
                  "catena delle impronte e, con un fornitore qualificato attivo, sigillato e "
                  "marcato (allegati 5 e 7)."),
        _tabella_funzioni(("pda_ipda", "worm", "catena", "firma_marca"), fatti),
        *_verifica("LG-PROC"),
        _riferimenti("LG-MAN-E", "RG3"),
    ], ("LG-MAN-E", "RG3", "LG-PROC"), ()))

    s.append(Sezione("man.f", "6", "Esibizione ed esportazione con produzione del PdD", [
        Paragrafo("L'esibizione produce un PdD con i file conservati e le impronte "
                  "verificate; la ricerca è per ente. Riversamento in uscita e restituzione "
                  "sono pratiche del ciclo di vita (allegato 9)."),
        _tabella_funzioni(("esibizione", "ricerca", "riversamento"), fatti),
        *_verifica("LG-ESIB"),
        _riferimenti("LG-MAN-F", "RG9"),
    ], ("LG-MAN-F", "RG9", "LG-ESIB"), ()))

    infra = repo["infrastruttura"]
    servizi = tuple((x["nome"], _testo(x["repliche"]), _testo(x["esposto"]),
                     _testo(x["lavoratori"]), _testo(x["cronjob"]))
                    for x in infra["helm"]["servizi"])
    tf = infra["terraform"]
    segnaposto = [DaCompilare(f"infra.{k}", f"valore di esercizio `{k}` "
                              "(oggi «DA_IMPOSTARE» in `infra/helm/values.yaml`)", CHI_INFRA,
                              "infra/helm/values.yaml o pipeline")
                  for k in infra["helm"]["segnaposto"]]
    s.append(Sezione("man.g", "7", "Descrizione del sistema di conservazione", [
        Paragrafo("Componenti applicativi dichiarati nel Chart Helm (`infra/helm/values.yaml`, "
                  "unica fonte dei manifest):"),
        Tabella(("Servizio", "Repliche", "Esposto", "Lavoratori", "Lavori periodici"),
                servizi),
        Paragrafo("Infrastruttura dichiarata in Terraform (`infra/terraform`): ambienti "
                  f"{_testo(tf['ambienti'])}; moduli {_testo(tf['moduli'])}."),
        Tabella(("Voce", "Valore dichiarato"), (
            ("Regione principale", _testo(tf["regione"])),
            ("Regione di replica degli oggetti", _testo(tf["regione_replica"])),
            ("Modalità di Object Lock", _testo(tf["object_lock"])),
            ("Retention predefinita del bucket (anni)", _testo(tf["retention_predefinita_anni"])),
            ("PostgreSQL", _testo(tf["postgresql"])),
        )),
        *segnaposto,
        Paragrafo("Gestione ed evoluzione: regole di sviluppo e di rilascio in "
                  "`REGOLE_SVILUPPO.md`; procedure di esercizio nei runbook (allegato 6)."),
        _tabella_funzioni(("backup_dr",), fatti),
        *_verifica("LG-INFRA"),
        _riferimenti("LG-MAN-G", "RG8"),
    ], ("LG-MAN-G", "RG8", "LG-INFRA"), ("repo:infra/helm/values.yaml",
                             "repo:infra/terraform/environments/prod/main.tf")))

    s.append(Sezione("man.h", "8", "Monitoraggio e verifiche dell'integrità degli archivi", [
        Paragrafo("La verifica continua ricalcola le impronte degli oggetti conservati e ne "
                  "controlla retention e appartenenza alla catena; le anomalie generano allarmi "
                  "e si trattano con i runbook (allegato 6)."),
        _tabella_funzioni(("verifiche_integrita", "monitoraggio", "audit"), fatti),
        _tabella_impostazioni(fatti, _gruppo(fatti, "verifiche"),
                              "Impostazioni delle verifiche"),
        _riferimenti("LG-MAN-H", "RG8"),
    ], ("LG-MAN-H", "RG8"), ("repo:infra/observability/regole",)))

    s.append(Sezione("man.i", "9", "Produzione di duplicati e copie", [
        _tabella_funzioni(("duplicati_copie",), fatti),
        DaCompilare("manuale.procedura_copie", "procedura per duplicati e copie con "
                    "attestazione di conformità", CHI_ARCHIVISTA, "testo del manuale"),
        _riferimenti("LG-MAN-I"),
    ], ("LG-MAN-I",), ()))

    s.append(Sezione("man.j", "10", "Tempi di versamento e di scarto", [
        Paragrafo("I tempi di conservazione per classe sono nell'allegato 1. Lo scarto segue "
                  "l'iter della pratica (proposta, autorizzazione dell'ente, nulla osta per gli "
                  "enti pubblici, esecuzione con verbale nel WORM)."),
        _tabella_funzioni(("scarto",), fatti),
        _tabella_impostazioni(fatti, _gruppo(fatti, "scarto"), "Impostazioni dello scarto"),
        DaCompilare("manuale.tempi_versamento", "tempi entro cui le tipologie vanno "
                    "trasferite in conservazione", "l'ente produttore (manuale di gestione "
                    "documentale o accordo di versamento)", "accordo di versamento"),
        *_verifica("LG-SCARTO"),
        _riferimenti("LG-MAN-J"),
    ], ("LG-MAN-J", "LG-SCARTO"), _fonti_db("classi_documentali", "impostazioni_valori")))

    s.append(Sezione("man.k", "11", "Richiesta della presenza di un pubblico ufficiale", [
        DaCompilare("manuale.pubblico_ufficiale", "casi in cui è previsto l'intervento di un "
                    "pubblico ufficiale e modalità della richiesta", CHI_RESPONSABILE,
                    "testo del manuale"),
        _riferimenti("LG-MAN-K"),
    ], ("LG-MAN-K",), ()))

    s.append(Sezione("man.l", "12", "Normative in vigore nei luoghi di conservazione", [
        Paragrafo(f"Regione dichiarata in Terraform: {_testo(tf['regione'])}; regione di "
                  f"replica degli oggetti: {_testo(tf['regione_replica'])}."),
        *blocchi_campi([("Ubicazione dichiarata dei centri dati (RO5)",
                         d["ubicazione_data_center"])]),
        DaCompilare("manuale.normative_luoghi", "normative in vigore nei luoghi di "
                    "conservazione e, per centri dati extra-UE, esito della consultazione del "
                    "Garante (RO6)", CHI_DPO, "testo del manuale"),
        _riferimenti("LG-MAN-L", "RO5", "RO6"),
    ], ("LG-MAN-L", "RO5", "RO6"), (FONTE_CONS, "repo:infra/terraform/environments/prod/main.tf")))

    non_attive = tuple(f.chiave for f in FUNZIONI.values()
                       if stato_effettivo(f, aperte) != REALIZZATA)
    s.append(Sezione("man.pianificate", "13", "Funzioni non ancora pienamente attive", [
        Paragrafo("Funzioni realizzate in parte o predisposte in attesa di una risorsa esterna: "
                  "il manuale non le descrive come disponibili finché non lo sono (REGOLE §0.3)."),
        _tabella_funzioni(non_attive, fatti),
    ], (), ("codice:libs/conformita/funzioni.py",)))

    s += _allegati_manuale(fatti)
    return Documento(MANUALE, "Manuale di conservazione",
                     "Servizio di conservazione — documento generato dalla configurazione",
                     s, _avvertenze(fatti))


def _allegati_manuale(fatti: Fatti) -> list[Sezione]:
    repo = fatti.repo
    formati = repo["formati"]
    ammessi, origine = fatti.impostazione("FORMATI_AMMESSI") if any(
        v["codice"] == "FORMATI_AMMESSI" for v in repo["impostazioni"]) else (None, "")
    blocchi_formati: list[Blocco] = [
        Paragrafo(f"Catalogo dei formati versione {formati['versione']} dal "
                  f"{formati['valido_dal']} (`{formati['fonte']}`; {formati['norm_ref']}). "
                  "Esito del catalogo per formato; il conservatore e gli enti possono solo "
                  "restringere (console «Catalogo dei formati»)."),
        Tabella(("Formato", "Famiglia", "MIME", "Esito"), tuple(
            (f["nome"], f["famiglia"], _testo(f["mime"]), f["esito"])
            for f in formati["formati"])),
    ]
    if ammessi is not None:
        blocchi_formati.append(Paragrafo(f"FORMATI_AMMESSI ({origine}): {_testo(ammessi)}."))

    profili_db = fatti.db.get("profili") or []
    blocchi_profili: list[Blocco] = [
        Tabella(("Profilo", "Versione", "Valido dal", "Eredita da", "Campi", "Obbligatori",
                 "Da riscontrare", "Origine"), tuple(
            (p["nome"] or p["codice"], p["versione"], _testo(p["valido_dal"]),
             _testo(p["estende"]), str(p["campi"]), str(p["obbligatori"]),
             str(p["da_riscontrare"]), _testo(p["origine"])) for p in repo["profili"]),
            "Profili dei metadati del codice (`libs/verumdoc_rules/dati/profili`)"),
    ]
    if profili_db:
        blocchi_profili.append(Tabella(("Profilo", "Versione", "Valido dal", "Adozione"), tuple(
            (p["codice"], str(p["versione"]), _testo(p["valido_dal"]), _testo(p["adozione"]))
            for p in profili_db), "Versioni dei profili pubblicate dal conservatore (database)"))
    elif fatti.con_database:
        blocchi_profili.append(Paragrafo("Nessuna versione dei profili pubblicata dal "
                                         "conservatore nel database: valgono i profili del "
                                         "codice."))
    else:
        blocchi_profili.append(Paragrafo("Versioni dei profili pubblicate dal conservatore: "
                                         "database non letto in questa edizione."))
    blocchi_profili.append(Nota("Campi «da riscontrare»: VD-C-157 (riscontro dell'Allegato 5 "
                                "oltre il §4)."))

    concedibili, origine_c = fatti.impostazione("RUOLI_PERMESSI_CONCEDIBILI")
    runbook = tuple((r["titolo"], f"`{r['file']}`") for r in repo["runbook"])
    allarmi = repo["allarmi"]
    gravita: dict[str, int] = {}
    for a in allarmi:
        gravita[a["gravita"]] = gravita.get(a["gravita"], 0) + 1

    tf = repo["infrastruttura"]["terraform"]
    return [
        Sezione("man.all1", "A.1", "Allegato 1 — Tipologie, classi documentali e tempi",
                _blocchi_classi(fatti), ("LG-MAN-C", "LG-MAN-J"),
                _fonti_db("classi_documentali")),
        Sezione("man.all2", "A.2", "Allegato 2 — Formati ammessi", blocchi_formati,
                ("LG-MAN-C",), (f"repo:{formati['fonte']}",)),
        Sezione("man.all3", "A.3", "Allegato 3 — Metadati e profili (Allegato 5 delle Linee "
                "guida)", blocchi_profili, ("LG-MAN-C",),
                ("repo:libs/verumdoc_rules/dati/profili", *_fonti_db("profili_tipologia"))),
        Sezione("man.all4", "A.4", "Allegato 4 — Ruoli e responsabilità: ruoli × permessi", [
            matrice_permessi(repo),
            Paragrafo("Permessi concedibili ai ruoli personalizzati "
                      f"(RUOLI_PERMESSI_CONCEDIBILI, {origine_c}): {_testo(concedibili)}."),
        ], ("LG-MAN-B", "RO1"), ("repo:libs/verumdoc_rules/permessi.py",
                                 *_fonti_db("impostazioni_valori"))),
        Sezione("man.all5", "A.5", "Allegato 5 — Processo: versamento, archiviazione, "
                "distribuzione, scarto, riversamento", [
            _tabella_funzioni(("versamento", "verifica_pdv", "rapporto_versamento",
                               "pda_ipda", "esibizione", "scarto", "riversamento"), fatti),
            _tabella_impostazioni(fatti, _gruppo(fatti, "riversamento")
                                  + _gruppo(fatti, "restituzione"),
                                  "Impostazioni di riversamento e restituzione"),
            *_verifica("LG-PROC"),
        ], ("LG-PROC",), ("repo:docs/architettura/ciclo_vita.md",)),
        Sezione("man.all6", "A.6", "Allegato 6 — Verifiche di integrità, allarmi e runbook", [
            _tabella_funzioni(("verifiche_integrita", "catena", "audit"), fatti),
            Paragrafo(f"{len(allarmi)} allarmi in `infra/observability/regole` ("
                      + ", ".join(f"{n} «{g}»" for g, n in sorted(gravita.items()))
                      + "); elenco completo in `docs/catalogo/metriche.md`."),
            Tabella(("Runbook", "File"), runbook),
        ], ("LG-MAN-H", "RG8"), ("repo:docs/runbooks", "repo:infra/observability/regole")),
        Sezione("man.all7", "A.7", "Allegato 7 — Conservazione delle firme e delle marche", [
            _tabella_funzioni(("firma_marca",), fatti),
            *_blocchi_firma(fatti),
            _tabella_impostazioni(fatti, _gruppo(fatti, "firma_qualificata"),
                                  "Impostazioni della firma qualificata"),
            Paragrafo("Le buste firmate vanno nel WORM con chiave che contiene l'impronta "
                      "della busta; prima della scadenza del certificato o della marca, o se "
                      "un algoritmo esce da FIRMA_ALGORITMI_AMMESSI, si appone una nuova marca "
                      "d'archivio (`docs/architettura/firma_qualificata.md`)."),
        ], ("LG-MAN-E",), _fonti_db("fornitori_firma", "impostazioni_valori")),
        Sezione("man.all8", "A.8", "Allegato 8 — Continuità operativa (RPO e RTO)", [
            Tabella(("Voce", "Valore dichiarato"), (
                ("Copie del database (giorni di retention)", _testo(tf["backup_giorni"])),
                ("Database cifrato", _testo(tf["rds_cifrato"])),
                ("Protezione dall'eliminazione del database",
                 _testo(tf["rds_protezione_eliminazione"])),
                ("Regione di replica degli oggetti", _testo(tf["regione_replica"])),
            ), "Terraform, ambiente di produzione"),
            DaCompilare("continuita.rpo", "RPO misurato (dati persi al più)", CHI_SICUREZZA,
                        "esito della prova di ripristino (`docs/runbooks/disaster_recovery.md`)"),
            DaCompilare("continuita.rto", "RTO misurato (tempo di ripristino del servizio)",
                        CHI_SICUREZZA, "esito della prova di ripristino "
                        "(`docs/architettura/giorno1_staging.md`)"),
            DaCompilare("continuita.piano", "piano di continuità operativa secondo ISO/IEC "
                        "22313 (RS3)", CHI_SICUREZZA, "documento del sistema di gestione"),
            _tabella_funzioni(("backup_dr",), fatti),
            _riferimenti("RS3"),
        ], ("RS3",), ("repo:infra/terraform/environments/prod/main.tf",
                      "repo:docs/runbooks/disaster_recovery.md")),
        Sezione("man.all9", "A.9", "Allegato 9 — Interoperabilità ed esibizione", [
            Paragrafo("API documentate: OpenAPI 3.1 generata (`docs/catalogo/openapi.yaml`, "
                      "catalogo `docs/catalogo/endpoint.md`), guida d'integrazione "
                      "`docs/integrazione/README.md`, codici d'errore `docs/catalogo/errori.md`. "
                      "PdD coincidenti con i PdA per il passaggio ad altro conservatore: "
                      "riversamento in uscita (allegato 5)."),
            Paragrafo("Accesso delle persone (RG2):"),
            *_blocchi_identita(fatti),
            _tabella_funzioni(("accesso", "pec"), fatti),
            *_blocchi_pec(fatti),
            _riferimenti("RG1", "RG2", "RG9"),
        ], ("RG1", "RG2", "RG9"), ("repo:docs/catalogo/openapi.yaml",
                                   *_fonti_db("fornitori_identita", "canali_pec"))),
        Sezione("man.all10", "A.10", "Allegato 10 — Impostazioni con riferimento normativo", [
            _tabella_impostazioni(fatti, tuple(v["codice"] for v in repo["impostazioni"]
                                               if v["norm_ref"]),
                                  "Registro `libs/verumdoc_rules/parametri_servizio.py`, "
                                  "valori al livello del conservatore"),
        ], (), ("repo:libs/verumdoc_rules/parametri_servizio.py",
                *_fonti_db("impostazioni_valori"))),
    ]


# ── Piano della sicurezza ─────────────────────────────────────────────

@dataclass(frozen=True)
class Misura:
    misura: str
    descrizione: str
    codice: tuple[str, ...]
    prove: tuple[str, ...]


MISURE: tuple[Misura, ...] = (
    Misura("Autenticazione", "token RS256 verificati (emittente, destinatario, scadenza) in "
           "ogni servizio; chiavi solo da ambiente, rotazione con più chiavi per `kid`",
           ("libs/shared/security/jwt_rs256.py",),
           ("libs/shared/security/tests/test_jwt_rs256.py",)),
    Misura("Sessione", "cookie HttpOnly con protezione CSRF, uscita che revoca il token, "
           "scadenza per inattività", ("libs/shared/security/sessione_cookie.py",
                                       "libs/shared/security/revoca.py"),
           ("services/gateway/tests/test_r05_sessione_cookie.py",)),
    Misura("Permessi", "controllo centrale dei permessi; azioni mai delegabili negate a tre "
           "livelli (parametro, vincolo del database, servizio)",
           ("libs/shared/security/permessi.py", "libs/verumdoc_rules/permessi.py"),
           ("services/onboarding/tests/test_r10_ruoli_inviti.py",)),
    Misura("Isolamento fra enti", "Row Level Security per ente su ogni tabella con ente, "
           "ambito impostato da un solo punto", ("libs/shared/db/ambito.py",),
           ("services/gateway/tests/test_r05_rls_ente.py",)),
    Misura("Login del database", "servizi con login non proprietario; guardia all'avvio sul "
           "ruolo e sul timeout delle transazioni", ("libs/shared/db/guardia.py",),
           ("services/gateway/tests/test_r05_guardia_ruolo_db.py",)),
    Misura("Immutabilità", "oggetti conservati in S3 Object Lock COMPLIANCE con retention per "
           "classe, rinnovo mai in riduzione", ("libs/shared/storage/worm.py",
                                                 "infra/terraform/modules/s3/main.tf"),
           ("services/aip-builder/tests/test_s3_uploader.py",)),
    Misura("Integrità verificabile", "registro delle impronte con checkpoint firmati e "
           "controfirmati dal testimone indipendente", ("libs/catena",),
           ("libs/catena/tests", "services/testimone/tests")),
    Misura("Registri protetti", "registri append-only protetti anche dal proprietario; "
           "modifiche solo con la procedura delle migrazioni",
           ("libs/shared/db/migrazioni_registri.py",),
           ("services/gateway/tests/test_r04_correzioni_registri_ruoli.py",)),
    Misura("Registro delle operazioni", "audit append-only esportato a periodi nel WORM e "
           "verificato", ("libs/shared/security/audit.py",
                          "services/hashchain/app/registro/audit_worm.py"),
           ("services/hashchain/tests/test_r05_audit_worm.py",)),
    Misura("Permessi dell'infrastruttura", "un ruolo IAM per servizio con i soli prefissi "
           "necessari e negazioni esplicite; ACL Kafka per topic",
           ("infra/terraform/modules/iam_servizi/main.tf", "infra/k8s/verifica_manifest.py"),
           ("infra/k8s/test_verifica_manifest.py",)),
    Misura("Segreti nelle code", "segreti sigillati per il solo servizio che li usa e legati "
           "al contesto", ("libs/shared/security/sigillo.py",), ("libs/shared/security/tests",)),
    Misura("Errori", "errori solo dal catalogo, mai dettagli interni verso il client",
           ("libs/shared/errori",), ("tools/docs/test_genera_catalogo_errori.py",)),
)

MINACCE: tuple[tuple[str, str], ...] = (
    ("Accesso non autorizzato ai documenti di un ente",
     "Autenticazione; Sessione; Permessi; Isolamento fra enti"),
    ("Manomissione o cancellazione di un oggetto conservato",
     "Immutabilità; Integrità verificabile; Registri protetti"),
    ("Riscrittura della storia delle operazioni", "Registro delle operazioni; Registri protetti; "
     "Integrità verificabile"),
    ("Servizio esposto compromesso", "Login del database; Permessi dell'infrastruttura; "
     "Segreti nelle code"),
    ("Fuga di informazioni tecniche", "Errori"),
    ("Perdita di dati o indisponibilità", "Copie e ripristino (sezione 8)"),
    ("Abuso da parte di un amministratore", "Permessi; Registri protetti; Integrità verificabile"),
)


def piano_sicurezza(fatti: Fatti) -> Documento:
    d = cons.dati(fatti.ambiente)
    repo = fatti.repo
    tf = repo["infrastruttura"]["terraform"]
    s: list[Sezione] = []
    s.append(Sezione("sic.0", "0", "Informazioni sul documento", [
        Paragrafo("Piano della sicurezza del sistema di conservazione, parte del manuale di "
                  "conservazione (Regolamento, Allegato A, RO7). Le misure elencate sono quelle "
                  "realizzate nel codice e nella configurazione, con il file che le attua e il "
                  "test che le prova."),
        *_verifica("LG-SIC"),
        DaCompilare("sicurezza.certificazione_27001", "estremi della certificazione ISO/IEC "
                    "27001 con campo di applicazione specifico (RS1)", CHI_SICUREZZA,
                    "testo del piano"),
        _riferimenti("RO7", "RS1", "RO4"),
    ], ("RO7", "RS1", "RO4", "LG-SIC"), ()))
    s.append(Sezione("sic.1", "1", "Ambito e responsabilità", blocchi_campi([
        ("Responsabile della sicurezza dei sistemi", d["responsabile_sicurezza_nome"]),
        ("Responsabile della protezione dei dati", d["responsabile_protezione_dati_nome"]),
        ("Responsabile del servizio di conservazione", d["responsabile_servizio_nome"]),
    ]), (), (FONTE_CONS,)))
    servizi = repo["infrastruttura"]["helm"]["servizi"]
    s.append(Sezione("sic.2", "2", "Asset", [
        Paragrafo("Servizi applicativi: " + ", ".join(x["nome"] for x in servizi) + "."),
        Tabella(("Asset", "Dove"), (
            ("Oggetti conservati (PdA, RdV, registro, buste di firma)",
             "storage S3 con Object Lock (`infra/terraform/modules/s3`)"),
            ("Database della configurazione e dei registri",
             "PostgreSQL (`infra/terraform/modules/rds`)"),
            ("Messaggi fra i servizi", "Kafka (`infra/terraform/modules/kafka`)"),
            ("Indice di ricerca", "OpenSearch (`infra/terraform/modules/opensearch`)"),
            ("Chiavi di cifratura", "KMS (`infra/terraform/modules/kms`)"),
            ("Chiavi di firma del registro e del testimone",
             "Secret dei servizi hashchain e testimone (`infra/helm/values.yaml`)"),
        )),
        DaCompilare("sicurezza.inventario", "inventario completo degli asset con proprietario "
                    "e classificazione", CHI_SICUREZZA, "testo del piano"),
    ], (), ("repo:infra/helm/values.yaml", "repo:infra/terraform")))
    s.append(Sezione("sic.3", "3", "Minacce e misure che le contrastano", [
        Tabella(("Minaccia", "Misure (sezione 4)"), MINACCE),
        DaCompilare("sicurezza.analisi_rischi", "analisi dei rischi con probabilità, impatto e "
                    "accettazione del rischio residuo", CHI_SICUREZZA, "testo del piano"),
    ], (), ()))
    s.append(Sezione("sic.4", "4", "Misure tecniche realizzate", [
        Tabella(("Misura", "Descrizione", "Codice", "Prova"), tuple(
            (m.misura, m.descrizione, ", ".join(f"`{c}`" for c in m.codice),
             ", ".join(f"`{p}`" for p in m.prove)) for m in MISURE)),
        Nota("Dettaglio delle scelte di sicurezza: `docs/architettura/sicurezza.md`."),
        DaCompilare("sicurezza.prove_owasp", "esito delle prove OWASP sulle componenti "
                    "sviluppate (RS2)", CHI_SICUREZZA, "rapporto delle prove"),
        _riferimenti("RS2"),
    ], ("RS2",), ("codice:libs/conformita/documenti.py",)))
    s.append(Sezione("sic.5", "5", "Cifratura", [
        Tabella(("Voce", "Valore dichiarato"), (
            ("Database cifrato a riposo", _testo(tf["rds_cifrato"])),
            ("Chiavi gestite", "KMS (`infra/terraform/modules/kms`)"),
            ("Modalità di Object Lock", _testo(tf["object_lock"])),
        ), "Terraform"),
        *(DaCompilare(f"infra.{k}", f"valore di esercizio `{k}`", CHI_INFRA,
                      "infra/helm/values.yaml o pipeline")
          for k in repo["infrastruttura"]["helm"]["segnaposto"] if "certificate" in k),
        _tabella_impostazioni(fatti, ("FIRMA_ALGORITMO_IMPRONTA", "FIRMA_ALGORITMI_AMMESSI"),
                              "Algoritmi di impronta e firma"),
    ], (), ("repo:infra/terraform/modules/rds/main.tf",)))
    s.append(Sezione("sic.6", "6", "Controllo degli accessi", [
        Paragrafo("Azioni mai delegabili (solo ruoli fissi): "
                  + ", ".join(f"`{p}`" for p in repo["permessi"]["mai_delegabili"]) + "."),
        *_blocchi_identita(fatti),
        _tabella_impostazioni(fatti, _gruppo(fatti, "accesso") + _gruppo(fatti, "sessione"),
                              "Impostazioni di accesso e sessione"),
        Nota("Matrice ruoli × permessi: allegato 4 del manuale e `docs/catalogo/permessi.md`."),
    ], ("RG2",), ("repo:libs/verumdoc_rules/permessi.py", *_fonti_db("fornitori_identita"))))
    s.append(Sezione("sic.7", "7", "Registro delle operazioni e log", [
        _tabella_funzioni(("audit", "monitoraggio"), fatti),
        _tabella_impostazioni(fatti, _gruppo(fatti, "audit"), "Impostazioni dell'audit"),
    ], ("RG8",), ()))
    s.append(Sezione("sic.8", "8", "Copie di sicurezza e ripristino", [
        Tabella(("Voce", "Valore dichiarato"), (
            ("Copie del database (giorni)", _testo(tf["backup_giorni"])),
            ("Regione di replica degli oggetti", _testo(tf["regione_replica"])),
        ), "Terraform, ambiente di produzione"),
        Paragrafo("Procedura: `docs/runbooks/disaster_recovery.md`."),
        DaCompilare("continuita.rpo", "RPO misurato", CHI_SICUREZZA, "prova di ripristino"),
        DaCompilare("continuita.rto", "RTO misurato", CHI_SICUREZZA, "prova di ripristino"),
        _riferimenti("RS3"),
    ], ("RS3",), ("repo:infra/terraform/environments/prod/main.tf",)))
    pagina = [a for a in repo["allarmi"] if a["gravita"] == "pagina"]
    s.append(Sezione("sic.9", "9", "Gestione degli incidenti", [
        Paragrafo(f"{len(pagina)} allarmi a pagina (intervento immediato) su "
                  f"{len(repo['allarmi'])}; ogni allarme rimanda a un runbook "
                  "(`docs/catalogo/metriche.md`, controllo "
                  "`tests/integrity/test_osservabilita.py`)."),
        Tabella(("Runbook", "File"), tuple((r["titolo"], f"`{r['file']}`")
                                          for r in repo["runbook"])),
        DaCompilare("sicurezza.violazioni_dati", "procedura di notifica delle violazioni dei "
                    "dati personali (artt. 33-34 GDPR): chi decide, tempi, modelli", CHI_DPO,
                    "testo del piano"),
        *_verifica("LG-SIC"),
    ], ("LG-SIC",), ("repo:docs/runbooks", "repo:infra/observability/regole")))
    s.append(Sezione("sic.10", "10", "Fornitori e terze parti", [
        Paragrafo("Infrastruttura dichiarata per il provider Terraform «hashicorp/aws» "
                  f"(regione {_testo(tf['regione'])})."),
        *_blocchi_firma(fatti),
        *_blocchi_pec(fatti),
        *_blocchi_identita(fatti),
        DaCompilare("sicurezza.contratti_fornitori", "contratti, nomine a responsabile del "
                    "trattamento e qualificazioni dei fornitori", CHI_DPO,
                    "registro dei fornitori"),
    ], (), _fonti_db("fornitori_firma", "canali_pec", "fornitori_identita")))
    aperte = fatti.aperte()
    s.append(Sezione("sic.11", "11", "Rischi residui (criticità aperte del registro)", [
        Paragrafo(f"{len(aperte)} criticità aperte o parzialmente chiuse nel registro "
                  "`docs/registro/criticita.md`: sono i rischi residui accettati finché non "
                  "si chiudono."),
        Tabella(("Codice", "Gravità", "Area", "Criticità", "Stato"), tuple(
            (c["codice"], c["gravita"], c["area"], c["titolo"], c["stato"]) for c in aperte)),
    ], (), ("repo:docs/registro/criticita.md",)))
    s.append(Sezione("sic.12", "12", "Verifica periodica", [
        DaCompilare("sicurezza.verifica_periodica", "calendario e responsabili della verifica "
                    "periodica di conformità a norme e standard (RO8)", CHI_RESPONSABILE,
                    "testo del piano"),
        _riferimenti("RO8"),
    ], ("RO8",), ()))
    return Documento(PIANO_SICUREZZA, "Piano della sicurezza del sistema di conservazione",
                     "Allegato del manuale di conservazione — documento generato dalla "
                     "configurazione", s, _avvertenze(fatti))


# ── Guida alle funzionalità ───────────────────────────────────────────

def _route_del_ruolo(repo: dict[str, Any], ruolo: str) -> list[dict[str, Any]]:
    return [r for r in repo["frontend"]["route"]
            if r["pagina"] and r["percorso"] not in ("/login", "/attiva")
            and (r["ruoli"] is None or ruolo in r["ruoli"])]


def guida(fatti: Fatti) -> Documento:
    repo = fatti.repo
    p = repo["permessi"]
    nomi = repo["frontend"]["nomi_ruoli"]
    rilascio = repo.get("rilascio") or {}
    sezioni: list[Sezione] = [Sezione("guida.0", "0", "Come leggere questa guida", [
        Paragrafo("Per ogni ruolo: che cosa può fare (permessi del codice) e da quali pagine "
                  "della console. Guida generata da `libs/verumdoc_rules/permessi.py`, "
                  "`frontend/src/App.tsx` e `frontend/src/components/Layout.tsx`: se cambia "
                  "un permesso o una pagina, la guida si rigenera (REGOLE §0.2)."),
        Paragrafo(f"Ultima modifica: {rilascio['data']} (`{rilascio['changelog']}`).")
        if rilascio.get("data") else
        DaCompilare("guida.ultima_modifica", "data dell'ultimo rilascio (prima riga del "
                    "changelog)", "chi prepara il rilascio", "changelog/"),
    ], (),("repo:frontend/src/App.tsx", "repo:libs/verumdoc_rules/permessi.py"))]
    for i, (ruolo, permessi) in enumerate(p["modelli"].items(), start=1):
        voci_menu = [r for r in _route_del_ruolo(repo, ruolo) if r["menu"]]
        altre = [r for r in _route_del_ruolo(repo, ruolo) if not r["menu"]]
        blocchi: list[Blocco] = [
            Elenco(tuple(f"{p['permessi'][x]} (`{x}`" + (", mai delegabile" if x in
                   p["mai_delegabili"] else "") + ")" for x in p["permessi"] if x in permessi)),
        ]
        if voci_menu:
            blocchi.append(Tabella(("Area", "Pagina", "Percorso", "Note"), tuple(
                (r["menu"]["area"], r["menu"]["voce"], f"`{r['percorso']}`",
                 f"serve l'abilitazione «{r['scope']}»" if r["scope"] else "")
                for r in voci_menu)))
        if altre:
            blocchi.append(Paragrafo("Pagine raggiungibili da collegamenti interni: "
                                     + ", ".join(f"`{r['percorso']}`" for r in altre) + "."))
        if not voci_menu and ruolo == "API_SYSTEM":
            blocchi.append(Paragrafo("Ruolo dei sistemi versanti: usa le API, non la console "
                                     "(`docs/integrazione/README.md`)."))
        sezioni.append(Sezione(f"guida.{ruolo.lower()}", str(i),
                               f"{nomi.get(ruolo, ruolo)} ({ruolo})", blocchi, (),
                               ("repo:frontend/src/components/Layout.tsx",)))
    return Documento(GUIDA, "Guida alle funzionalità",
                     "Che cosa fa ogni ruolo nella console — documento generato", sezioni)


# ── Catalogo delle route ──────────────────────────────────────────────

def catalogo_route(fatti: Fatti) -> Documento:
    repo = fatti.repo
    nomi = repo["frontend"]["nomi_ruoli"]
    righe = []
    for r in repo["frontend"]["route"]:
        ruoli = "tutti gli utenti autenticati" if r["ruoli"] is None else ", ".join(
            nomi.get(x, x) for x in r["ruoli"])
        if r["percorso"] in ("/login", "/attiva"):
            ruoli = "pubblica"
        menu = f"{r['menu']['area']} · {r['menu']['voce']}" if r["menu"] else (
            "collegamenti interni" if r["pagina"] else "reindirizzamento")
        righe.append((f"`{r['percorso']}`", r["pagina"] or "—", ruoli, r["scope"] or "—", menu))
    senza = [r for r in repo["frontend"]["route"] if r["pagina"] and not r["menu"]
             and r["percorso"] not in ("/login", "/attiva")]
    return Documento(CATALOGO_ROUTE, "Catalogo delle route del frontend",
                     "Generato da `frontend/src/App.tsx` e `frontend/src/components/Layout.tsx`",
                     [Sezione("route.1", "1", "Route", [
                         Paragrafo(f"{len(righe)} route; {len(senza)} pagine senza voce di menu "
                                   "(raggiungibili da collegamenti interni). Il servizio "
                                   "ricontrolla sempre i permessi: la guardia della pagina "
                                   "evita solo di mostrare ciò che sarebbe rifiutato."),
                         Tabella(("Percorso", "Pagina", "Ruoli", "Abilitazione", "Menu"),
                                 tuple(righe)),
                     ], (), ("repo:frontend/src/App.tsx",))])


GENERATORI = {MANUALE: manuale, PIANO_SICUREZZA: piano_sicurezza, GUIDA: guida,
              CATALOGO_ROUTE: catalogo_route}


def genera_tutti(fatti: Fatti) -> dict[str, Documento]:
    return {tipo: genera(fatti) for tipo, genera in GENERATORI.items()}


__all__ = ["GENERATORI", "MINACCE", "MISURE", "Fatti", "catalogo_route", "genera_tutti",
           "guida", "manuale", "matrice_permessi", "piano_sicurezza"]
