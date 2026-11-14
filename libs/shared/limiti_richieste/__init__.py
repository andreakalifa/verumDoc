# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/limiti_richieste — limite di richieste al minuto per piano nei
servizi pubblici diversi dal gateway (distribution, dashboard-api, notification).

Riferimenti: revisione R03 REV-03 (VD-C-099); piano R03–R05 §4.2 («limiti di
frequenza … per piano e per ente, sulle chiavi API come sui token»);
REGOLE_SVILUPPO §8.1 (limiti in impostazioni), §9.1 (una sola implementazione),
§19.8 (429 con ``Retry-After``).

Un solo componente per tutti i servizi, sopra la logica del gateway
(``libs.shared.security.quote``: finestra scorrevole su Redis, piano letto
dall'ENTE nel database con cache breve, ``QTA-001`` con ``Retry-After``):

  - ``LimitatoreRichieste(servizio)`` conta una richiesta dell'identità già
    verificata (token utente o chiave API: il payload o l'``Identita`` del
    servizio), con il piano dell'ente e i limiti ``QUOTE_RICHIESTE_MINUTO``
    del gateway; la quota personalizzata dell'ente (``QUOTA_RICHIESTE_MINUTO``
    al livello ENTE del registro) prevale sul piano: stessa funzione
    ``limiti_ente`` del gateway, unica fonte (R03 i3);
  - il contatore è per ente e per servizio (``rl:rpm:<servizio>:<ente>``): un
    sistema che interroga molto la ricerca non toglie richieste al versamento
    né al portale. Con ``LIMITI_RICHIESTE_AMBITO=ente`` il contatore è quello
    del gateway (``rl:rpm:<ente>``), uno solo per tutti i servizi;
  - le operazioni costose (ricerca, PdD) hanno anche un limite per categoria
    (``LIMITI_RICHIESTE_CATEGORIE``, per piano), contato a parte;
  - ``dipendenza(...)`` e ``dipendenza_categoria(...)`` danno le dipendenze
    FastAPI: si mettono a livello di router (``dependencies=[...]``) sopra la
    dipendenza d'autenticazione del servizio, che FastAPI esegue una volta sola
    per richiesta anche se la rotta la richiede di nuovo.

Redis: ``REDIS_URL`` (stesso nome del gateway). Vuoto: in ``ENV=production``
il servizio non parte (``controlla_configurazione``, REGOLE §19.4); fuori
produzione i limiti sono disattivati con un avviso nel log. Redis non
raggiungibile durante una richiesta: la richiesta passa con un avviso (stessa
scelta del gateway: le quote proteggono la capacità, non i dati).
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.shared.errori import ErroreApi
from libs.shared.security.quote import (
    ImpostazioniQuote,
    LimitiEnte,
    QuotaSuperata,
    chiave_richieste,
    consuma_richiesta,
    limiti_ente,
)

logger = structlog.get_logger(__name__)

#: Limiti per categoria (richieste al minuto per piano) usati se non configurati.
#: Valori di capacità, non normativi: decisione da confermare (rapporto R03 f2).
CATEGORIE_PREDEFINITE: dict[str, dict[str, int]] = {
    # ricerca testuale (OpenSearch) e ricerche dell'archivio
    "ricerca": {"starter": 30, "business": 120, "enterprise": 600},
    # preparazione di un PdD (rilettura e impronta di ogni file, ZIP)
    "pdd": {"starter": 10, "business": 60, "enterprise": 300},
}


class ImpostazioniLimiti(BaseSettings):
    """Impostazioni del limitatore (prefisso ``LIMITI_RICHIESTE_``; Redis da ``REDIS_URL``)."""

    model_config = SettingsConfigDict(env_prefix="LIMITI_RICHIESTE_", case_sensitive=False,
                                      extra="ignore")

    #: false: nessun limite (solo fuori produzione)
    attivi: bool = True
    #: servizio: contatore per ente e servizio; ente: contatore unico del gateway
    ambito: Literal["servizio", "ente"] = "servizio"
    categorie: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {k: dict(v) for k, v in CATEGORIE_PREDEFINITE.items()},
        description="Limiti al minuto per categoria e piano, es. {\"ricerca\": {\"starter\": 30}}")


class _ImpostazioniRedis(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    env: str = "production"
    redis_url: str = ""


def controlla_configurazione(*, env: str, redis_url: str, attivi: bool = True) -> list[str]:
    """Problemi che fermano l'avvio (puro): in produzione i limiti servono e
    richiedono Redis."""
    problemi: list[str] = []
    if env.strip().lower() == "production":
        if not attivi:
            problemi.append("LIMITI_RICHIESTE_ATTIVI=false non è ammesso in produzione")
        elif not redis_url.strip():
            problemi.append("REDIS_URL mancante: i limiti di richieste al minuto non sono "
                            "applicabili (REGOLE §19.4)")
    return problemi


def _valore(identita: Any, nome: str) -> Any:
    if isinstance(identita, Mapping):
        return identita.get(nome)
    return getattr(identita, nome, None)


def _uuid_testo(valore: Any) -> str | None:
    try:
        return str(uuid.UUID(str(valore)))
    except (TypeError, ValueError):
        return None


def soggetto(identita: Any) -> str:
    """A chi si conta la richiesta: l'ente; senza ente (profili globali) l'utente."""
    ente = _uuid_testo(_valore(identita, "tenant_id"))
    if ente is not None:
        return ente
    utente = _uuid_testo(_valore(identita, "user_id")) or str(_valore(identita, "sub") or "")
    return f"utente:{utente or 'anonimo'}"


PianoDi = Callable[[Any, ImpostazioniQuote], Awaitable[str]]
LimitiDi = Callable[[Any, ImpostazioniQuote], Awaitable[LimitiEnte]]


class LimitatoreRichieste:
    """Limite di richieste al minuto per piano di un servizio pubblico."""

    def __init__(
        self,
        servizio: str,
        *,
        redis: Any | None = None,
        impostazioni: ImpostazioniLimiti | None = None,
        quote: ImpostazioniQuote | None = None,
        limiti_di: LimitiDi = limiti_ente,
        piano_di: PianoDi | None = None,
        redis_url: str | None = None,
        env: str | None = None,
    ) -> None:
        self.servizio = servizio
        self.impostazioni = impostazioni or ImpostazioniLimiti()
        # variabili d'ambiente QUOTE_* (come nel gateway)
        self.quote = quote or ImpostazioniQuote()  # type: ignore[call-arg]
        if piano_di is not None:
            # solo piano (test): nessuna quota personalizzata
            async def _da_piano(ente: Any, quote: ImpostazioniQuote) -> LimitiEnte:
                return LimitiEnte(piano=await piano_di(ente, quote))

            limiti_di = _da_piano
        self._limiti_di = limiti_di
        base = _ImpostazioniRedis()
        self._redis_url = base.redis_url if redis_url is None else redis_url
        self._env = base.env if env is None else env
        self._redis = redis
        self._per_ciclo: dict[int, Any] = {}
        problemi = controlla_configurazione(env=self._env, redis_url=self._redis_url
                                            if redis is None else "iniettato",
                                            attivi=self.impostazioni.attivi)
        if problemi:
            raise RuntimeError(f"{servizio}: " + "; ".join(problemi))
        if redis is None and not self._redis_url.strip() and self.impostazioni.attivi:
            logger.warning("limiti_richieste_disattivati", servizio=servizio,
                           motivo="REDIS_URL non impostato (ammesso solo fuori produzione)")

    # ── Redis ────────────────────────────────────────────────────────
    def client_redis(self) -> Any | None:
        """Client Redis del ciclo di eventi corrente (None = limiti non applicati).

        Un client ``redis.asyncio`` è legato al ciclo che lo usa per primo: se ne
        tiene uno per ciclo (in esercizio uno solo; nei test uno per client)."""
        if not self.impostazioni.attivi:
            return None
        if self._redis is not None:
            return self._redis
        if not self._redis_url.strip():
            return None
        import asyncio

        import redis.asyncio as aioredis

        ciclo = id(asyncio.get_running_loop())
        client = self._per_ciclo.get(ciclo)
        if client is None:
            if len(self._per_ciclo) > 16:  # cicli dei test già chiusi
                self._per_ciclo.clear()
            client = aioredis.from_url(self._redis_url, encoding="utf-8", decode_responses=True)
            self._per_ciclo[ciclo] = client
        return client

    def configura(self, *, redis_url: str | None = None, redis: Any | None = None) -> None:
        """Cambia Redis (test; o dopo una riconfigurazione): URL o client iniettato."""
        self._redis = redis
        if redis_url is not None:
            self._redis_url = redis_url
        self._per_ciclo = {}

    async def chiudi(self) -> None:
        client, self._redis = self._redis, None
        clienti = [client, *self._per_ciclo.values()] if client is not None \
            else list(self._per_ciclo.values())
        self._per_ciclo = {}
        for c in clienti:
            if hasattr(c, "aclose"):
                try:
                    await c.aclose()
                except RuntimeError:  # ciclo già chiuso (test)
                    continue

    # ── Chiavi ───────────────────────────────────────────────────────
    def chiave(self, chi: str, categoria: str | None = None) -> str:
        if categoria:
            return f"rl:rpm:{self.servizio}:{categoria}:{chi}"
        if self.impostazioni.ambito == "ente":
            return chiave_richieste(chi)
        return f"rl:rpm:{self.servizio}:{chi}"

    def limite_categoria(self, categoria: str, piano: str) -> int | None:
        """Limite al minuto della categoria per il piano; None = categoria senza limite."""
        tabella = {k.lower(): int(v) for k, v in
                   (self.impostazioni.categorie.get(categoria) or {}).items()}
        if not tabella:
            return None
        chiave = (piano or "").strip().lower()
        if chiave in tabella:
            return tabella[chiave]
        return tabella.get(self.quote.piano_predefinito.lower(), min(tabella.values()))

    # ── Conteggio ────────────────────────────────────────────────────
    async def _limiti(self, identita: Any) -> LimitiEnte:
        ente = _uuid_testo(_valore(identita, "tenant_id"))
        if ente is None:
            return LimitiEnte(piano=self.quote.piano_predefinito)
        return await self._limiti_di(ente, self.quote)

    async def consuma(self, identita: Any, *, categoria: str | None = None,
                      adesso: float | None = None) -> None:
        """Conta la richiesta di ``identita`` (generale, oppure solo della
        ``categoria``); oltre il limite ``ErroreApi`` QTA-001 (429) con
        ``Retry-After``."""
        redis = self.client_redis()
        if redis is None:
            return
        chi = soggetto(identita)
        limiti = await self._limiti(identita)
        piano = limiti.piano
        if categoria is None:
            limite = limiti.richieste_minuto(self.quote)
        else:
            limite_cat = self.limite_categoria(categoria, piano)
            if limite_cat is None:
                return
            limite = limite_cat
        try:
            await consuma_richiesta(redis, self.chiave(chi, categoria), limite=limite,
                                    piano=piano, finestra=self.quote.finestra_secondi,
                                    adesso=adesso)
        except QuotaSuperata as exc:
            logger.info("limite_richieste_superato", servizio=self.servizio, soggetto=chi,
                        categoria=categoria, piano=piano, limite=limite)
            errore = exc.errore_api()
            if categoria is not None and isinstance(errore, ErroreApi):
                # stesso codice del catalogo, con la categoria nei dettagli
                errore.dettagli = {**errore.dettagli, "categoria": categoria}
            raise errore from None

    # ── Dipendenze FastAPI ───────────────────────────────────────────
    def dipendenza(self, autenticazione: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
        """Dipendenza che autentica con ``autenticazione`` (dipendenza del servizio)
        e conta la richiesta; restituisce l'identità."""
        from fastapi import Depends

        async def limite_richieste_al_minuto(identita: Any = Depends(autenticazione)) -> Any:
            await self.consuma(identita)
            return identita

        return limite_richieste_al_minuto

    def dipendenza_categoria(self, autenticazione: Callable[..., Any],
                             categoria: str) -> Callable[..., Awaitable[Any]]:
        """Come ``dipendenza`` ma conta solo il limite della ``categoria`` (da usare
        in aggiunta al limite generale del router)."""
        from fastapi import Depends

        async def limite_categoria(identita: Any = Depends(autenticazione)) -> Any:
            await self.consuma(identita, categoria=categoria)
            return identita

        limite_categoria.__name__ = f"limite_{categoria}"
        return limite_categoria


__all__ = ["CATEGORIE_PREDEFINITE", "ImpostazioniLimiti", "LimitatoreRichieste", "LimitiDi",
           "controlla_configurazione", "soggetto"]
