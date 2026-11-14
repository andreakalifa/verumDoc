# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/quote.py — quote per piano: richieste al minuto e PdV al giorno.

Riferimenti: VD-C-099 (R03, agente b4); piano R03–R05 §4.2 («limiti di
frequenza e di volume per piano e per ente, sulle chiavi API come sui token»);
REGOLE_SVILUPPO §8.1 (limiti in impostazioni, non nel codice), §19.8 (429 con
Retry-After).

Prima il piano si leggeva dal claim ``piano`` del token: i token delle chiavi
API non lo portavano e i sistemi versanti avevano sempre 60 richieste al minuto;
``PDV_LIMITS`` era definito e mai usato. Ora:

  - il piano si legge dall'ENTE nel database (``tenants.piano``) con una cache
    breve (``QUOTE_PIANO_CACHE_SECONDI``, predefinito 60 s), mai dal token;
    nella stessa lettura le quote personalizzate dell'ente (R03 i3,
    ``limiti_ente``), che prevalgono sul piano;
  - i limiti per piano sono impostazioni (variabili d'ambiente JSON
    ``QUOTE_RICHIESTE_MINUTO`` e ``QUOTE_PDV_GIORNO``); predefiniti R03 i3
    120/600/6000 e 1000/20000/200000; un piano senza voce usa quella di
    ``QUOTE_PIANO_PREDEFINITO``;
  - richieste al minuto: finestra scorrevole su Redis per ente (come prima),
    ``QTA-001`` con ``Retry-After`` = secondi finché la richiesta più vecchia
    esce dalla finestra;
  - PdV al giorno: contatore Redis per ente e giorno (ora italiana).
    R03 f1 (REV-02): il posto si PRENOTA atomicamente prima del trasferimento
    (``prenota_pdv``: INCR e confronto in uno script Lua) e si RILASCIA se il
    PdV non nasce (``rilascia_pdv``); una richiesta ripetuta con la stessa
    Idempotency-Key non prenota. ``QTA-002`` con ``Retry-After`` fino alla
    mezzanotte. ``controlla_quota_pdv`` + ``registra_pdv_versato`` (controllo e
    conteggio separati, NON atomici) restano solo per compatibilità.

R07 (D13): i limiti dei piani sono impostazioni del conservatore modificabili
in console (``QUOTA_PIANO_<PIANO>_*``, valori iniziali R03; le variabili
``QUOTE_*`` restano il valore del conservatore da ambiente, la riga del database
prevale). Oltre la quota giornaliera l'ente sceglie (``QUOTA_SUPERAMENTO``) fra
RIFIUTA (429 fino a mezzanotte, come prima) e RALLENTA: un PdV ogni
``QUOTA_RALLENTAMENTO_SECONDI`` (chiave Redis ``qta:lento:<ente>`` con SET NX EX),
gli altri 429 con ``Retry-After`` = secondi al prossimo posto. Gli avvisi
all'80% e al 100% li accoda il gateway (``app.services.quota_versamento``).

Redis non raggiungibile: la richiesta passa e si scrive un avviso nel log
(le quote proteggono la capacità, non la sicurezza dei dati: decisione da
confermare, vedi rapporto R03 b4). Senza client Redis (``None``) nessun
controllo: è il caso dei test che lo escludono esplicitamente.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger(__name__)

#: Predefiniti per piano (R03 i3, proposta f2 da confermare col committente).
#: I valori di R02 (60/300/1000 richieste, 100/500/2000 PdV) non bastavano per
#: l'obiettivo di 1M documenti al giorno: un grande ente restava fermo a 2000 PdV.
#:   - starter    1.000 PdV/giorno,   120 richieste/minuto (piccolo comune);
#:   - business  20.000 PdV/giorno,   600 richieste/minuto (~14 PdV/min per 24 h);
#:   - enterprise 200.000 PdV/giorno, 6.000 richieste/minuto (20% dell'obiettivo
#:     giornaliero; 100 richieste/s coprono 50 documenti/s con 2 richieste ciascuno).
#: Un ente fuori scala ha un valore proprio che prevale sul piano: impostazioni
#: ``QUOTA_PDV_GIORNO`` / ``QUOTA_RICHIESTE_MINUTO`` al livello ENTE del registro
#: (``libs.verumdoc_rules.quote``), lette da ``limiti_ente``.
RICHIESTE_MINUTO_PREDEFINITE: dict[str, int] = {"starter": 120, "business": 600, "enterprise": 6000}
PDV_GIORNO_PREDEFINITI: dict[str, int] = {"starter": 1000, "business": 20000, "enterprise": 200000}


class ImpostazioniQuote(BaseSettings):
    """Limiti per piano (variabili d'ambiente con prefisso ``QUOTE_``)."""

    model_config = SettingsConfigDict(env_prefix="QUOTE_", case_sensitive=False)

    richieste_minuto: dict[str, int] = Field(
        default_factory=lambda: dict(RICHIESTE_MINUTO_PREDEFINITE),
        description="Richieste al minuto per piano, es. {\"starter\": 60}",
    )
    pdv_giorno: dict[str, int] = Field(
        default_factory=lambda: dict(PDV_GIORNO_PREDEFINITI),
        description="Pacchetti di versamento al giorno per piano",
    )
    piano_predefinito: str = Field(
        "starter", description="Piano usato se l'ente non ne ha uno noto")
    finestra_secondi: int = Field(60, ge=1, le=3600)
    piano_cache_secondi: float = Field(60.0, ge=0, le=3600)
    fuso_giornata: str = Field("Europe/Rome", description="Fuso del giorno delle quote giornaliere")


class QuotaSuperata(Exception):
    """Quota esaurita: ``codice`` QTA-001/QTA-002, ``retry_after`` in secondi."""

    def __init__(self, codice: str, *, limite: int, retry_after: int, piano: str) -> None:
        super().__init__(f"{codice}: limite {limite}")
        self.codice = codice
        self.limite = limite
        self.retry_after = max(1, int(retry_after))
        self.piano = piano

    def errore_api(self) -> Exception:
        """Errore del catalogo (problem+json) con Retry-After."""
        from libs.shared.errori import ErroreApi

        testo = ("Limite di richieste al minuto del piano" if self.codice == "QTA-001"
                 else "Limite giornaliero di versamenti del piano")
        return ErroreApi(
            self.codice, f"{testo} «{self.piano}»: {self.limite}.",
            dettagli={"limite": self.limite, "piano": self.piano},
            retry_after=self.retry_after,
            headers={"Retry-After": str(self.retry_after)},
        )


def _limite(tabella: dict[str, int], piano: str, imp: ImpostazioniQuote) -> int:
    chiave = (piano or "").strip().lower()
    if chiave in tabella:
        return int(tabella[chiave])
    logger.warning("piano_senza_quota", piano=piano, predefinito=imp.piano_predefinito)
    return int(tabella.get(imp.piano_predefinito.lower(), min(tabella.values(), default=0)))


def limite_richieste(piano: str, imp: ImpostazioniQuote, personalizzato: int | None = None,
                     del_piano: int | None = None) -> int:
    """Richieste al minuto: il valore dell'ente se c'è, poi quello del piano dalla
    console (``del_piano``), infine quello del piano da ambiente."""
    if personalizzato is not None:
        return int(personalizzato)
    if del_piano is not None:
        return int(del_piano)
    return _limite({k.lower(): v for k, v in imp.richieste_minuto.items()}, piano, imp)


def limite_pdv(piano: str, imp: ImpostazioniQuote, personalizzato: int | None = None,
               del_piano: int | None = None) -> int:
    """PdV al giorno: il valore dell'ente se c'è, poi quello del piano dalla console,
    infine quello del piano da ambiente."""
    if personalizzato is not None:
        return int(personalizzato)
    if del_piano is not None:
        return int(del_piano)
    return _limite({k.lower(): v for k, v in imp.pdv_giorno.items()}, piano, imp)


@dataclass(frozen=True)
class LimitiEnte:
    """Piano dell'ente e quote personalizzate (``None`` = vale il piano).

    UNICA fonte dei limiti per il gateway (REST, sessioni, SFTP,
    PDND, quota PdV/giorno) e per i servizi (``libs.shared.limiti_richieste``)."""

    piano: str
    richieste_minuto_ente: int | None = None
    pdv_giorno_ente: int | None = None
    #: R07 (D13): limiti del piano dalla console (None = dalle variabili QUOTE_*)
    richieste_minuto_piano: int | None = None
    pdv_giorno_piano: int | None = None
    #: R07 (D13): oltre la quota giornaliera, un PdV ogni N secondi (None = rifiuta)
    rallenta_secondi: int | None = None

    def richieste_minuto(self, imp: ImpostazioniQuote) -> int:
        return limite_richieste(self.piano, imp, self.richieste_minuto_ente,
                                self.richieste_minuto_piano)

    def pdv_giorno(self, imp: ImpostazioniQuote) -> int:
        return limite_pdv(self.piano, imp, self.pdv_giorno_ente, self.pdv_giorno_piano)


# ── Piano e quote dell'ente (dal database, cache breve) ─────────────

#: tenant_id → (limiti, istante di scadenza della voce)
_cache_piani: dict[str, tuple[LimitiEnte, float]] = {}
_MASSIMO_VOCI = 20_000


def svuota_cache_piani() -> None:
    _cache_piani.clear()


def _quote_da_valori(tid: str, valori: Any) -> tuple[int | None, int | None]:
    """Quote personalizzate dai valori ENTE letti; valore non valido → piano, con errore nel log."""
    from libs.verumdoc_rules.impostazioni import ValoreImpostazioneNonValido
    from libs.verumdoc_rules.quote import quote_personalizzate

    if not valori:
        return None, None
    if isinstance(valori, str):
        import json

        valori = json.loads(valori)
    try:
        quote = quote_personalizzate(valori)
    except ValoreImpostazioneNonValido as exc:
        # Configurazione rotta: non si indovina un valore, vale il piano (la quota
        # protegge la capacità, non i dati). L'errore va corretto dalla console.
        logger.error("quota_ente_non_valida", tenant_id=tid, errore=str(exc))
        return None, None
    return quote.richieste_minuto, quote.pdv_giorno


def _codici_parametri_quote() -> tuple[str, ...]:
    from libs.shared.parametri import con_limiti
    from libs.verumdoc_rules.parametri_servizio import (
        PIANI,
        QUOTA_RALLENTAMENTO_SECONDI,
        QUOTA_SUPERAMENTO,
        codice_piano_pdv,
        codice_piano_richieste,
    )

    # W1: con il limite del conservatore per l'ente (LIMITE.QUOTA_SUPERAMENTO)
    return tuple(con_limiti((*(codice_piano_pdv(p) for p in PIANI),
                             *(codice_piano_richieste(p) for p in PIANI),
                             QUOTA_SUPERAMENTO, QUOTA_RALLENTAMENTO_SECONDI)))


CODICI_PARAMETRI_QUOTE: tuple[str, ...] = _codici_parametri_quote()


def ambiente_piani(imp: ImpostazioniQuote) -> dict[str, int]:
    """Limiti dei piani dalle variabili ``QUOTE_*`` come valori del conservatore
    da ambiente (R07: la riga del database, scritta dalla console, prevale)."""
    from libs.verumdoc_rules.parametri_servizio import (
        PIANI,
        codice_piano_pdv,
        codice_piano_richieste,
    )

    valori: dict[str, int] = {}
    pdv = {k.lower(): v for k, v in imp.pdv_giorno.items()}
    rpm = {k.lower(): v for k, v in imp.richieste_minuto.items()}
    for p in PIANI:
        if p in pdv:
            valori[codice_piano_pdv(p)] = int(pdv[p])
        if p in rpm:
            valori[codice_piano_richieste(p)] = int(rpm[p])
    return valori


def _parametri_quote(piano: str, grezzo: Any, imp: ImpostazioniQuote) -> dict[str, Any]:
    """Limiti del piano dalla console, scelta oltre la quota e rallentamento (D13)."""
    import json

    from libs.shared.parametri import livelli_da_righe, risolvi
    from libs.verumdoc_rules.parametri_servizio import (
        PIANI,
        QUOTA_RALLENTAMENTO_SECONDI,
        QUOTA_SUPERAMENTO,
        SUPERAMENTO_RALLENTA,
        codice_piano_pdv,
        codice_piano_richieste,
    )

    righe = json.loads(grezzo) if isinstance(grezzo, str) else (grezzo or [])
    valori = risolvi(livelli_da_righe(righe, decodificati=True), CODICI_PARAMETRI_QUOTE,
                     ambiente=ambiente_piani(imp))
    chiave = piano.strip().lower()
    noto = chiave in PIANI
    return {
        "pdv_giorno_piano": int(valori[codice_piano_pdv(chiave)]) if noto else None,
        "richieste_minuto_piano": int(valori[codice_piano_richieste(chiave)]) if noto else None,
        "rallenta_secondi": (int(valori[QUOTA_RALLENTAMENTO_SECONDI])
                             if valori[QUOTA_SUPERAMENTO] == SUPERAMENTO_RALLENTA else None),
    }


async def limiti_ente(tenant_id: Any, imp: ImpostazioniQuote) -> LimitiEnte:
    """Piano (``tenants.piano``) e quote personalizzate dell'ente (righe ENTE di
    ``impostazioni_valori`` con i codici di ``libs.verumdoc_rules.quote``), in UNA
    lettura con cache ``piano_cache_secondi``.

    Ente non UUID, assente o database non configurato nel processo: piano
    predefinito senza personalizzazioni. Errore del database: piano predefinito
    con avviso nel log (la revoca, che gira prima, è già fail-closed sullo
    stesso database)."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from libs.shared.db import DBNonInizializzato, get_async_session
    from libs.verumdoc_rules.quote import CODICI_QUOTE

    predefinito = LimitiEnte(piano=imp.piano_predefinito)
    try:
        tid = str(uuid.UUID(str(tenant_id)))
    except (TypeError, ValueError):
        return predefinito
    adesso = time.monotonic()
    voce = _cache_piani.get(tid)
    if voce is not None and voce[1] > adesso:
        return voce[0]
    from libs.shared.security.ambito_dati import ambito_ente

    try:
        # Revisione R05 (R14): impostazioni_valori ha la RLS per ente; le quote di
        # un ente si leggono nel SUO ambito, qualunque sia quello del chiamante
        # (richiesta senza ambito ancora impostato, fruitore PDND, servizio)
        with ambito_ente(tid):
            async with get_async_session() as session:
                riga = (await session.execute(
                    text(
                        "SELECT t.piano, ("
                        "  SELECT jsonb_object_agg(v.codice, v.valore)::text"
                        "  FROM public.impostazioni_valori v"
                        "  WHERE v.livello = 'ENTE' AND v.tenant_id = t.id"
                        "    AND v.codice = ANY(CAST(:codici AS text[]))"
                        ") AS quote, ("
                        "  SELECT jsonb_agg(jsonb_build_object('livello', v.livello, "
                        "    'codice', v.codice, 'valore', v.valore))::text"
                        "  FROM public.impostazioni_valori v"
                        "  WHERE v.classe_id IS NULL AND v.codice = ANY(CAST(:par AS text[]))"
                        "    AND (v.livello = 'CONSERVATORE' OR "
                        "         (v.livello = 'ENTE' AND v.tenant_id = t.id))"
                        ") AS parametri "
                        "FROM public.tenants t WHERE t.id = CAST(:t AS uuid)"
                    ),
                    {"t": tid, "codici": list(CODICI_QUOTE), "par": list(CODICI_PARAMETRI_QUOTE)},
                )).first()
    except DBNonInizializzato:
        return predefinito
    except SQLAlchemyError as exc:
        logger.warning("piano_non_leggibile", tenant_id=tid, errore=type(exc).__name__)
        return predefinito
    if riga is None:
        risultato = predefinito
    else:
        rpm, pdv = _quote_da_valori(tid, riga[1])
        piano = str(riga[0] or imp.piano_predefinito)
        risultato = LimitiEnte(piano=piano, richieste_minuto_ente=rpm, pdv_giorno_ente=pdv,
                               **_parametri_quote(piano, riga[2], imp))
    if len(_cache_piani) >= _MASSIMO_VOCI:
        _cache_piani.clear()
    _cache_piani[tid] = (risultato, adesso + imp.piano_cache_secondi)
    return risultato


async def piano_ente(tenant_id: Any, imp: ImpostazioniQuote) -> str:
    """Solo il piano dell'ente (vedi ``limiti_ente``, stessa lettura e cache)."""
    return (await limiti_ente(tenant_id, imp)).piano


# ── Richieste al minuto ─────────────────────────────────────────────

def errore_redis(exc: BaseException) -> bool:
    """True per gli errori di rete o del client Redis (non per i bug del codice)."""
    return isinstance(exc, (OSError, TimeoutError)) or type(exc).__module__.startswith("redis")


async def _pipeline(redis: Any) -> Any:
    risultato = redis.pipeline()
    if asyncio.iscoroutine(risultato):  # AsyncMock nei test
        return await risultato
    return risultato


def chiave_richieste(tenant_id: str) -> str:
    """Chiave Redis della finestra delle richieste dell'ente (come in R02)."""
    return f"rl:rpm:{tenant_id}"


async def consuma_richiesta(
    redis: Any, chiave: str, *, limite: int, piano: str, finestra: int = 60,
    adesso: float | None = None,
) -> None:
    """Conta la richiesta nella finestra scorrevole ``chiave`` (vedi
    ``chiave_richieste``); ``QuotaSuperata`` (QTA-001) se il limite è già
    raggiunto. ``redis`` None: nessun controllo."""
    if redis is None:
        return
    ora = time.time() if adesso is None else adesso
    tenant_id = chiave
    try:
        pipe = await _pipeline(redis)
        pipe.zremrangebyscore(chiave, 0, ora - finestra)
        pipe.zcard(chiave)
        pipe.zrange(chiave, 0, 0, withscores=True)
        pipe.zadd(chiave, {f"{ora:.6f}:{uuid.uuid4().hex[:8]}": ora})
        pipe.expire(chiave, finestra)
        risultati = await pipe.execute()
    except Exception as exc:
        if not errore_redis(exc):
            raise
        logger.warning("quota_non_verificabile", tenant_id=tenant_id, errore=type(exc).__name__)
        return
    conteggio = int(risultati[1] or 0)
    if conteggio >= limite:
        piu_vecchia = risultati[2][0][1] if risultati[2] else ora
        attesa = math.ceil(float(piu_vecchia) + finestra - ora)
        raise QuotaSuperata("QTA-001", limite=limite, retry_after=max(1, attesa), piano=piano)


# ── PdV al giorno ───────────────────────────────────────────────────

def chiave_pdv_giorno(tenant_id: str, adesso: datetime, fuso: str) -> str:
    giorno = adesso.astimezone(ZoneInfo(fuso)).strftime("%Y%m%d")
    return f"qta:pdv:{tenant_id}:{giorno}"


def secondi_alla_mezzanotte(adesso: datetime, fuso: str) -> int:
    locale = adesso.astimezone(ZoneInfo(fuso))
    domani = (locale + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # differenza in UTC: fra datetime con lo stesso fuso Python sottrae l'ora
    # «da orologio» e sbaglierebbe nei giorni del cambio d'ora
    return max(1, math.ceil((domani.astimezone(UTC) - adesso.astimezone(UTC)).total_seconds()))


async def controlla_quota_pdv(
    redis: Any, tenant_id: str, *, limite: int, piano: str, fuso: str = "Europe/Rome",
    adesso: datetime | None = None,
) -> None:
    """``QuotaSuperata`` (QTA-002) se oggi l'ente ha già versato ``limite`` PdV."""
    if redis is None:
        return
    ora = adesso or datetime.now(UTC)
    try:
        valore = await redis.get(chiave_pdv_giorno(tenant_id, ora, fuso))
    except Exception as exc:
        if not errore_redis(exc):
            raise
        logger.warning("quota_pdv_non_verificabile", tenant_id=tenant_id,
                       errore=type(exc).__name__)
        return
    if int(valore or 0) >= limite:
        raise QuotaSuperata("QTA-002", limite=limite,
                            retry_after=secondi_alla_mezzanotte(ora, fuso), piano=piano)


#: Prenotazione atomica di un PdV (R03 f1, REV-02): INCR e confronto con il limite in
#: UNA operazione Redis; oltre il limite si annulla subito l'incremento (DECR) e si
#: restituisce il valore prima del tentativo con segno negativo. La scadenza della
#: chiave si fissa a ogni prenotazione (2 giorni: il contatore di ieri resta leggibile).
#: Il commento «verumdoc:prenota_pdv» identifica lo script (anche nel Redis finto dei test).
SCRIPT_PRENOTA_PDV = """-- verumdoc:prenota_pdv
local valore = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
if valore > tonumber(ARGV[1]) then
  redis.call('DECR', KEYS[1])
  return -(valore - 1)
end
return valore
"""

#: Rilascio di una prenotazione (PdV poi non creato): DECR solo se il contatore è
#: positivo, così un rilascio ripetuto o dopo la scadenza non va mai sotto zero.
SCRIPT_RILASCIA_PDV = """-- verumdoc:rilascia_pdv
local valore = tonumber(redis.call('GET', KEYS[1]) or '0')
if valore > 0 then
  return redis.call('DECR', KEYS[1])
end
return 0
"""

#: Durata della chiave del contatore giornaliero (secondi).
SCADENZA_CONTATORE_PDV = 2 * 86400


class PrenotazionePdv:
    """Un posto della quota giornaliera preso per un versamento in corso.

    Si rilascia (``rilascia_pdv``) se il versamento NON crea un PdV nuovo
    (rifiuto, ripetizione della stessa richiesta, errore); se il PdV nasce, la
    prenotazione resta: è il conteggio. ``chiave`` è fissata alla prenotazione,
    quindi un rilascio dopo la mezzanotte libera il posto del giorno giusto."""

    __slots__ = ("chiave", "oltre_limite", "rilasciata", "tenant_id", "totale")

    def __init__(self, chiave: str, tenant_id: str, totale: int) -> None:
        self.chiave = chiave
        self.tenant_id = tenant_id
        self.totale = totale
        self.rilasciata = False
        #: R07 (D13): posto preso oltre la quota con RALLENTA
        self.oltre_limite = False


def chiave_rallentamento(tenant_id: str) -> str:
    """R07 (D13): chiave del posto «rallentato» oltre la quota giornaliera."""
    return f"qta:lento:{tenant_id}"


async def prenota_pdv(
    redis: Any, tenant_id: str, *, limite: int, piano: str, fuso: str = "Europe/Rome",
    adesso: datetime | None = None, rallenta_secondi: int | None = None,
) -> PrenotazionePdv | None:
    """Prende atomicamente un posto della quota di PdV del giorno (QTA-002 se finita).

    Controllo e incremento sono UNA operazione Redis (script Lua): N richieste
    parallele dello stesso ente non superano mai ``limite``. Restituisce la
    prenotazione da rilasciare se il PdV poi non nasce; ``None`` senza Redis
    (client ``None``) o con Redis non raggiungibile: fail-open con avviso nel
    log, come per la quota al minuto (decisione b4: le quote proteggono la
    capacità, non la sicurezza dei dati)."""
    if redis is None:
        return None
    ora = adesso or datetime.now(UTC)
    chiave = chiave_pdv_giorno(tenant_id, ora, fuso)
    try:
        esito = int(await redis.eval(SCRIPT_PRENOTA_PDV, 1, chiave, int(limite),
                                     SCADENZA_CONTATORE_PDV))
    except Exception as exc:
        if not errore_redis(exc):
            raise
        logger.warning("quota_pdv_non_verificabile", tenant_id=tenant_id,
                       errore=type(exc).__name__, effetto="versamento ammesso senza quota")
        return None
    if esito <= 0:
        if rallenta_secondi:
            return await _posto_rallentato(redis, tenant_id, limite=limite, piano=piano,
                                           secondi=int(rallenta_secondi), usati=-esito)
        raise QuotaSuperata("QTA-002", limite=limite,
                            retry_after=secondi_alla_mezzanotte(ora, fuso), piano=piano)
    return PrenotazionePdv(chiave, tenant_id, esito)


async def _posto_rallentato(redis: Any, tenant_id: str, *, limite: int, piano: str,
                            secondi: int, usati: int) -> PrenotazionePdv | None:
    """Oltre la quota con RALLENTA: un PdV ogni ``secondi`` (SET NX EX atomico).

    Il posto non si rilascia: se il PdV poi non nasce, il prossimo aspetta lo
    stesso (il rallentamento protegge la capacità). Redis non raggiungibile:
    fail-open come la prenotazione."""
    chiave = chiave_rallentamento(tenant_id)
    try:
        preso = await redis.set(chiave, "1", nx=True, ex=secondi)
        attesa = None if preso else await redis.ttl(chiave)
    except Exception as exc:
        if not errore_redis(exc):
            raise
        logger.warning("quota_pdv_non_verificabile", tenant_id=tenant_id,
                       errore=type(exc).__name__, effetto="versamento ammesso senza quota")
        return None
    if not preso:
        raise QuotaSuperata("QTA-002", limite=limite, piano=piano,
                            retry_after=int(attesa) if attesa and int(attesa) > 0 else secondi)
    prenotazione = PrenotazionePdv(chiave, tenant_id, usati + 1)
    prenotazione.rilasciata = True   # nessun posto del contatore da restituire
    prenotazione.oltre_limite = True
    return prenotazione


async def rilascia_pdv(redis: Any, prenotazione: PrenotazionePdv | None) -> None:
    """Restituisce il posto di una prenotazione il cui PdV non è nato (idempotente)."""
    if redis is None or prenotazione is None or prenotazione.rilasciata:
        return
    prenotazione.rilasciata = True
    try:
        await redis.eval(SCRIPT_RILASCIA_PDV, 1, prenotazione.chiave)
    except Exception as exc:
        if not errore_redis(exc):
            raise
        # Il posto resta occupato fino alla fine della giornata: a sfavore del
        # cliente ma senza superare mai il limite.
        logger.warning("quota_pdv_rilascio_non_riuscito", tenant_id=prenotazione.tenant_id,
                       errore=type(exc).__name__)


async def registra_pdv_versato(
    redis: Any, tenant_id: str, *, fuso: str = "Europe/Rome", adesso: datetime | None = None,
) -> int | None:
    """Conta un PdV NUOVO dell'ente nel giorno corrente; restituisce il totale."""
    if redis is None:
        return None
    ora = adesso or datetime.now(UTC)
    chiave = chiave_pdv_giorno(tenant_id, ora, fuso)
    try:
        totale = int(await redis.incr(chiave))
        await redis.expire(chiave, 2 * 86400)
    except Exception as exc:
        if not errore_redis(exc):
            raise
        logger.warning("quota_pdv_non_registrata", tenant_id=tenant_id, errore=type(exc).__name__)
        return None
    return totale


__all__ = [
    "PDV_GIORNO_PREDEFINITI", "RICHIESTE_MINUTO_PREDEFINITE", "ImpostazioniQuote",
    "CODICI_PARAMETRI_QUOTE", "LimitiEnte", "ambiente_piani", "chiave_rallentamento",
    "limiti_ente",
    "PrenotazionePdv", "QuotaSuperata", "SCRIPT_PRENOTA_PDV", "SCRIPT_RILASCIA_PDV",
    "chiave_pdv_giorno", "prenota_pdv", "rilascia_pdv", "chiave_richieste", "errore_redis",
    "consuma_richiesta", "controlla_quota_pdv",
    "limite_pdv", "limite_richieste", "piano_ente", "registra_pdv_versato",
    "secondi_alla_mezzanotte", "svuota_cache_piani",
]
