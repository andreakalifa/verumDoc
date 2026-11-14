# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/onboarding/app/services/configurazione.py — casi d'uso della
configurazione con bozze, anteprima e applicazione (contratto R05 Q1).

Riferimenti: REGOLE_SVILUPPO §8.2-§8.5, §8.11, §8.13, §9.1, §12.5-§12.7, §13,
§14, §15.2-§15.3, §18; piano R03-R05 §6.

Tutte le risoluzioni passano dal motore ``libs/verumdoc_rules`` con le stesse
query del gateway (``lettura``); permessi e piano sono funzioni pure del motore
(``configurazione``); l'impatto si calcola con ``confronta`` sul campione di
``campione`` (lo stesso codice di ``tools/confronta_regole.py``).

Scritture (solo in ``applica_bozza`` e nelle risposte alle proposte), sempre in
UNA transazione con la voce di audit (§15.3):
  - impostazioni di CONSERVATORE, ENTE, CLASSE: riga corrente in
    ``impostazioni_valori`` (letta da gateway, quote, partizioni) + riga
    append-only in ``impostazioni_storico``; per le voci della classe che stanno
    nelle colonne di ``classi_documentali`` si aggiorna la colonna;
  - accordo: NUOVA versione in ``accordi_versamento`` (mai modificata);
  - profilo: NUOVA versione in ``profili_tipologia`` o ``profili_metadati``;
  - proposta: una riga per ente in ``configurazione_proposte`` (un solo INSERT …
    SELECT), nessun valore cambiato finché l'ente non accetta.

L'anteprima gira in una transazione READ ONLY con ``statement_timeout``: il
database stesso rifiuta qualunque scrittura.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Any

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from libs.shared.db import get_async_session, get_async_session_lettura
from libs.shared.errori import ErroreApi, errore_registrato
from libs.verumdoc_rules import CATALOGO, REGISTRO, AmbienteRegole
from libs.verumdoc_rules.accordi import AccordoVersamento, vigenti_per_codice
from libs.verumdoc_rules.campione import Variante, carica_campione
from libs.verumdoc_rules.catalogo import catalogo_impostazioni
from libs.verumdoc_rules.catalogo_v1 import (
    CLASSE_DIMENSIONE_MAX_MB,
    IDEMPOTENZA_OBBLIGATORIA,
    IDEMPOTENZA_TRANSIZIONE_FINO_AL,
)
from libs.verumdoc_rules.catalogo_v2 import PROFILO_TIPOLOGIA
from libs.verumdoc_rules.catalogo_v4 import PROFILO_TIPOLOGIA_VERSIONE
from libs.verumdoc_rules.configurazione import (
    RUOLI_CONSERVATORE,
    Ambito,
    Attore,
    ModificaValore,
    Piano,
    Risorsa,
    esito_versione_tipologia,
    motivi_campi_classe,
    motivo_blocco_ambito,
    motivo_blocco_lettura,
    motivo_blocco_voce,
    normalizza_modifiche,
    piano_profilo,
    piano_valori,
)
from libs.verumdoc_rules.confronta import confronta
from libs.verumdoc_rules.impostazioni import (
    ASSENTE,
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Impostazione,
    Livello,
    RegistroImpostazioni,
    Risolutore,
    giorno_locale,
    valore_per_fotografia,
)
from libs.verumdoc_rules.lettura import (
    CODICI_IN_COLONNA_CLASSE,
    SQL_PROFILI_TIPOLOGIA,
    SQL_VALORI_LIVELLI,
    accordi_da_righe,
    json_o_none,
    livelli_da_righe,
    prepara_ambiente,
    profili_classe_da_righe,
    profili_tipologia_da_righe,
    tipologia_effettiva,
    valori_colonne_classe,
)
from libs.verumdoc_rules.override import valida_override
from libs.verumdoc_rules.profili import (
    ADOZIONE_AUTOMATICA,
    ADOZIONI,
    CampoProfilo,
    ProfiloMetadati,
    ProfiloNonValido,
    almeno_severo,
)
from libs.verumdoc_rules.tipologie import tipologie, versioni_tipologia
from libs.verumdoc_rules.validatore import (
    CAMPI_METADATI_INDICE,
    controlla_forma,
    esporta_profilo,
    rilievo,
    valida_versamento,
)

logger = structlog.get_logger(__name__)


class ImpostazioniConfigurazione(BaseSettings):
    """Limiti dell'anteprima e valori del conservatore da ambiente (come il gateway)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")
    #: PdV recenti al massimo nel campione dell'impatto (dichiarato nella risposta)
    configurazione_anteprima_campione_max: int = Field(2000, ge=1, le=100_000)
    #: secondi al massimo per riapplicare il motore al campione
    configurazione_anteprima_tempo_max_s: float = Field(5.0, gt=0, le=120)
    #: finestra del campione (giorni prima di oggi)
    configurazione_anteprima_finestra_giorni: int = Field(30, ge=1, le=366)
    configurazione_anteprima_esempi: int = Field(10, ge=0, le=100)
    #: tempo massimo di ogni query dell'anteprima (millisecondi)
    configurazione_anteprima_query_ms: int = Field(10_000, ge=100, le=600_000)
    #: stesse variabili del gateway (livello CONSERVATORE da ambiente): con valori
    #: diversi la console mostrerebbe valori diversi da quelli applicati
    idempotenza_obbligatoria: bool | None = None
    idempotenza_transizione_fino_al: date | None = None


impostazioni = ImpostazioniConfigurazione()

#: Massimo di elementi in una pagina (elenchi a cursore).
LIMITE_MASSIMO = 200
#: R07: una modifica programmata decorre al più entro un anno.
PROGRAMMATA_MASSIMO_GIORNI = 366
STATI_BOZZA = ("APERTA", "PROGRAMMATA", "APPLICATA", "ANNULLATA")
STATI_PROPOSTA = ("PROPOSTA", "ACCETTATA", "RIFIUTATA")


@cache
def registro_console() -> RegistroImpostazioni:
    """Catalogo completo: versamento, quote, normative e retention dei registri."""
    from libs.shared.db.partizioni import REGISTRO_RETENZIONE

    registri = [replace(v, ambito_ammesso=frozenset({Livello.CONSERVATORE}),
                        riservata_conservatore=True, gruppo="registri",
                        applicata_da="manutenzione delle partizioni (libs/shared/db/partizioni)")
                for v in REGISTRO_RETENZIONE]
    return catalogo_impostazioni(registri)


def valori_conservatore_da_ambiente() -> dict[str, Any]:
    """Valori del conservatore da variabili d'ambiente, gli stessi dei servizi che
    li applicano (la riga del database prevale): idempotenza (gateway) e, R07
    (D13), limiti dei piani ``QUOTE_*`` (libs.shared.security.quote)."""
    from libs.shared.security.quote import ImpostazioniQuote, ambiente_piani

    valori: dict[str, Any] = dict(ambiente_piani(ImpostazioniQuote()))
    if impostazioni.idempotenza_obbligatoria is not None:
        valori[IDEMPOTENZA_OBBLIGATORIA] = impostazioni.idempotenza_obbligatoria
    if impostazioni.idempotenza_transizione_fino_al is not None:
        valori[IDEMPOTENZA_TRANSIZIONE_FINO_AL] = impostazioni.idempotenza_transizione_fino_al
    return valori


def _errore_db(evento: str, exc: Exception) -> ErroreApi:
    return errore_registrato("CFG-503", exc, evento=evento)


# ── Cursori ─────────────────────────────────────────────────────────

def codifica_cursore(elenco: str, quando: datetime, ultimo: str) -> str:
    grezzo = json.dumps([elenco, quando.isoformat(), ultimo]).encode()
    return base64.urlsafe_b64encode(grezzo).decode().rstrip("=")


def decodifica_cursore(elenco: str, cursore: str) -> tuple[datetime, str]:
    try:
        grezzo = base64.urlsafe_b64decode(cursore + "=" * (-len(cursore) % 4))
        nome, quando, ultimo = json.loads(grezzo)
        if nome != elenco:
            raise ValueError("cursore di un altro elenco")
        return datetime.fromisoformat(quando), str(uuid.UUID(ultimo))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ErroreApi("CFG-400", "Cursore non valido: ripartire dalla prima pagina") from exc


# ── Contesto: ente, classe, accordo ─────────────────────────────────

@dataclass(frozen=True)
class ClasseInfo:
    id: str
    codice: str
    tenant_id: str | None
    formati_ammessi: tuple[str, ...]
    metadati_obbligatori: tuple[str, ...]
    max_dimensione_mb: int | None
    attiva: bool

    @property
    def di_base(self) -> bool:
        return self.tenant_id is None

    def colonne(self) -> dict[str, Any]:
        return valori_colonne_classe(self.formati_ammessi, self.metadati_obbligatori,
                                     self.max_dimensione_mb)


def _uuid(valore: Any) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


async def verifica_ente(session: Any, attore: Attore, ente: str | None) -> str:
    """Ente esistente e visibile all'attore; altrimenti 404 (mai 403 fra enti, §14.4)."""
    eid = _uuid(ente)
    if eid is None or (not attore.conservatore and eid != attore.tenant_id):
        raise ErroreApi("CFG-404", "Ente non trovato")
    riga = (await session.execute(text("SELECT 1 FROM public.tenants WHERE id = CAST(:t AS uuid)"),
                                  {"t": eid})).first()
    if riga is None:
        raise ErroreApi("CFG-404", "Ente non trovato")
    return eid


async def carica_classe(session: Any, attore: Attore, classe: str,
                        ente: str | None) -> ClasseInfo:
    """Classe per id o per codice, nel perimetro dell'attore: classi di base e del
    proprio ente (il conservatore vede tutte le classi per id); per codice la
    classe dell'ente prevale su quella di base. Fuori perimetro: 404."""
    cid = _uuid(classe)
    colonne = ("SELECT id::text AS id, codice, tenant_id::text AS tenant_id, attiva, "
               "formati_ammessi::text AS formati_ammessi, "
               "metadati_obbligatori::text AS metadati_obbligatori, max_dimensione_mb "
               "FROM public.classi_documentali ")
    if cid is not None:
        riga = (await session.execute(text(colonne + "WHERE id = CAST(:c AS uuid)"),
                                      {"c": cid})).mappings().first()
    else:
        riga = (await session.execute(text(
            colonne + "WHERE codice = :c AND (tenant_id = CAST(:t AS uuid) OR tenant_id IS NULL) "
            "ORDER BY tenant_id NULLS LAST LIMIT 1"), {"c": classe, "t": ente})).mappings().first()
    if riga is None:
        raise ErroreApi("CFG-404", f"Classe {classe!r} non trovata")
    info = ClasseInfo(
        id=riga["id"], codice=riga["codice"], tenant_id=riga["tenant_id"],
        formati_ammessi=tuple(json_o_none(riga["formati_ammessi"]) or ()),
        metadati_obbligatori=tuple(json_o_none(riga["metadati_obbligatori"]) or ()),
        max_dimensione_mb=riga["max_dimensione_mb"], attiva=bool(riga["attiva"]))
    if not attore.conservatore and info.tenant_id not in (None, attore.tenant_id):
        raise ErroreApi("CFG-404", f"Classe {classe!r} non trovata")
    if ente is not None and info.tenant_id not in (None, ente):
        raise ErroreApi("CFG-404", f"Classe {classe!r} non trovata per l'ente")
    return info


SQL_ACCORDI_TUTTE_LE_VERSIONI = (
    "SELECT id::text AS id, tenant_id::text AS tenant_id, codice, versione, "
    "classe_id::text AS classe_id, valido_dal, cessato, valori::text AS valori, descrizione "
    "FROM public.accordi_versamento WHERE tenant_id = CAST(:t AS uuid)")


@dataclass
class Contesto:
    """Configurazione letta per un ambito (valori attuali di ogni livello)."""

    ambito: Ambito
    classe: ClasseInfo | None
    livelli: dict[Livello, dict[str, Any]]
    accordi: list[AccordoVersamento] = field(default_factory=list)
    accordo_base: AccordoVersamento | None = None
    profili_classe: list[ProfiloMetadati] = field(default_factory=list)
    profili_tipologia_db: dict[str, list[ProfiloMetadati]] = field(default_factory=dict)

    def risoluzione(self, adesso: datetime) -> ContestoRisoluzione:
        return ContestoRisoluzione(
            adesso=adesso, tenant_id=self.ambito.ente,
            classe_id=self.classe.id if self.classe else None,
            accordo_id=self.accordo_base.id if self.accordo_base else None)


async def _classe_per_id(session: Any, classe_id: str) -> ClasseInfo:
    riga = (await session.execute(text(
        "SELECT id::text AS id, codice, tenant_id::text AS tenant_id, attiva, "
        "formati_ammessi::text AS formati_ammessi, "
        "metadati_obbligatori::text AS metadati_obbligatori, max_dimensione_mb "
        "FROM public.classi_documentali WHERE id = CAST(:c AS uuid)"), {"c": classe_id}
    )).mappings().one()
    return ClasseInfo(
        id=riga["id"], codice=riga["codice"], tenant_id=riga["tenant_id"],
        formati_ammessi=tuple(json_o_none(riga["formati_ammessi"]) or ()),
        metadati_obbligatori=tuple(json_o_none(riga["metadati_obbligatori"]) or ()),
        max_dimensione_mb=riga["max_dimensione_mb"], attiva=bool(riga["attiva"]))


async def leggi_contesto(session: Any, ambito: Ambito, classe: ClasseInfo | None,
                         ) -> Contesto:
    """Valori attuali di tutti i livelli dell'ambito (query uniche di ``lettura``).

    Per un accordo esistente la classe è quella dell'accordo (se ne ha una)."""
    accordi: list[AccordoVersamento] = []
    if ambito.ente:
        accordi = accordi_da_righe((await session.execute(
            text(SQL_ACCORDI_TUTTE_LE_VERSIONI), {"t": ambito.ente})).mappings().all())
    base = max((a for a in accordi if a.codice == ambito.accordo), key=lambda a: a.versione,
               default=None) if ambito.accordo else None
    if classe is None and base is not None and base.classe_id:
        classe = await _classe_per_id(session, base.classe_id)
    parametri = {"t": ambito.ente, "c": classe.id if classe else None}
    righe = (await session.execute(text(SQL_VALORI_LIVELLI), parametri)).mappings().all()
    livelli = livelli_da_righe(righe, conservatore_ambiente=valori_conservatore_da_ambiente(),
                               colonne_classe=classe.colonne() if classe else None)
    contesto = Contesto(ambito=ambito, classe=classe, livelli=livelli, accordi=accordi,
                        accordo_base=base)
    if ambito.accordo:
        contesto.livelli[Livello.ACCORDO] = dict(base.valori) if base else {}
    if classe is not None:
        contesto.profili_classe = profili_classe_da_righe((await session.execute(text(
            "SELECT id::text AS id, versione, valido_dal, valido_al, campi::text AS campi "
            "FROM public.profili_metadati WHERE classe_id = CAST(:c AS uuid)"),
            {"c": classe.id})).mappings().all(), classe.codice)
    contesto.profili_tipologia_db = profili_tipologia_da_righe(
        (await session.execute(text(SQL_PROFILI_TIPOLOGIA))).mappings().all())
    return contesto


# ── Ambito di una richiesta ─────────────────────────────────────────

async def risolvi_ambito(session: Any, attore: Attore, risorsa: Risorsa,
                         grezzo: Mapping[str, Any]) -> tuple[Ambito, ClasseInfo | None]:
    """Ambito della richiesta con ente e classe verificati nel perimetro dell'attore.

    Per l'amministratore dell'ente l'ente predefinito è il suo."""
    try:
        livello = Livello(str(grezzo.get("livello") or ""))
    except ValueError as exc:
        raise ErroreApi("CFG-400", "Livello non valido (CONSERVATORE, ENTE, CLASSE, ACCORDO, "
                                   "PDV)") from exc
    ente = grezzo.get("ente")
    if ente is None and not attore.conservatore and livello is not Livello.CONSERVATORE:
        ente = attore.tenant_id
    classe_grezza = grezzo.get("classe")
    accordo = grezzo.get("accordo")
    if accordo is not None and (not isinstance(accordo, str) or not accordo
                                or len(accordo) > 50):
        raise ErroreApi("CFG-400", "Codice dell'accordo non valido")
    ente_id = await verifica_ente(session, attore, ente) if ente is not None else None
    classe: ClasseInfo | None = None
    if classe_grezza is not None and livello in (Livello.CLASSE, Livello.ACCORDO, Livello.ENTE,
                                                  Livello.CONSERVATORE):
        classe = await carica_classe(session, attore, str(classe_grezza), ente_id)
        if livello is Livello.CLASSE and ente_id is None and classe.tenant_id:
            ente_id = classe.tenant_id
    ambito = Ambito(livello=livello, ente=ente_id,
                    classe=classe.id if classe else None,
                    classe_di_base=bool(classe and classe.di_base),
                    accordo=accordo if livello is Livello.ACCORDO else None)
    if livello is Livello.CONSERVATORE:
        ambito = replace(ambito, ente=None, classe=None, accordo=None, classe_di_base=False)
        classe = None
    motivo = motivo_blocco_ambito(attore, risorsa, ambito)
    if motivo == "ente non trovato":
        raise ErroreApi("CFG-404", "Ente non trovato")
    if motivo:
        codice = "CFG-400" if motivo.startswith(("indicare", "la risorsa", "il livello PDV")) \
            else "CFG-403"
        raise ErroreApi(codice, motivo)
    return ambito, classe


# ── Lettura delle impostazioni e delle regole ───────────────────────

def _voce_dict(voce: Impostazione, valore: Any, origine: Livello, attore: Attore,
               ambito: Ambito, catena: list[tuple[Livello, Any]]) -> dict[str, Any]:
    al_livello = next((v for liv, v in catena if liv is ambito.livello), ASSENTE)
    superiore = next(((liv, v) for liv, v in catena
                      if _ordine(liv) > _ordine(ambito.livello) and v is not ASSENTE), None)
    motivo = motivo_blocco_voce(attore, ambito, voce) \
        or motivo_blocco_ambito(attore, Risorsa.QUOTE if voce.gruppo == "quote"
                                else Risorsa.IMPOSTAZIONI, ambito)
    return {
        "codice": voce.codice, "valore": valore_per_fotografia(valore), "origine": origine.value,
        "tipo": voce.tipo.value, "locked": voce.locked, "norm_ref": voce.norm_ref,
        "descrizione": voce.descrizione,
        "ambito_ammesso": sorted(liv.value for liv in voce.livelli_ammessi()),
        "riservata_conservatore": voce.riservata_conservatore,
        "applicata_da": voce.applicata_da, "gruppo": voce.gruppo,
        # R07 (W1): verso più sicuro e, per una voce LIMITE.<codice>, la voce limitata
        "verso_sicuro": voce.verso_sicuro.value if voce.verso_sicuro else None,
        "limite_di": voce.limite_di,
        "scelte": list(voce.scelte) or None, "minimo": voce.minimo, "massimo": voce.massimo,
        "predefinito": valore_per_fotografia(voce.predefinito),
        "valore_al_livello": None if al_livello is ASSENTE else valore_per_fotografia(al_livello),
        "ereditato": ({"valore": valore_per_fotografia(superiore[1]),
                       "origine": superiore[0].value} if superiore else None),
        "azioni_consentite": {"modifica": motivo is None, "motivo_blocco": motivo},
    }


def _ordine(livello: Livello) -> int:
    return [Livello.PDV, Livello.ACCORDO, Livello.CLASSE, Livello.ENTE, Livello.CONSERVATORE,
            Livello.PREDEFINITO].index(livello)


def _pagina(voci: list[dict[str, Any]], chiave: str, limit: int,
            cursore: str | None) -> dict[str, Any]:
    """Elenco a cursore sul codice (cataloghi piccoli e ordinati)."""
    if cursore:
        voci = [v for v in voci if v[chiave] > cursore]
    pagina = voci[:limit]
    altri = len(voci) > limit
    return {"items": pagina, "limit": limit, "altri": altri,
            "cursore": pagina[-1][chiave] if altri and pagina else None}


async def _contesto_lettura(session: Any, attore: Attore, livello: str | None,
                            ente: str | None, classe: str | None, accordo: str | None,
                            ) -> tuple[Ambito, Contesto]:
    if ente is None and not attore.conservatore:
        ente = attore.tenant_id
    motivo = motivo_blocco_lettura(attore, _uuid(ente) if ente else None)
    if motivo == "ente non trovato":
        raise ErroreApi("CFG-404", "Ente non trovato")
    if motivo:
        raise ErroreApi("CFG-403", motivo)
    if livello is None:
        livello = ("ACCORDO" if accordo else "CLASSE" if classe else "ENTE" if ente
                   else "CONSERVATORE")
    try:
        liv = Livello(livello)
    except ValueError as exc:
        raise ErroreApi("CFG-400", "Livello non valido") from exc
    ente_id = await verifica_ente(session, attore, ente) if ente else None
    info = await carica_classe(session, attore, classe, ente_id) if classe else None
    if liv is Livello.CLASSE and info is None:
        raise ErroreApi("CFG-400", "indicare la classe documentale")
    if liv is Livello.ACCORDO and not accordo:
        raise ErroreApi("CFG-400", "indicare il codice dell'accordo di versamento")
    if liv in (Livello.ENTE, Livello.ACCORDO) and ente_id is None:
        raise ErroreApi("CFG-400", "indicare l'ente")
    ambito = Ambito(livello=liv, ente=ente_id, classe=info.id if info else None,
                    classe_di_base=bool(info and info.di_base), accordo=accordo)
    contesto = await leggi_contesto(session, ambito, info)
    if accordo and contesto.accordo_base is None:
        raise ErroreApi("CFG-404", f"Accordo {accordo!r} non trovato")
    return ambito, contesto


async def elenco_impostazioni(attore: Attore, *, livello: str | None, ente: str | None,
                              classe: str | None, accordo: str | None, gruppo: str | None,
                              limit: int, cursore: str | None, adesso: datetime
                              ) -> dict[str, Any]:
    """Impostazioni effettive nel contesto, con origine e azioni consentite."""
    try:
        async with get_async_session_lettura() as session:
            ambito, contesto = await _contesto_lettura(session, attore, livello, ente, classe,
                                                       accordo)
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_lettura", exc) from None
    registro = registro_console()
    livelli = dict(contesto.livelli)
    risolutore = Risolutore(registro, FornitoreDaDizionari(livelli))
    ctx = contesto.risoluzione(adesso)
    voci = []
    for voce in registro:
        if gruppo and voce.gruppo != gruppo:
            continue
        try:
            effettivo = risolutore.effettiva(ctx, voce.codice)
            catena = risolutore.catena(ctx, voce.codice)
        except ValueError as exc:
            logger.error("configurazione_valore_non_valido", codice=voce.codice,
                         errore=str(exc)[:200])
            raise ErroreApi("CFG-503", f"Valore salvato non valido per {voce.codice}") from exc
        voci.append(_voce_dict(voce, effettivo.valore, effettivo.origine, attore, ambito, catena))
    voci.sort(key=lambda v: v["codice"])
    risposta = _pagina(voci, "codice", limit, cursore)
    risposta["contesto"] = {**ambito.come_dict(),
                            "accordo_versione": contesto.accordo_base.versione
                            if contesto.accordo_base else None}
    return risposta


async def elenco_regole(attore: Attore, *, livello: str | None, ente: str | None,
                        classe: str | None, accordo: str | None, limit: int,
                        cursore: str | None, adesso: datetime) -> dict[str, Any]:
    """Catalogo delle regole con l'effetto effettivo nel contesto."""
    try:
        async with get_async_session_lettura() as session:
            ambito, contesto = await _contesto_lettura(session, attore, livello, ente, classe,
                                                       accordo)
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_lettura", exc) from None
    risolutore = Risolutore(REGISTRO, FornitoreDaDizionari(contesto.livelli))
    ctx = contesto.risoluzione(adesso)
    voci = []
    for regola in CATALOGO.regole():
        voce = REGISTRO[regola.codice_impostazione]
        effettivo = risolutore.effettiva(ctx, voce.codice)
        motivo = motivo_blocco_voce(attore, ambito, voce) \
            or motivo_blocco_ambito(attore, Risorsa.REGOLE, ambito)
        voci.append({
            "codice": regola.codice, "etichetta": regola.etichetta, "fase": regola.fase.value,
            "effetto": effettivo.valore, "origine": effettivo.origine.value,
            "effetto_predefinito": regola.effetto_predefinito.value, "locked": regola.locked,
            "norm_ref": regola.norm_ref, "impostazione": voce.codice,
            "ambito_ammesso": sorted(liv.value for liv in voce.livelli_ammessi()),
            "azioni_consentite": {"modifica": motivo is None, "motivo_blocco": motivo},
        })
    risposta = _pagina(sorted(voci, key=lambda v: v["codice"]), "codice", limit, cursore)
    risposta["contesto"] = ambito.come_dict()
    risposta["catalogo_regole"] = CATALOGO.versione
    return risposta


# ── Profili ─────────────────────────────────────────────────────────

def _profilo_breve(p: ProfiloMetadati) -> dict[str, Any]:
    return {"versione": p.versione, "valido_dal": p.valido_dal.isoformat(),
            "valido_al": p.valido_al.isoformat() if p.valido_al else None,
            "campi": len(p.campi), "obbligatori": sum(c.obbligatorio for c in p.campi),
            "da_riscontrare": sum(c.da_riscontrare for c in p.campi),
            "adozione": p.adozione}


def catalogo_formati(attore: Attore) -> dict[str, Any]:
    """R08 (D02): catalogo BASE dei formati (dati versionati del conservatore,
    Allegato 2) con PUID e MIME; gli esiti effettivi per livello sono le voci
    ``FORMATO.<ID>.ESITO`` di ``/impostazioni?gruppo=formati``."""
    from libs.verumdoc_rules.catalogo_formati import CATALOGO_FORMATI, ESITI, voci_da_mostrare

    motivo = motivo_blocco_lettura(attore, None)
    if motivo:
        raise ErroreApi("CFG-403", motivo)
    return {"versione": CATALOGO_FORMATI.versione, "valido_dal": CATALOGO_FORMATI.valido_dal,
            "norm_ref": CATALOGO_FORMATI.norm_ref, "esiti": list(ESITI),
            "formati": voci_da_mostrare()}


async def elenco_profili(attore: Attore, *, ente: str | None, limit: int,
                         cursore: str | None, adesso: datetime) -> dict[str, Any]:
    """Profili di tipologia (Allegato 5) e profili delle classi visibili."""
    giorno = giorno_locale(adesso)
    if ente is None and not attore.conservatore:
        ente = attore.tenant_id
    motivo = motivo_blocco_lettura(attore, _uuid(ente) if ente else None)
    if motivo:
        raise ErroreApi("CFG-404" if motivo == "ente non trovato" else "CFG-403", motivo)
    try:
        async with get_async_session_lettura() as session:
            db = profili_tipologia_da_righe(
                (await session.execute(text(SQL_PROFILI_TIPOLOGIA))).mappings().all())
            filtro = "c.tenant_id IS NULL" + (" OR c.tenant_id = CAST(:t AS uuid)" if ente else "")
            if attore.conservatore and ente is None:
                filtro = "TRUE"
            classi = (await session.execute(text(
                "SELECT c.id::text AS id, c.codice, c.nome, "  # noqa: S608 — frammenti fissi
                "c.tenant_id::text AS tenant_id, p.id::text AS pid, p.versione, p.valido_dal, "
                "p.valido_al, p.campi::text AS campi "
                "FROM public.profili_metadati p JOIN public.classi_documentali c "
                f"ON c.id = p.classe_id WHERE {filtro}"),  # noqa: S608 — frammenti fissi
                {"t": _uuid(ente)})).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_profili", exc) from None
    voci: list[dict[str, Any]] = []
    for codice, tip in sorted(tipologie().items()):
        versioni = versioni_tipologia(codice, db.get(codice, ()))
        # R11: «in vigore» = quella che vale per le classi senza versione scelta
        # (adozione automatica); «ultima» = la più alta valida, anche ad adozione esplicita
        in_vigore = next((p for p in reversed(versioni) if p.valido_il(giorno)
                          and p.adozione == ADOZIONE_AUTOMATICA), None)
        ultima = next((p for p in reversed(versioni) if p.valido_il(giorno)), None)
        voci.append({"id": f"tipologia:{codice}", "tipo": "tipologia", "codice": codice,
                     "nome": tip.nome, "estende": tip.estende, "nota": tip.nota,
                     "origine": tip.origine,
                     "norm_ref": versioni[-1].norm_ref, "versioni": len(versioni),
                     "in_vigore": _profilo_breve(in_vigore) if in_vigore else None,
                     "ultima": _profilo_breve(ultima) if ultima and ultima is not in_vigore
                     else None})
    per_classe: dict[str, list[Mapping[str, Any]]] = {}
    for r in classi:
        per_classe.setdefault(r["id"], []).append(r)
    for cid, righe in per_classe.items():
        versioni = profili_classe_da_righe(
            [{"id": r["pid"], "versione": r["versione"], "valido_dal": r["valido_dal"],
              "valido_al": r["valido_al"], "campi": r["campi"]} for r in righe],
            righe[0]["codice"])
        in_vigore = next((p for p in sorted(versioni, key=lambda p: -p.versione)
                          if p.valido_il(giorno)), None)
        voci.append({"id": f"classe:{cid}", "tipo": "classe", "codice": righe[0]["codice"],
                     "nome": righe[0]["nome"], "ente": righe[0]["tenant_id"],
                     "versioni": len(versioni),
                     "in_vigore": _profilo_breve(in_vigore) if in_vigore else None})
    return _pagina(sorted(voci, key=lambda v: v["id"]), "id", limit, cursore)


async def versioni_profilo(attore: Attore, profilo_id: str) -> dict[str, Any]:
    """Versioni di un profilo (``tipologia:<codice>`` o ``classe:<uuid>``)."""
    tipo, _, chiave = profilo_id.partition(":")
    try:
        async with get_async_session() as session:
            if tipo == "tipologia":
                if chiave not in tipologie():
                    raise ErroreApi("CFG-404", "Profilo non trovato")
                db = profili_tipologia_da_righe((await session.execute(text(
                    SQL_PROFILI_TIPOLOGIA + " WHERE codice = :k"), {"k": chiave})).mappings().all())
                versioni = versioni_tipologia(chiave, db.get(chiave, ()))
                origine = {p.versione: ("predefinito" if (p.id or "").startswith("tipologia:")
                                        else "conservatore") for p in versioni}
            elif tipo == "classe":
                motivo = motivo_blocco_lettura(attore, None)
                if motivo:
                    raise ErroreApi("CFG-403", motivo)
                info = await carica_classe(session, attore, chiave, None)
                versioni = profili_classe_da_righe((await session.execute(text(
                    "SELECT id::text AS id, versione, valido_dal, valido_al, "
                    "campi::text AS campi FROM public.profili_metadati "
                    "WHERE classe_id = CAST(:c AS uuid)"), {"c": info.id})).mappings().all(),
                    info.codice)
                origine = {p.versione: "classe" for p in versioni}
            else:
                raise ErroreApi("CFG-404", "Profilo non trovato")
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_profili", exc) from None
    return {"id": profilo_id,
            "items": [{**p.come_dict(), "origine": origine[p.versione]}
                      for p in sorted(versioni, key=lambda p: p.versione)]}


# ── Bozze ───────────────────────────────────────────────────────────

_COLONNE_BOZZA = (
    "SELECT id::text AS id, risorsa, livello, tenant_id::text AS tenant_id, "
    "classe_id::text AS classe_id, accordo_codice, modifiche::text AS modifiche, "
    "proposta::text AS proposta, motivo, versione, stato, creato_da::text AS creato_da, "
    "aggiornato_da::text AS aggiornato_da, created_at, updated_at, applicata_il, "
    "applicata_da::text AS applicata_da, impronta_piano, esito::text AS esito, "
    "valido_dal, programmata_il, programmata_da::text AS programmata_da, "
    "anteprima_programmata::text AS anteprima_programmata, anteprima_il, avviso_autore_il "
    "FROM public.configurazione_bozze ")


def _bozza_dict(r: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "bozza_id": r["id"], "risorsa": r["risorsa"],
        "ambito": {"livello": r["livello"], "ente": r["tenant_id"], "classe": r["classe_id"],
                   "accordo": r["accordo_codice"]},
        "modifiche": json_o_none(r["modifiche"]), "proposta": json_o_none(r["proposta"]),
        "motivo": r["motivo"], "versione": r["versione"], "stato": r["stato"],
        "creato_da": r["creato_da"], "aggiornato_da": r["aggiornato_da"],
        "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
        "applicata_il": r["applicata_il"].isoformat() if r["applicata_il"] else None,
        "applicata_da": r["applicata_da"], "impronta_piano": r["impronta_piano"],
        "esito": json_o_none(r["esito"]),
        # R07 (D28): modifica programmata
        "valido_dal": r["valido_dal"].isoformat() if r["valido_dal"] else None,
        "programmata_il": r["programmata_il"].isoformat() if r["programmata_il"] else None,
        "programmata_da": r["programmata_da"],
        "anteprima_programmata": json_o_none(r["anteprima_programmata"]),
        "anteprima_il": r["anteprima_il"].isoformat() if r["anteprima_il"] else None,
        "avviso_autore_il": (r["avviso_autore_il"].isoformat()
                             if r["avviso_autore_il"] else None),
    }


def _visibile(attore: Attore, riga: Mapping[str, Any]) -> bool:
    """Bozze: il conservatore vede tutto; l'amministratore dell'ente le sue."""
    if attore.conservatore:
        return True
    return attore.ruolo == "ADMIN_TENANT" and riga["tenant_id"] == attore.tenant_id


async def _leggi_bozza(session: Any, attore: Attore, bozza_id: str, *,
                       per_aggiornare: bool = False) -> Mapping[str, Any]:
    bid = _uuid(bozza_id)
    if bid is None:
        raise ErroreApi("CFG-404", "Bozza non trovata")
    sql = _COLONNE_BOZZA + "WHERE id = CAST(:b AS uuid)" + (" FOR UPDATE" if per_aggiornare
                                                            else "")
    riga = (await session.execute(text(sql), {"b": bid})).mappings().first()
    if riga is None or not _visibile(attore, riga):
        raise ErroreApi("CFG-404", "Bozza non trovata")
    return riga


def _audit(evento: str, attore: Attore, tenant_id: str | None, riepilogo: str,
           **dettagli: Any) -> tuple[str, dict[str, Any]]:
    return ("INSERT INTO public.audit_global (id, tenant_id, user_id, evento, dettagli) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), CAST(:u AS uuid), :e, "
            "CAST(:d AS jsonb))",
            {"t": tenant_id, "u": attore.user_id, "e": evento,
             "d": json.dumps({"riepilogo": riepilogo, "ruolo": attore.ruolo, **dettagli},
                             default=str)})


async def _scrivi_audit(session: Any, evento: str, attore: Attore, tenant_id: str | None,
                        riepilogo: str, **dettagli: Any) -> None:
    sql, parametri = _audit(evento, attore, tenant_id, riepilogo, **dettagli)
    await session.execute(text(sql), parametri)


def _proposta_valida(proposta: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if proposta is None:
        return None
    enti = proposta.get("enti")
    if enti is not None:
        if not isinstance(enti, list) or not all(_uuid(e) for e in enti) or len(enti) > 10_000:
            raise ErroreApi("CFG-400", "proposta.enti: elenco di identificativi di enti")
        return {"enti": sorted({_uuid(e) for e in enti})}  # type: ignore[type-var]
    return {"enti": None}


async def crea_bozza(attore: Attore, *, risorsa: Risorsa, ambito: Mapping[str, Any],
                     modifiche: list[dict[str, Any]], motivo: str,
                     proposta: Mapping[str, Any] | None) -> dict[str, Any]:
    proposta_norm = _proposta_valida(proposta)
    try:
        async with get_async_session() as session:
            amb, _ = await risolvi_ambito(session, attore, risorsa, ambito)
            if proposta_norm is not None and amb.livello is not Livello.CONSERVATORE:
                raise ErroreApi("CFG-400", "Le proposte agli enti partono dal livello "
                                           "CONSERVATORE")
            riga = (await session.execute(text(
                "INSERT INTO public.configurazione_bozze (risorsa, livello, tenant_id, classe_id, "
                "accordo_codice, modifiche, proposta, motivo, creato_da, aggiornato_da) VALUES "
                "(:r, :l, CAST(:t AS uuid), CAST(:c AS uuid), :a, CAST(:m AS jsonb), "
                "CAST(:p AS jsonb), :mot, CAST(:u AS uuid), CAST(:u AS uuid)) "
                "RETURNING id::text AS id, versione"),
                {"r": risorsa.value, "l": amb.livello.value, "t": amb.ente, "c": amb.classe,
                 "a": amb.accordo, "m": json.dumps(modifiche, default=str),
                 "p": json.dumps(proposta_norm) if proposta_norm is not None else None,
                 "mot": motivo, "u": attore.user_id})).mappings().one()
            await _scrivi_audit(session, "configurazione.bozza.creata", attore, amb.ente,
                                f"Bozza {risorsa.value} al livello {amb.livello.value}",
                                bozza_id=riga["id"], ambito=amb.come_dict(),
                                modifiche=len(modifiche), motivo=motivo)
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_bozza_creazione", exc) from None
    return {"bozza_id": riga["id"], "versione": riga["versione"], "stato": "APERTA"}


async def elenco_bozze(attore: Attore, *, stato: str | None, ente: str | None, limit: int,
                       cursore: str | None) -> dict[str, Any]:
    if not attore.conservatore and attore.ruolo != "ADMIN_TENANT":
        raise ErroreApi("CFG-403", f"il ruolo {attore.ruolo} non gestisce la configurazione")
    condizioni: list[str] = []
    parametri: dict[str, Any] = {"n": limit + 1}
    if not attore.conservatore:
        condizioni.append("tenant_id = CAST(:t AS uuid)")
        parametri["t"] = attore.tenant_id
    elif ente:
        condizioni.append("tenant_id = CAST(:t AS uuid)")
        parametri["t"] = _uuid(ente)
    if stato:
        if stato not in STATI_BOZZA:
            raise ErroreApi("CFG-400", "Stato non valido (" + ", ".join(STATI_BOZZA) + ")")
        condizioni.append("stato = :s")
        parametri["s"] = stato
    if cursore:
        quando, ultimo = decodifica_cursore("bozze", cursore)
        condizioni.append("(created_at, id) < (:q, CAST(:i AS uuid))")
        parametri.update(q=quando, i=ultimo)
    where = ("WHERE " + " AND ".join(condizioni)) if condizioni else ""
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                _COLONNE_BOZZA + where  # noqa: S608 — frammenti fissi, valori come parametri
                + " ORDER BY created_at DESC, id DESC LIMIT :n"),
                parametri)).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_bozze_elenco", exc) from None
    altri = len(righe) > limit
    righe = righe[:limit]
    return {"items": [_bozza_dict(r) for r in righe], "limit": limit, "altri": altri,
            "cursore": codifica_cursore("bozze", righe[-1]["created_at"], righe[-1]["id"])
            if altri else None}


async def leggi_bozza(attore: Attore, bozza_id: str) -> dict[str, Any]:
    try:
        async with get_async_session() as session:
            return _bozza_dict(await _leggi_bozza(session, attore, bozza_id))
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_bozza_lettura", exc) from None


def _controlla_versione(riga: Mapping[str, Any], versione: int) -> None:
    if riga["stato"] != "APERTA":
        raise ErroreApi("CFG-411", f"Bozza {riga['stato'].lower()}",
                        dettagli={"stato": riga["stato"]})
    if riga["versione"] != versione:
        raise ErroreApi("CFG-410", "La bozza è stata modificata da altri",
                        dettagli={"versione": riga["versione"]})


async def aggiorna_bozza(attore: Attore, bozza_id: str, *, versione: int,
                         modifiche: list[dict[str, Any]] | None, motivo: str | None,
                         proposta: Mapping[str, Any] | None) -> dict[str, Any]:
    try:
        async with get_async_session() as session:
            riga = await _leggi_bozza(session, attore, bozza_id, per_aggiornare=True)
            _controlla_versione(riga, versione)
            # chi modifica deve poter modificare l'ambito della bozza (stessa regola)
            await _ambito_della_bozza(session, attore, riga)
            nuovo = (await session.execute(text(
                "UPDATE public.configurazione_bozze SET "
                "modifiche = COALESCE(CAST(:m AS jsonb), modifiche), "
                "motivo = COALESCE(:mot, motivo), "
                "proposta = CASE WHEN :pp THEN CAST(:p AS jsonb) ELSE proposta END, "
                "versione = versione + 1, aggiornato_da = CAST(:u AS uuid), updated_at = now() "
                "WHERE id = CAST(:b AS uuid) RETURNING versione"),
                {"m": json.dumps(modifiche, default=str) if modifiche is not None else None,
                 "mot": motivo, "pp": proposta is not None,
                 "p": json.dumps(_proposta_valida(proposta)) if proposta is not None else None,
                 "u": attore.user_id, "b": riga["id"]})).scalar_one()
            await _scrivi_audit(session, "configurazione.bozza.modificata", attore,
                                riga["tenant_id"], f"Bozza {riga['id']} aggiornata",
                                bozza_id=riga["id"], versione=nuovo)
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_bozza_modifica", exc) from None
    return {"bozza_id": riga["id"], "versione": nuovo, "stato": "APERTA"}


async def annulla_bozza(attore: Attore, bozza_id: str, *, versione: int | None) -> None:
    try:
        async with get_async_session() as session:
            riga = await _leggi_bozza(session, attore, bozza_id, per_aggiornare=True)
            if riga["stato"] == "PROGRAMMATA":
                # R07 (D28): una modifica programmata si annulla fino alla decorrenza
                if versione is not None and riga["versione"] != versione:
                    raise ErroreApi("CFG-410", "La bozza è stata modificata da altri",
                                    dettagli={"versione": riga["versione"]})
            else:
                _controlla_versione(riga, riga["versione"] if versione is None else versione)
            ambito, _classe = await _ambito_della_bozza(session, attore, riga)
            await _controlla_annullabile(session, attore, riga, ambito)
            await session.execute(text(
                "UPDATE public.configurazione_bozze SET stato = 'ANNULLATA', "
                "versione = versione + 1, aggiornato_da = CAST(:u AS uuid), updated_at = now() "
                "WHERE id = CAST(:b AS uuid)"), {"u": attore.user_id, "b": riga["id"]})
            await _scrivi_audit(session, "configurazione.bozza.annullata", attore,
                                riga["tenant_id"], f"Bozza {riga['id']} annullata",
                                bozza_id=riga["id"])
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_bozza_annullamento", exc) from None


async def _controlla_annullabile(session: Any, attore: Attore, riga: Mapping[str, Any],
                                 ambito: Ambito) -> None:
    """R07, revisione W2: l'amministratore dell'ente non annulla (APERTA o
    PROGRAMMATA) una bozza del conservatore né una bozza con voci che lui non
    può modificare (riservate, locked, fuori ambito): prima, programmando, il
    conservatore perdeva il controllo che ha sull'applicazione immediata."""
    if attore.conservatore:
        return
    autori = sorted({a for a in (riga["creato_da"], riga["programmata_da"]) if a})
    if autori:
        ruoli = (await session.execute(text(
            "SELECT ruolo::text FROM public.users WHERE id = ANY(CAST(:u AS uuid[]))"),
            {"u": autori})).scalars().all()
        if any(r in RUOLI_CONSERVATORE for r in ruoli):
            raise ErroreApi("CFG-403", "la bozza è del conservatore: solo il conservatore "
                                       "può annullarla")
    if attore.user_id in autori:
        return  # la propria bozza (anche con voci che non potrà applicare)
    risorsa = Risorsa(riga["risorsa"])
    if risorsa is Risorsa.PROFILO:
        return
    valori, _campi, _errati = normalizza_modifiche(risorsa, json_o_none(riga["modifiche"]) or [])
    registro = registro_console()
    riservate = sorted({m.codice for m in valori if m.codice in registro
                        and motivo_blocco_voce(attore, ambito, registro[m.codice])})
    if riservate:
        raise ErroreApi("CFG-403", "la bozza contiene voci stabilite dal conservatore: "
                                   + ", ".join(riservate[:10]),
                        dettagli={"voci": riservate})


async def _ambito_della_bozza(session: Any, attore: Attore, riga: Mapping[str, Any]
                              ) -> tuple[Ambito, ClasseInfo | None]:
    return await risolvi_ambito(session, attore, Risorsa(riga["risorsa"]), {
        "livello": riga["livello"], "ente": riga["tenant_id"], "classe": riga["classe_id"],
        "accordo": riga["accordo_codice"]})


# ── Piano (anteprima e applicazione) ────────────────────────────────

@dataclass
class PianoCalcolato:
    piano: Piano
    contesto: Contesto
    risorsa: Risorsa
    impronta: str
    variante: Variante | None = None
    nuovo_profilo: ProfiloMetadati | None = None
    nuovo_accordo: AccordoVersamento | None = None
    proposta: dict[str, Any] | None = None

    def come_dict(self) -> dict[str, Any]:
        return {"impronta_piano": self.impronta,
                "modifiche": [r.come_dict() for r in self.piano.modifiche],
                "bloccati": self.piano.bloccati, "avvisi": self.piano.avvisi,
                "dettagli": self.piano.dettagli}


async def calcola_piano(session: Any, attore: Attore, riga: Mapping[str, Any],
                        adesso: datetime) -> PianoCalcolato:
    """Piano della bozza (funzione UNICA per anteprima e applicazione)."""
    risorsa = Risorsa(riga["risorsa"])
    ambito, classe = await _ambito_della_bozza(session, attore, riga)
    contesto = await leggi_contesto(session, ambito, classe)
    modifiche = json_o_none(riga["modifiche"]) or []
    proposta = json_o_none(riga["proposta"])
    giorno = giorno_locale(adesso)
    base = {"bozza": riga["id"], "versione": riga["versione"], "risorsa": risorsa.value,
            "ambito": ambito.come_dict(), "proposta": proposta}
    calcolato = PianoCalcolato(Piano(), contesto, risorsa, "", proposta=proposta)
    if risorsa is Risorsa.PROFILO:
        _piano_profilo(calcolato, modifiche, giorno)
    else:
        await _piano_valori(session, calcolato, attore, modifiche, adesso)
    calcolato.impronta = calcolato.piano.impronta(base)
    return calcolato


def _piano_profilo(calcolato: PianoCalcolato, modifiche: Sequence[Any], giorno: date) -> None:
    contesto, ambito = calcolato.contesto, calcolato.contesto.ambito
    piano = calcolato.piano
    if len(modifiche) != 1 or not isinstance(modifiche[0], Mapping):
        piano.bloccati.append({"codice": "profilo",
                               "motivo": "una sola modifica {tipologia?, campi, descrizione?}"})
        return
    voce = modifiche[0]
    if ambito.livello is Livello.CONSERVATORE:
        codice = str(voce.get("tipologia") or "")
        if codice not in tipologie():
            piano.bloccati.append({"codice": codice or "tipologia", "motivo": (
                "tipologia sconosciuta: le tipologie nuove si aggiungono con un file di dati "
                "del motore delle regole")})
            return
        try:
            versioni = versioni_tipologia(codice, contesto.profili_tipologia_db.get(codice, ()))
        except ProfiloNonValido as exc:
            piano.bloccati.append({"codice": codice, "motivo": str(exc)})
            return
        attuale = versioni[-1]
        calcolato.piano = piano = piano_profilo(attuale=attuale, campi_nuovi=voce.get("campi"),
                                                codice=f"tipologia:{codice}")
        nuova = attuale.versione + 1
        # R11: la versione nuova eredita l'adozione dell'ultima (una v2 esplicita non
        # diventa automatica copiandola), salvo scelta dichiarata nella bozza
        adozione = str(voce.get("adozione") or attuale.adozione)
        if adozione not in ADOZIONI:
            piano.bloccati.append({"codice": f"tipologia:{codice}.adozione",
                                   "motivo": f"adozione ammessa: {', '.join(ADOZIONI)}"})
        piano.dettagli["adozione"] = adozione
        if not piano.bloccati:
            calcolato.nuovo_profilo = ProfiloMetadati(
                codice=codice, versione=nuova, valido_dal=giorno,
                campi=tuple(CampoProfilo.da_dict(c) for c in piano.scritture["campi"]),
                tipologia=codice, norm_ref=attuale.norm_ref, adozione=adozione)
            calcolato.variante = Variante(profilo_tipologia=calcolato.nuovo_profilo)
    else:
        assert contesto.classe is not None  # garantito da risolvi_ambito
        versioni = sorted(contesto.profili_classe, key=lambda p: p.versione)
        attuale = versioni[-1] if versioni else None
        calcolato.piano = piano = piano_profilo(attuale=attuale, campi_nuovi=voce.get("campi"),
                                                codice=f"classe:{contesto.classe.codice}")
        # R11 (D07): la classe aggiunge solo campi facoltativi propri
        piano.bloccati += motivi_campi_classe(
            (CampoProfilo.da_dict(c) for c in piano.scritture.get("campi") or ()),
            _nomi_di_tipologia(contesto), f"classe:{contesto.classe.codice}")
        nuova = (attuale.versione if attuale else 0) + 1
        if not piano.bloccati:
            calcolato.nuovo_profilo = ProfiloMetadati(
                codice=contesto.classe.codice, versione=nuova, valido_dal=giorno,
                campi=tuple(CampoProfilo.da_dict(c) for c in piano.scritture["campi"]))
            calcolato.variante = Variante(classe=contesto.classe.id,
                                          ente=contesto.classe.tenant_id,
                                          profilo_classe=calcolato.nuovo_profilo)
    piano.dettagli["profilo"] = {"versione_nuova": nuova,
                                 "descrizione": voce.get("descrizione")}


def _nomi_di_tipologia(contesto: Contesto) -> frozenset[str]:
    """Nomi dei campi di tutte le versioni dei profili di tipologia (predefinite e del
    conservatore): una classe non li ridefinisce (R11, D07)."""
    nomi: set[str] = set()
    for codice in tipologie():
        for p in versioni_tipologia(codice, contesto.profili_tipologia_db.get(codice, ())):
            nomi |= p.nomi()
    return frozenset(nomi)


MOTIVO_PROFILO_ABBASSATO = (
    "toglie o abbassa il profilo dell'Allegato 5 (meno obblighi di quello in vigore): solo "
    "il conservatore, con motivo")


def _profilo_effettivo(livelli: Mapping[Livello, Mapping[str, Any]], registro: RegistroImpostazioni,
                       contesto: Contesto, adesso: datetime
                       ) -> tuple[str, int, ProfiloMetadati | None, date]:
    """(tipologia, versione, profilo, giorno) che il motore applicherebbe con questi
    livelli: stessa funzione del versamento (``tipologia_effettiva``). Il giorno è
    quello da cui vale la versione scelta, se futura (modifica da programmare)."""
    giorno = giorno_locale(adesso)
    db = contesto.profili_tipologia_db
    versione = int(Risolutore(registro, FornitoreDaDizionari(livelli)).effettiva(
        contesto.risoluzione(adesso), PROFILO_TIPOLOGIA_VERSIONE).valore or 0)
    base = tipologia_effettiva(registro=registro, livelli=livelli, giorno=giorno,
                               profili_tipologia_db=db)
    codice = base.codice if base is not None else ""
    if versione and codice:
        scelta = next((p for p in versioni_tipologia(codice, db.get(codice, ()))
                       if p.versione == versione), None)
        if scelta is not None and scelta.valido_dal > giorno:
            giorno = scelta.valido_dal
    return codice, versione, tipologia_effettiva(
        registro=registro, livelli=livelli, giorno=giorno, profili_tipologia_db=db,
        versione=versione), giorno


async def _controlla_profilo_tipologia(session: Any, calcolato: PianoCalcolato,
                                       registro: RegistroImpostazioni, attore: Attore,
                                       adesso: datetime) -> None:
    """R11 e revisione T1 (VD-P-114). Quando la bozza tocca ``PROFILO_TIPOLOGIA`` o
    ``PROFILO_TIPOLOGIA_VERSIONE`` (a qualunque livello, anche programmata: il piano
    si ricalcola all'esecuzione con lo stesso autore):
      - la versione scelta deve esistere per la tipologia risultante;
      - chi non è il conservatore non toglie né abbassa il profilo: il profilo dopo la
        modifica deve chiedere almeno gli obblighi di quello di prima (``almeno_severo``),
        per l'ambito e, a livello ente, per ogni classe dell'ente con una versione scelta."""
    piano, contesto = calcolato.piano, calcolato.contesto
    if not {PROFILO_TIPOLOGIA, PROFILO_TIPOLOGIA_VERSIONE} & set(piano.scritture):
        return
    coppie: list[tuple[str, dict[Livello, dict[str, Any]], dict[Livello, dict[str, Any]]]] = [
        ("", {k: dict(v) for k, v in contesto.livelli.items()},
         {k: dict(v) for k, v in piano.livelli_dopo.items()})]
    if contesto.ambito.livello is not Livello.CLASSE and contesto.ambito.ente:
        # classi dell'ente con una versione o una tipologia propria: ereditano il cambio
        righe = (await session.execute(text(
            "SELECT c.codice, v.codice AS voce, v.valore::text AS valore "
            "FROM public.impostazioni_valori v JOIN public.classi_documentali c "
            "ON c.id = v.classe_id WHERE v.livello = 'CLASSE' AND c.tenant_id = CAST(:t AS uuid) "
            "AND v.codice IN ('PROFILO_TIPOLOGIA', 'PROFILO_TIPOLOGIA_VERSIONE')"),
            {"t": contesto.ambito.ente})).mappings().all()
        per_classe: dict[str, dict[str, Any]] = {}
        for r in righe:
            per_classe.setdefault(r["codice"], {})[r["voce"]] = json_o_none(r["valore"])
        for codice_classe, valori in sorted(per_classe.items()):
            coppie.append((f" (classe {codice_classe})",
                           {**coppie[0][1], Livello.CLASSE: valori},
                           {**coppie[0][2], Livello.CLASSE: valori}))
    for etichetta, prima, dopo in coppie:
        try:
            _, _, profilo_prima, _ = _profilo_effettivo(prima, registro, contesto, adesso)
        except (ProfiloNonValido, ValueError):
            profilo_prima = None                     # configurazione di prima già rotta
        try:
            codice, versione, profilo_dopo, giorno = _profilo_effettivo(
                dopo, registro, contesto, adesso)
        except (ProfiloNonValido, ValueError) as exc:
            piano.bloccati.append({"codice": PROFILO_TIPOLOGIA_VERSIONE,
                                   "motivo": f"{exc}{etichetta}"})
            continue
        if versione:
            blocco, avviso = esito_versione_tipologia(
                codice, versione, versioni_tipologia(
                    codice, contesto.profili_tipologia_db.get(codice, ())), giorno_locale(adesso))
            if blocco:
                piano.bloccati.append({"codice": PROFILO_TIPOLOGIA_VERSIONE,
                                       "motivo": blocco + etichetta})
            if avviso:
                piano.avvisi.append(avviso + etichetta)
            if not etichetta:
                piano.dettagli["profilo_tipologia"] = {"codice": codice, "versione": versione}
        if not almeno_severo(profilo_dopo, profilo_prima):
            if attore.conservatore:
                piano.avvisi.append("la modifica riduce gli obblighi del profilo dell'Allegato 5"
                                    + etichetta)
            else:
                piano.bloccati.append({"codice": PROFILO_TIPOLOGIA,
                                       "motivo": MOTIVO_PROFILO_ABBASSATO + etichetta})


async def _piano_valori(session: Any, calcolato: PianoCalcolato, attore: Attore,
                        modifiche: Sequence[Any], adesso: datetime) -> None:
    contesto, ambito, risorsa = calcolato.contesto, calcolato.contesto.ambito, calcolato.risorsa
    registro = registro_console()
    valori, campi_accordo, bloccati = normalizza_modifiche(
        risorsa, [m for m in modifiche if isinstance(m, Mapping)])
    if len(valori) + len(campi_accordo) + len(bloccati) != len(modifiche):
        bloccati.append({"codice": "?", "motivo": "ogni modifica deve essere un oggetto"})
    accordo_livello = ambito.livello is Livello.ACCORDO
    # le liste in colonna della classe: vuota = eredita (§8.14), il numero non si eredita
    if ambito.livello is Livello.CLASSE:
        valori = [ModificaValore(m.codice, None, True)
                  if m.codice in CODICI_IN_COLONNA_CLASSE and not m.eredita
                  and isinstance(m.valore, list) and not m.valore else m for m in valori]
    for m in valori:
        if m.codice in registro:
            voce = registro[m.codice]
            if (voce.gruppo == "quote") != (risorsa is Risorsa.QUOTE):
                bloccati.append({"codice": m.codice, "motivo": (
                    "le quote si modificano con la risorsa «quote»" if voce.gruppo == "quote"
                    else "la risorsa «quote» modifica solo le quote")})
    esclusi = {b["codice"] for b in bloccati}
    piano = piano_valori(
        registro=registro, attore=attore, ambito=ambito,
        modifiche=[m for m in valori if m.codice not in esclusi], livelli=contesto.livelli,
        contesto=contesto.risoluzione(adesso),
        codici_senza_eredita=frozenset({CLASSE_DIMENSIONE_MAX_MB})
        if ambito.livello is Livello.CLASSE else frozenset())
    piano.bloccati[:0] = bloccati
    calcolato.piano = piano
    if not valori and not campi_accordo:
        piano.bloccati.append({"codice": "modifiche", "motivo": "nessuna modifica nella bozza"})
    if calcolato.proposta is not None:
        for codice in list(piano.scritture):
            voce = registro[codice]
            if voce.locked or piano.scritture[codice] is ASSENTE:
                piano.bloccati.append({"codice": codice, "motivo": (
                    "un valore imposto dalla norma si applica, non si propone" if voce.locked
                    else "una proposta indica un valore")})
            elif Livello.ENTE not in voce.livelli_ammessi():
                piano.bloccati.append({"codice": codice,
                                       "motivo": "non personalizzabile dagli enti"})
        enti = calcolato.proposta.get("enti")
        sql = "SELECT count(*) FROM public.tenants WHERE stato = 'ATTIVO'" + (  # noqa: S608
            " AND id = ANY(CAST(:e AS uuid[]))" if enti is not None else "")
        destinatari = (await session.execute(text(sql), {"e": enti})).scalar_one()
        piano.dettagli["proposta"] = {"enti_destinatari": int(destinatari)}
        piano.avvisi.append("Proposta agli enti: il valore del conservatore non cambia; ogni "
                            "ente lo accetta o lo rifiuta con motivo")
    await _controlla_profilo_tipologia(session, calcolato, registro, attore, adesso)
    if accordo_livello:
        _dettagli_accordo(calcolato, campi_accordo, adesso)
    _variante_valori(calcolato)


def _dettagli_accordo(calcolato: PianoCalcolato, campi: Mapping[str, Any],
                      adesso: datetime) -> None:
    contesto, ambito, piano = calcolato.contesto, calcolato.contesto.ambito, calcolato.piano
    base = contesto.accordo_base
    classe_id = base.classe_id if base else (contesto.classe.id if contesto.classe else None)
    if base and contesto.classe and base.classe_id != contesto.classe.id:
        piano.bloccati.append({"codice": "accordo", "motivo": (
            "la classe di un accordo non cambia: creare un accordo nuovo")})
    cessato = bool(campi.get("cessato", False))
    if "cessato" in campi and not isinstance(campi["cessato"], bool):
        piano.bloccati.append({"codice": "cessato", "motivo": "atteso true o false"})
    descrizione = campi.get("descrizione", base.descrizione if base else "")
    if not isinstance(descrizione, str) or len(descrizione) > 2000:
        piano.bloccati.append({"codice": "descrizione", "motivo": "testo fino a 2000 caratteri"})
        descrizione = ""
    if base is None and cessato:
        piano.bloccati.append({"codice": "cessato", "motivo": "l'accordo non esiste ancora"})
    # due accordi in vigore (o futuri) per lo stesso ambito: la scelta sarebbe ambigua
    giorno = giorno_locale(adesso)
    altri = {c: a for c, a in vigenti_per_codice(contesto.accordi, date.max).items()
             if c != ambito.accordo and a.classe_id == classe_id
             and (a.valido_dal >= giorno or vigenti_per_codice(contesto.accordi, giorno).get(c))}
    if altri and not cessato:
        piano.bloccati.append({"codice": "accordo", "motivo": (
            "per lo stesso ambito (classe o tutte le classi) c'è già l'accordo "
            + ", ".join(sorted(altri)) + ": la scelta al versamento sarebbe ambigua")})
    valori = dict(base.valori) if base else {}
    for codice, valore in calcolato.piano.scritture.items():
        if valore is ASSENTE:
            valori.pop(codice, None)
        else:
            valori[codice] = valore
    calcolato.nuovo_accordo = AccordoVersamento(
        tenant_id=str(ambito.ente), codice=str(ambito.accordo),
        versione=(base.versione if base else 0) + 1, valido_dal=giorno, classe_id=classe_id,
        valori=valori, cessato=cessato, descrizione=str(descrizione))
    piano.dettagli["accordo"] = {
        "codice": ambito.accordo, "versione_nuova": calcolato.nuovo_accordo.versione,
        "classe": classe_id, "cessato": cessato, "descrizione": descrizione,
        "valori": {k: valori[k] for k in sorted(valori)}}


def _variante_valori(calcolato: PianoCalcolato) -> None:
    """Variante per l'impatto: solo i codici che il versamento usa (REGISTRO)."""
    ambito, piano = calcolato.contesto.ambito, calcolato.piano
    if calcolato.nuovo_accordo is not None:
        calcolato.variante = Variante(ente=ambito.ente, classe=calcolato.nuovo_accordo.classe_id,
                                      accordo=calcolato.nuovo_accordo)
        return
    scritture = {k: v for k, v in piano.scritture.items() if k in REGISTRO}
    if not scritture:
        return
    classe = calcolato.contesto.classe
    livello = Livello.ENTE if calcolato.proposta is not None else ambito.livello
    calcolato.variante = Variante(
        livello=livello, ente=ambito.ente,
        classe=classe.id if classe and ambito.livello is Livello.CLASSE else None,
        valori={k: v for k, v in scritture.items() if v is not ASSENTE},
        togli=frozenset(k for k, v in scritture.items() if v is ASSENTE))


async def _impatto(session: Any, calcolato: PianoCalcolato, adesso: datetime
                   ) -> dict[str, Any]:
    """Impatto sul campione dichiarato dei PdV recenti (stesso codice di confronta_regole)."""
    variante = calcolato.variante
    giorni = impostazioni.configurazione_anteprima_finestra_giorni
    vuoto = {"pdv_ultimi_30_giorni_che_sarebbero_rifiutati": 0,
             "pdv_ultimi_30_giorni_con_nuovi_avvisi": 0, "esempi": []}
    if variante is None:
        return {**vuoto, "campione": None,
                "nota": "la modifica non riguarda le regole del versamento"}
    ambito = calcolato.contesto.ambito
    ente = variante.ente if ambito.livello is not Livello.CONSERVATORE else None
    if calcolato.proposta is not None:
        ente = None

    async def esegui(sql: str, parametri: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        return (await session.execute(text(sql), dict(parametri))).mappings().all()

    campione = await carica_campione(
        esegui, registro=REGISTRO, adesso=adesso, giorni=giorni,
        limite=impostazioni.configurazione_anteprima_campione_max, ente=ente,
        classe=variante.classe, campi_noti=CAMPI_METADATI_INDICE,
        conservatore_ambiente=valori_conservatore_da_ambiente())
    scadenza = time.monotonic() + impostazioni.configurazione_anteprima_tempo_max_s
    esito = confronta(
        campione.pdv, catalogo=CATALOGO,
        ambiente_prima=lambda p: campione.contesti.ambiente(p),  # type: ignore[arg-type]
        ambiente_dopo=lambda p: campione.contesti.ambiente(p, variante),  # type: ignore[arg-type]
        max_esempi=impostazioni.configurazione_anteprima_esempi,
        tempo_scaduto=lambda: time.monotonic() > scadenza)
    return {
        "pdv_ultimi_30_giorni_che_sarebbero_rifiutati": esito.rifiutati_dopo,
        "pdv_ultimi_30_giorni_con_nuovi_avvisi": esito.con_nuovi_avvisi,
        "esempi": esito.esempi,
        "rifiutati_con_la_configurazione_attuale": esito.rifiutati_prima,
        "non_valutabili": esito.non_valutabili,
        "campione": {**campione.dichiarazione(), "analizzati": esito.analizzati,
                     "tempo_max_s": impostazioni.configurazione_anteprima_tempo_max_s,
                     "troncato_per_tempo": esito.troncato_per_tempo,
                     "nota": ("se tutti gli enti accettassero la proposta"
                              if calcolato.proposta is not None else None)},
    }


async def anteprima(attore: Attore, bozza_id: str, adesso: datetime) -> dict[str, Any]:
    """Piano e impatto della bozza. NON scrive nulla: sessione di sola lettura
    (``get_async_session_lettura``: replica se configurata, altrimenti transazione
    READ ONLY sul primario; il database rifiuta ogni scrittura) con
    statement_timeout."""
    try:
        async with get_async_session_lettura() as session:
            await session.execute(text(
                f"SET LOCAL statement_timeout = "
                f"{int(impostazioni.configurazione_anteprima_query_ms)}"))
            riga = await _leggi_bozza(session, attore, bozza_id)
            if riga["stato"] != "APERTA":
                raise ErroreApi("CFG-411", f"Bozza {riga['stato'].lower()}",
                                dettagli={"stato": riga["stato"]})
            calcolato = await calcola_piano(session, attore, riga, adesso)
            impatto = await _impatto(session, calcolato, adesso)
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_anteprima", exc) from None
    return {**calcolato.come_dict(), "bozza_id": riga["id"], "versione": riga["versione"],
            "impatto": impatto}


# ── Applicazione ────────────────────────────────────────────────────

def _decorrenza(calcolato: PianoCalcolato, valido_dal: date | None, adesso: datetime) -> date:
    """Decorrenza ammessa per la risorsa (CFG-423 se no).

    R07 (D28): per le impostazioni di conservatore, ente e classe una data futura
    vuol dire «programmare» la bozza (``applica_bozza`` la mette PROGRAMMATA);
    le proposte valgono sempre dall'applicazione."""
    oggi = giorno_locale(adesso)
    versionata = calcolato.nuovo_accordo is not None or calcolato.nuovo_profilo is not None
    if not versionata:
        if valido_dal is None or valido_dal == oggi:
            return oggi
        if calcolato.proposta is not None:
            raise ErroreApi("CFG-423", "Le proposte valgono dall'applicazione: omettere "
                                       "valido_dal")
        if valido_dal < oggi:
            raise ErroreApi("CFG-423", "La decorrenza non può essere nel passato")
        if valido_dal > oggi + timedelta(days=PROGRAMMATA_MASSIMO_GIORNI):
            # revisione R07 (W4, nota): una programmazione oltre un anno non si accetta
            raise ErroreApi("CFG-423", f"La decorrenza di una modifica programmata è al più "
                                       f"fra {PROGRAMMATA_MASSIMO_GIORNI} giorni")
        return valido_dal
    decorrenza = valido_dal or oggi
    if decorrenza < oggi:
        raise ErroreApi("CFG-423", "La decorrenza non può essere nel passato")
    base = calcolato.contesto.accordo_base
    if calcolato.nuovo_accordo is not None and base and decorrenza < base.valido_dal:
        raise ErroreApi("CFG-423", "La decorrenza precede quella della versione precedente "
                                   f"({base.valido_dal.isoformat()})")
    return decorrenza


async def _scrivi_valori(session: Any, calcolato: PianoCalcolato, attore: Attore,
                         motivo: str, bozza_id: str | None, proposta_id: str | None = None,
                         livello: Livello | None = None, ente: str | None = None) -> int:
    """Righe correnti + storico append-only; colonne per le voci in colonna della classe."""
    ambito = calcolato.contesto.ambito
    livello = livello or ambito.livello
    classe = calcolato.contesto.classe if livello is Livello.CLASSE else None
    tenant = ente if ente is not None else (
        (classe.tenant_id if classe else None) if livello is Livello.CLASSE
        else ambito.ente if livello is Livello.ENTE else None)
    scritte = 0
    for codice, valore in sorted(calcolato.piano.scritture.items()):
        if classe is not None and codice in CODICI_IN_COLONNA_CLASSE:
            colonna = CODICI_IN_COLONNA_CLASSE[codice]
            nuovo = [] if valore is ASSENTE else valore
            prima = (await session.execute(text(
                f"SELECT {colonna}::text FROM public.classi_documentali "  # noqa: S608
                "WHERE id = CAST(:c AS uuid) FOR UPDATE"), {"c": classe.id})).scalar_one()
            await session.execute(text(
                f"UPDATE public.classi_documentali SET {colonna} = "  # noqa: S608 — colonna fissa
                + ("CAST(:v AS jsonb)" if colonna != "max_dimensione_mb" else ":v")
                + ", updated_at = now() WHERE id = CAST(:c AS uuid)"),
                {"v": json.dumps(nuovo) if colonna != "max_dimensione_mb" else nuovo,
                 "c": classe.id})
            prima_valore = json_o_none(prima)
        else:
            chiave = {"l": livello.value, "t": tenant, "c": classe.id if classe else None,
                      "k": codice}
            filtro = ("livello = :l AND tenant_id IS NOT DISTINCT FROM CAST(:t AS uuid) AND "
                      "classe_id IS NOT DISTINCT FROM CAST(:c AS uuid) AND codice = :k")
            prima_valore = json_o_none((await session.execute(text(
                f"SELECT valore::text FROM public.impostazioni_valori WHERE {filtro} "  # noqa: S608
                "FOR UPDATE"), chiave)).scalar())
            if valore is ASSENTE:
                await session.execute(text(
                    f"DELETE FROM public.impostazioni_valori WHERE {filtro}"), chiave)  # noqa: S608
            else:
                await session.execute(text(
                    "INSERT INTO public.impostazioni_valori (livello, tenant_id, classe_id, "
                    "codice, valore, motivo, creato_da) VALUES (:l, CAST(:t AS uuid), "
                    "CAST(:c AS uuid), :k, CAST(:v AS jsonb), :m, CAST(:u AS uuid)) "
                    "ON CONFLICT ON CONSTRAINT uq_impostazioni_valori_livello DO UPDATE SET "
                    "valore = EXCLUDED.valore, motivo = EXCLUDED.motivo, "
                    "creato_da = EXCLUDED.creato_da, created_at = now()"),
                    {**chiave, "v": json.dumps(valore), "m": motivo, "u": attore.user_id})
        await session.execute(text(
            "INSERT INTO public.impostazioni_storico (livello, tenant_id, classe_id, codice, "
            "valore_prima, valore_dopo, motivo, bozza_id, proposta_id, creato_da) VALUES "
            "(:l, CAST(:t AS uuid), CAST(:c AS uuid), :k, CAST(:p AS jsonb), CAST(:d AS jsonb), "
            ":m, CAST(:b AS uuid), CAST(:pr AS uuid), CAST(:u AS uuid))"),
            {"l": livello.value, "t": tenant, "c": classe.id if classe else None, "k": codice,
             "p": json.dumps(prima_valore) if prima_valore is not None else None,
             "d": None if valore is ASSENTE else json.dumps(valore), "m": motivo,
             "b": bozza_id, "pr": proposta_id, "u": attore.user_id})
        scritte += 1
    return scritte


async def applica_bozza(attore: Attore, bozza_id: str, *, impronta_piano: str,
                        valido_dal: date | None, adesso: datetime) -> dict[str, Any]:
    """Applica la bozza in UNA transazione: lock della bozza, lock dell'ambito,
    ricalcolo del piano, confronto dell'impronta (409 CFG-409 col piano nuovo),
    scritture, stato APPLICATA e una voce d'audit con i conteggi (§13.1)."""
    try:
        async with get_async_session() as session:
            riga = await _leggi_bozza(session, attore, bozza_id, per_aggiornare=True)
            if riga["stato"] != "APERTA":
                raise ErroreApi("CFG-411", f"Bozza {riga['stato'].lower()}",
                                dettagli={"stato": riga["stato"]})
            # lock dell'ambito (stesso ordine per tutti: bozza → ambito, §12.7)
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                                  {"k": "configurazione:" + "|".join(
                                      str(riga[c] or "") for c in ("livello", "tenant_id",
                                                                   "classe_id",
                                                                   "accordo_codice"))})
            calcolato = await calcola_piano(session, attore, riga, adesso)
            if calcolato.impronta != impronta_piano:
                raise ErroreApi("CFG-409", "Il piano è cambiato dall'anteprima: nulla è stato "
                                           "scritto", dettagli={"motivo": "PIANO_CAMBIATO",
                                                                "piano": calcolato.come_dict()})
            if calcolato.piano.bloccati:
                raise ErroreApi("CFG-422", "Il piano ha elementi bloccati",
                                dettagli={"bloccati": calcolato.piano.bloccati})
            decorrenza = _decorrenza(calcolato, valido_dal, adesso)
            _versione_valida_alla_decorrenza(calcolato, decorrenza)
            versionata = (calcolato.nuovo_accordo is not None
                          or calcolato.nuovo_profilo is not None)
            if not versionata and decorrenza > giorno_locale(adesso):
                return await _programma(session, calcolato, attore, riga, decorrenza)
            esito = await _esegui_piano(session, calcolato, attore, riga, decorrenza)
            await session.execute(text(
                "UPDATE public.configurazione_bozze SET stato = 'APPLICATA', "
                "versione = versione + 1, applicata_il = now(), applicata_da = CAST(:u AS uuid), "
                "aggiornato_da = CAST(:u AS uuid), updated_at = now(), impronta_piano = :i, "
                "esito = CAST(:e AS jsonb) WHERE id = CAST(:b AS uuid)"),
                {"u": attore.user_id, "i": calcolato.impronta, "e": json.dumps(esito, default=str),
                 "b": riga["id"]})
            await _scrivi_audit(
                session, "configurazione.bozza.applicata", attore, riga["tenant_id"],
                f"Configurazione {riga['risorsa']} applicata al livello {riga['livello']}: "
                f"{len(calcolato.piano.modifiche)} modifiche",
                bozza_id=riga["id"], impronta_piano=calcolato.impronta, esito=esito,
                modifiche=[r.come_dict() for r in calcolato.piano.modifiche],
                motivo=riga["motivo"])
    except IntegrityError as exc:
        logger.warning("configurazione_conflitto", errore=str(exc)[:200])
        raise ErroreApi("CFG-409", "Un'altra modifica è stata applicata nello stesso momento",
                        dettagli={"motivo": "PIANO_CAMBIATO"}) from None
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_applicazione", exc) from None
    return {"bozza_id": riga["id"], "stato": "APPLICATA", "impronta_piano": calcolato.impronta,
            "esito": esito}


def _versione_valida_alla_decorrenza(calcolato: PianoCalcolato, decorrenza: date) -> None:
    """R11: la versione del profilo di tipologia scelta per la classe deve valere dalla
    decorrenza (altrimenti ogni versamento della classe fallirebbe): CFG-423, con
    l'indicazione di programmare la modifica."""
    scelta = calcolato.piano.dettagli.get("profilo_tipologia")
    if not scelta:
        return
    versioni = versioni_tipologia(scelta["codice"], calcolato.contesto.profili_tipologia_db.get(
        scelta["codice"], ()))
    profilo = next((p for p in versioni if p.versione == scelta["versione"]), None)
    if profilo is not None and not profilo.valido_il(decorrenza):
        raise ErroreApi("CFG-423", f"La versione {profilo.versione} del profilo "
                                   f"{profilo.codice} vale dal {profilo.valido_dal.isoformat()}: "
                                   "programmare la modifica da quella data")


async def _programma(session: Any, calcolato: PianoCalcolato, attore: Attore,
                     riga: Mapping[str, Any], decorrenza: date) -> dict[str, Any]:
    """R07 (D28): la bozza diventa PROGRAMMATA per ``decorrenza``. Il giorno prima
    il lavoro periodico (``app.services.programmate``) ricalcola piano e impatto e
    avvisa l'autore; alla decorrenza la applica con lo stesso codice."""
    await session.execute(text(
        "UPDATE public.configurazione_bozze SET stato = 'PROGRAMMATA', valido_dal = :d, "
        "programmata_il = now(), programmata_da = CAST(:u AS uuid), impronta_piano = :i, "
        "anteprima_programmata = NULL, anteprima_il = NULL, avviso_autore_il = NULL, "
        "versione = versione + 1, aggiornato_da = CAST(:u AS uuid), updated_at = now() "
        "WHERE id = CAST(:b AS uuid)"),
        {"d": decorrenza, "u": attore.user_id, "i": calcolato.impronta, "b": riga["id"]})
    await _scrivi_audit(
        session, "configurazione.bozza.programmata", attore, riga["tenant_id"],
        f"Configurazione {riga['risorsa']} programmata al livello {riga['livello']} dal "
        f"{decorrenza.isoformat()}: {len(calcolato.piano.modifiche)} modifiche",
        bozza_id=riga["id"], impronta_piano=calcolato.impronta,
        valido_dal=decorrenza.isoformat(),
        modifiche=[r.come_dict() for r in calcolato.piano.modifiche], motivo=riga["motivo"])
    return {"bozza_id": riga["id"], "stato": "PROGRAMMATA", "impronta_piano": calcolato.impronta,
            "valido_dal": decorrenza.isoformat(), "esito": None}


async def _esegui_piano(session: Any, calcolato: PianoCalcolato, attore: Attore,
                        riga: Mapping[str, Any], decorrenza: date) -> dict[str, Any]:
    motivo = str(riga["motivo"])
    if calcolato.nuovo_profilo is not None:
        p = calcolato.nuovo_profilo
        campi = json.dumps([c.come_dict() for c in p.campi])
        if p.tipologia:
            nuovo = (await session.execute(text(
                "INSERT INTO public.profili_tipologia (codice, versione, valido_dal, campi, "
                "descrizione, norm_ref, motivo, bozza_id, creato_da, adozione) VALUES (:k, :v, "
                ":d, CAST(:c AS jsonb), :ds, :n, :m, CAST(:b AS uuid), CAST(:u AS uuid), :a) "
                "RETURNING id::text"),
                {"k": p.codice, "v": p.versione, "d": decorrenza, "c": campi, "a": p.adozione,
                 "ds": calcolato.piano.dettagli["profilo"].get("descrizione"),
                 "n": p.norm_ref, "m": motivo, "b": riga["id"], "u": attore.user_id})
                ).scalar_one()
        else:
            classe = calcolato.contesto.classe
            assert classe is not None
            nuovo = (await session.execute(text(
                "INSERT INTO public.profili_metadati (classe_id, versione, valido_dal, campi, "
                "descrizione, creato_da) VALUES (CAST(:c AS uuid), :v, :d, CAST(:k AS jsonb), "
                ":ds, CAST(:u AS uuid)) RETURNING id::text"),
                {"c": classe.id, "v": p.versione, "d": decorrenza, "k": campi,
                 "ds": calcolato.piano.dettagli["profilo"].get("descrizione") or motivo,
                 "u": attore.user_id})).scalar_one()
        return {"profilo": {"id": nuovo, "versione": p.versione,
                            "valido_dal": decorrenza.isoformat()}}
    if calcolato.nuovo_accordo is not None:
        a = calcolato.nuovo_accordo
        nuovo = (await session.execute(text(
            "INSERT INTO public.accordi_versamento (tenant_id, codice, versione, classe_id, "
            "descrizione, valido_dal, cessato, valori, motivo, bozza_id, creato_da) VALUES "
            "(CAST(:t AS uuid), :k, :v, CAST(:c AS uuid), :ds, :d, :x, CAST(:val AS jsonb), :m, "
            "CAST(:b AS uuid), CAST(:u AS uuid)) RETURNING id::text"),
            {"t": a.tenant_id, "k": a.codice, "v": a.versione, "c": a.classe_id,
             "ds": a.descrizione, "d": decorrenza, "x": a.cessato,
             "val": json.dumps(dict(a.valori)), "m": motivo, "b": riga["id"],
             "u": attore.user_id})).scalar_one()
        return {"accordo": {"id": nuovo, "codice": a.codice, "versione": a.versione,
                            "valido_dal": decorrenza.isoformat(), "cessato": a.cessato}}
    if calcolato.proposta is not None:
        enti = calcolato.proposta.get("enti")
        totale = 0
        for codice, valore in sorted(calcolato.piano.scritture.items()):
            esito = await session.execute(text(
                "INSERT INTO public.configurazione_proposte (bozza_id, tenant_id, codice, valore, "
                "motivo) SELECT CAST(:b AS uuid), t.id, :k, CAST(:v AS jsonb), :m "
                "FROM public.tenants t WHERE t.stato = 'ATTIVO'"
                + (" AND t.id = ANY(CAST(:e AS uuid[]))" if enti is not None else "")),
                {"b": riga["id"], "k": codice, "v": json.dumps(valore), "m": motivo, "e": enti})
            totale += esito.rowcount or 0
        return {"proposte": totale}
    scritte = await _scrivi_valori(session, calcolato, attore, motivo, riga["id"])
    return {"valori_scritti": scritte}


# ── Proposte (REGOLE §8.13) ─────────────────────────────────────────

_COLONNE_PROPOSTA = (
    "SELECT p.id::text AS id, p.bozza_id::text AS bozza_id, p.tenant_id::text AS tenant_id, "
    "p.codice, p.valore::text AS valore, p.motivo, p.stato, p.motivo_risposta, "
    "p.risposto_da::text AS risposto_da, p.risposto_il, p.created_at "
    "FROM public.configurazione_proposte p ")


def _proposta_dict(r: Mapping[str, Any], attore: Attore) -> dict[str, Any]:
    aperta = r["stato"] == "PROPOSTA"
    puo = attore.ruolo == "ADMIN_TENANT" and attore.tenant_id == r["tenant_id"]
    motivo = None if aperta and puo else (
        "proposta già decisa" if not aperta else "decide l'amministratore dell'ente")
    voce = registro_console()[r["codice"]] if r["codice"] in registro_console() else None
    return {"proposta_id": r["id"], "bozza_id": r["bozza_id"], "ente": r["tenant_id"],
            "codice": r["codice"], "valore": json_o_none(r["valore"]), "motivo": r["motivo"],
            "descrizione": voce.descrizione if voce else None,
            "stato": r["stato"], "motivo_risposta": r["motivo_risposta"],
            "risposto_da": r["risposto_da"],
            "risposto_il": r["risposto_il"].isoformat() if r["risposto_il"] else None,
            "created_at": r["created_at"].isoformat(),
            "azioni_consentite": {"accetta": motivo is None, "rifiuta": motivo is None,
                                  "motivo_blocco": motivo}}


async def elenco_proposte(attore: Attore, *, stato: str | None, ente: str | None, limit: int,
                          cursore: str | None) -> dict[str, Any]:
    if not attore.conservatore and attore.ruolo != "ADMIN_TENANT":
        raise ErroreApi("CFG-403", f"il ruolo {attore.ruolo} non gestisce la configurazione")
    condizioni: list[str] = []
    parametri: dict[str, Any] = {"n": limit + 1}
    if not attore.conservatore or ente:
        condizioni.append("p.tenant_id = CAST(:t AS uuid)")
        parametri["t"] = attore.tenant_id if not attore.conservatore else _uuid(ente)
    if stato:
        if stato not in STATI_PROPOSTA:
            raise ErroreApi("CFG-400", "Stato non valido (" + ", ".join(STATI_PROPOSTA) + ")")
        condizioni.append("p.stato = :s")
        parametri["s"] = stato
    if cursore:
        quando, ultimo = decodifica_cursore("proposte", cursore)
        condizioni.append("(p.created_at, p.id) < (:q, CAST(:i AS uuid))")
        parametri.update(q=quando, i=ultimo)
    where = ("WHERE " + " AND ".join(condizioni)) if condizioni else ""
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                _COLONNE_PROPOSTA + where  # noqa: S608 — frammenti fissi, valori come parametri
                + " ORDER BY p.created_at DESC, p.id DESC LIMIT :n"),
                parametri)).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_proposte_elenco", exc) from None
    altri = len(righe) > limit
    righe = righe[:limit]
    return {"items": [_proposta_dict(r, attore) for r in righe], "limit": limit, "altri": altri,
            "cursore": codifica_cursore("proposte", righe[-1]["created_at"], righe[-1]["id"])
            if altri else None}


# ── Accordi di versamento: elenco (R05, richiesta della console) ──────

STATI_ACCORDO = ("ATTIVO", "CESSATO")


async def elenco_accordi(attore: Attore, *, ente: str | None, classe: str | None,
                         stato: str | None, limit: int, cursore: str | None) -> dict[str, Any]:
    """Ultima versione di ogni accordo (``codice``) del perimetro, dalla più recente,
    a cursore. Permessi come le bozze: conservatore (tutti gli enti, ``ente``
    facoltativo) e ADMIN_TENANT (solo il suo ente)."""
    if not attore.conservatore and attore.ruolo != "ADMIN_TENANT":
        raise ErroreApi("CFG-403", f"il ruolo {attore.ruolo} non gestisce la configurazione")
    # ultima versione: nessuna versione maggiore dello stesso codice (indice unico
    # uq_accordi_versamento_versione su tenant_id, codice, versione)
    condizioni = ["NOT EXISTS (SELECT 1 FROM public.accordi_versamento n "
                  "WHERE n.tenant_id = a.tenant_id AND n.codice = a.codice "
                  "AND n.versione > a.versione)"]
    parametri: dict[str, Any] = {"n": limit + 1}
    if not attore.conservatore or ente:
        condizioni.append("a.tenant_id = CAST(:t AS uuid)")
        parametri["t"] = attore.tenant_id if not attore.conservatore else _uuid(ente)
    if classe:
        condizioni.append("a.classe_id = CAST(:c AS uuid)")
        parametri["c"] = _uuid(classe)
    if stato:
        if stato not in STATI_ACCORDO:
            raise ErroreApi("CFG-400", "Stato non valido (" + ", ".join(STATI_ACCORDO) + ")")
        condizioni.append("a.cessato" if stato == "CESSATO" else "NOT a.cessato")
    if cursore:
        quando, ultimo = decodifica_cursore("accordi", cursore)
        condizioni.append("(a.created_at, a.id) < (:q, CAST(:i AS uuid))")
        parametri.update(q=quando, i=ultimo)
    try:
        async with get_async_session() as session:
            righe = (await session.execute(text(
                "SELECT a.id::text AS id, a.tenant_id::text AS tenant_id, a.codice, "  # noqa: S608 — frammenti fissi, valori come parametri
                "a.versione, a.classe_id::text AS classe_id, a.descrizione, a.valido_dal, "
                "a.cessato, a.created_at FROM public.accordi_versamento a WHERE "
                + " AND ".join(condizioni)
                + " ORDER BY a.created_at DESC, a.id DESC LIMIT :n"),
                parametri)).mappings().all()
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_accordi_elenco", exc) from None
    altri = len(righe) > limit
    righe = righe[:limit]
    return {"items": [{"codice": r["codice"], "versione": r["versione"], "ente": r["tenant_id"],
                       "classe": r["classe_id"], "descrizione": r["descrizione"],
                       "valido_dal": r["valido_dal"].isoformat(), "cessato": r["cessato"]}
                      for r in righe],
            "limit": limit, "altri": altri,
            "cursore": codifica_cursore("accordi", righe[-1]["created_at"], righe[-1]["id"])
            if altri else None}


async def rispondi_proposta(attore: Attore, proposta_id: str, *, accetta: bool,
                            motivo: str) -> dict[str, Any]:
    """L'amministratore dell'ente accetta (il valore diventa quello del livello
    ENTE, con storico) o rifiuta con motivo. Una voce d'audit."""
    pid = _uuid(proposta_id)
    if pid is None:
        raise ErroreApi("CFG-404", "Proposta non trovata")
    try:
        async with get_async_session() as session:
            r = (await session.execute(text(_COLONNE_PROPOSTA + "WHERE p.id = CAST(:p AS uuid) "
                                            "FOR UPDATE"), {"p": pid})).mappings().first()
            visibile = r is not None and (attore.conservatore
                                          or r["tenant_id"] == attore.tenant_id)
            if not visibile:
                raise ErroreApi("CFG-404", "Proposta non trovata")
            assert r is not None
            if attore.ruolo != "ADMIN_TENANT" or r["tenant_id"] != attore.tenant_id:
                raise ErroreApi("CFG-403", "La proposta la decide l'amministratore dell'ente")
            if r["stato"] != "PROPOSTA":
                raise ErroreApi("CFG-412", f"Proposta già {r['stato'].lower()}",
                                dettagli={"stato": r["stato"]})
            if accetta:
                registro = registro_console()
                voce = registro[r["codice"]] if r["codice"] in registro else None
                ambito = Ambito(livello=Livello.ENTE, ente=r["tenant_id"])
                blocco = ("impostazione non più nel catalogo" if voce is None
                          else motivo_blocco_voce(Attore("SUPER_ADMIN", None), ambito, voce))
                if blocco:
                    raise ErroreApi("CFG-422", "La proposta non si può più applicare",
                                    dettagli={"bloccati": [{"codice": r["codice"],
                                                            "motivo": blocco}]})
                assert voce is not None
                valore = valore_per_fotografia(voce.converti(json_o_none(r["valore"]),
                                                             Livello.ENTE))
                calcolato = PianoCalcolato(
                    Piano(scritture={r["codice"]: valore}),
                    Contesto(ambito=ambito, classe=None, livelli={}), Risorsa.IMPOSTAZIONI, "")
                await _scrivi_valori(session, calcolato, attore, motivo, None, proposta_id=r["id"])
            nuovo_stato = "ACCETTATA" if accetta else "RIFIUTATA"
            await session.execute(text(
                "UPDATE public.configurazione_proposte SET stato = :s, motivo_risposta = :m, "
                "risposto_da = CAST(:u AS uuid), risposto_il = now() "
                "WHERE id = CAST(:p AS uuid)"),
                {"s": nuovo_stato, "m": motivo, "u": attore.user_id, "p": r["id"]})
            await _scrivi_audit(session, f"configurazione.proposta.{nuovo_stato.lower()}",
                                attore, r["tenant_id"],
                                f"Proposta {r['codice']} {nuovo_stato.lower()}",
                                proposta_id=r["id"], codice=r["codice"], motivo=motivo)
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_proposta_risposta", exc) from None
    return {"proposta_id": r["id"], "stato": nuovo_stato}


# ── Validatore preventivo ───────────────────────────────────────────

async def _ambiente_versamento(session: Any, attore: Attore, ente: str, classe: str,
                               accordo: str | None, override: Mapping[str, Any] | None,
                               adesso: datetime) -> AmbienteRegole:
    """Ambiente come quello del gateway per (ente, classe, accordo, override)."""
    info = await carica_classe(session, attore, classe, ente)
    ambito = Ambito(livello=Livello.CLASSE, ente=ente, classe=info.id)
    contesto = await leggi_contesto(session, ambito, info)
    personalizzazione = valida_override(override, REGISTRO)
    return prepara_ambiente(
        registro=REGISTRO, livelli=contesto.livelli, adesso=adesso, tenant_id=ente,
        classe_id=info.id, profili_classe=contesto.profili_classe,
        profili_tipologia_db=contesto.profili_tipologia_db,
        accordi=contesto.accordi, accordo_richiesto=accordo, override=personalizzazione)


def _ente_validatore(attore: Attore, ente: str | None) -> str:
    """Ente del validatore: il proprio; il conservatore lo indica."""
    if attore.conservatore:
        if not ente:
            raise ErroreApi("CFG-400", "indicare l'ente")
        return str(_uuid(ente) or "")
    if attore.ruolo not in ("ADMIN_TENANT", "OPERATORE", "API_SYSTEM"):
        raise ErroreApi("CFG-403", f"il ruolo {attore.ruolo} non versa")
    if ente is not None and _uuid(ente) != attore.tenant_id:
        raise ErroreApi("CFG-404", "Ente non trovato")
    return str(attore.tenant_id)


async def valida(attore: Attore, *, indice: Any, file_impronte: Mapping[str, Any] | None,
                 ente: str | None, adesso: datetime) -> dict[str, Any]:
    """Stessi rilievi del versamento, senza scrivere (transazione READ ONLY)."""
    from libs.verumdoc_rules.accordi import AccordiAmbigui, AccordoNonValido
    from libs.verumdoc_rules.override import OverrideNonAmmesso

    eid = _ente_validatore(attore, ente)
    intestazione, forma = controlla_forma(indice)
    if intestazione is None:
        return {"esito": "NON_CONFORME", "rilievi": forma, "avvisi": [],
                "regole_applicate": None, "fasi_eseguite": ["forma"]}
    try:
        async with get_async_session_lettura() as session:
            await verifica_ente(session, attore, eid)
            try:
                info = await carica_classe(session, attore, intestazione.classe, eid)
            except ErroreApi:
                return {"esito": "NON_CONFORME", "rilievi": [*forma, rilievo(
                    "VRS-105", f"Classe documentale {intestazione.classe!r} non censita",
                    campo="classe_documentale")], "avvisi": [], "regole_applicate": None,
                    "fasi_eseguite": ["forma"]}
            if not info.attiva:
                forma.append(rilievo("VRS-106", f"Classe documentale {info.codice!r} non attiva",
                                     campo="classe_documentale"))
            try:
                ambiente = await _ambiente_versamento(session, attore, eid, info.id,
                                                      intestazione.accordo,
                                                      intestazione.override, adesso)
            except OverrideNonAmmesso as exc:
                forma.append(rilievo("RGL-111", str(exc)[:500], campo="override"))
                return {"esito": "NON_CONFORME", "rilievi": forma, "avvisi": [],
                        "regole_applicate": None, "fasi_eseguite": ["forma"]}
            except AccordoNonValido as exc:
                forma.append(rilievo("RGL-112", str(exc), campo="accordo"))
                return {"esito": "NON_CONFORME", "rilievi": forma, "avvisi": [],
                        "regole_applicate": None, "fasi_eseguite": ["forma"]}
            except (ProfiloNonValido, AccordiAmbigui, ValueError) as exc:
                logger.error("validatore_configurazione_non_valida", errore=str(exc)[:200])
                raise ErroreApi("RGL-503", "Configurazione delle regole non valida") from exc
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_validatore", exc) from None
    file_info = None
    if file_impronte is not None:
        file_info = {str(k): dict(v) for k, v in file_impronte.items() if isinstance(v, Mapping)}
    return valida_versamento(indice, ambiente, file_info=file_info,
                             rilievi_forma=forma).come_dict()


async def profilo_esportato(attore: Attore, *, ente: str | None, classe: str,
                            accordo: str | None, adesso: datetime) -> dict[str, Any]:
    """Configurazione effettiva di (ente, classe, accordo) per il validatore senza rete."""
    eid = _ente_validatore(attore, ente)
    try:
        async with get_async_session_lettura() as session:
            await verifica_ente(session, attore, eid)
            info = await carica_classe(session, attore, classe, eid)
            try:
                ambiente = await _ambiente_versamento(session, attore, eid, info.id, accordo,
                                                      None, adesso)
            except ValueError as exc:
                raise ErroreApi("CFG-400", str(exc)[:300]) from exc
    except SQLAlchemyError as exc:
        raise _errore_db("configurazione_esportazione", exc) from None
    return esporta_profilo(ambiente, classe=info.codice, ente=eid, adesso=adesso)


def adesso_utc() -> datetime:
    return datetime.now(UTC)
