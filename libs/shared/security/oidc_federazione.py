# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/oidc_federazione.py — verifica della trust chain OpenID Federation
dell'OpenID Provider (R13b, VD-C-069).

Riferimenti pubblici: OpenID Federation 1.0 (Entity Configuration in
``/.well-known/openid-federation``, Subordinate Statement dal ``federation_fetch_endpoint``
dell'autorità, firme JWS, ``authority_hints``, catena fino al Trust Anchor); Linee guida
AgID «OpenID Connect in SPID» e regole tecniche CIE OIDC, che adottano la federazione
con il Trust Anchor del Sistema SPID / CIE.

Uso in VerumDoc: FACOLTATIVO. Con l'aggregatore (modello attuale, VD-C-069) l'entità della
federazione è l'aggregatore; si attiva solo se la console indica Trust Anchor, sue chiavi
pubbliche (FISSATE in configurazione, mai scaricate) e identificativo dell'OP. Allora le
chiavi con cui si verificano id_token e userinfo vengono dalla catena verificata, non da un
indirizzo JWKS qualsiasi.

Controlli: ogni Entity Configuration ha ``iss`` = ``sub``; il Subordinate Statement
dell'autorità ha ``iss`` = autorità, ``sub`` = entità, è firmato da una chiave
dell'autorità e porta la ``jwks`` dell'entità, UNICA fonte delle sue chiavi di
federazione: la configurazione dell'entità e le dichiarazioni che emette si verificano con
quelle chiavi, confrontate per thumbprint RFC 7638 (non per solo kid); indirizzi solo
https verso host ammessi; l'autorità di
vertice è il Trust Anchor e la sua configurazione si verifica con le chiavi fissate;
``exp`` e ``iat`` in ogni dichiarazione; al più ``PROFONDITA_MASSIMA`` livelli.

R16 (VD-C-173), federazione completa secondo OpenID Federation 1.0:
  - ``crit``: una dichiarazione che pretende la comprensione di un claim non supportato
    invalida la catena;
  - ``constraints`` di ogni Subordinate Statement (``max_path_length``,
    ``naming_constraints``, ``allowed_entity_types``: ``oidc_politiche``);
  - ``metadata_policy`` di tutti i livelli combinate dal Trust Anchor verso il basso e
    applicate ai metadati della foglia (dopo i ``metadata`` del superiore diretto), con
    ``metadata_policy_crit``;
  - ``trust_marks`` richiesti dalla configurazione (tipo → emittenti ammessi): firma
    dell'emittente verificata con le chiavi della SUA catena verso lo stesso Trust Anchor
    (o con le chiavi fissate se è il Trust Anchor), emittente ammesso anche dal Trust Anchor
    (``trust_mark_issuers``), delega del proprietario se il Trust Anchor lo dichiara
    (``trust_mark_owners``), ``sub`` uguale all'entità, scadenza; stato (revoca) chiesto
    all'endpoint ``federation_trust_mark_status_endpoint`` dell'emittente se configurato;
  - revoca e scadenza delle dichiarazioni: ``exp`` in ogni dichiarazione (anche dei
    marchi), scadenza della catena = la più vicina; un Subordinate Statement ritirato dal
    superiore (risposta diversa da 200) invalida la catena al successivo scaricamento.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import jwt

from libs.shared.security.oidc_politiche import (
    PoliticaNonValida,
    applica,
    combina,
    controlla_vincoli,
)

PROFONDITA_MASSIMA = 3
TIPO_DICHIARAZIONE = "entity-statement+jwt"
ALGORITMI = ["RS256", "PS256", "ES256"]
DIMENSIONE_MASSIMA = 64 * 1024

#: indirizzo → testo della risposta (solo https in produzione: lo controlla il chiamante)
Scarica = Callable[[str], Awaitable[str]]
#: R16: POST di un modulo (indirizzo, campi) → testo della risposta (stato dei trust mark)
Invia = Callable[[str, Mapping[str, str]], Awaitable[str]]
TIPO_TRUST_MARK = "trust-mark+jwt"
TIPO_DELEGA = "trust-mark-delegation+jwt"
TIPO_STATO = "trust-mark-status-response+jwt"
#: claim delle dichiarazioni che il verificatore comprende (``crit``)
CLAIM_COMPRESI = frozenset({
    "iss", "sub", "iat", "exp", "jwks", "metadata", "metadata_policy", "constraints",
    "authority_hints", "trust_marks", "trust_mark_issuers", "trust_mark_owners",
    "metadata_policy_crit", "crit", "source_endpoint", "trust_anchor_hints"})


class CatenaNonValida(ValueError):
    """La trust chain non porta al Trust Anchor configurato."""


@dataclass(frozen=True)
class CatenaVerificata:
    entita: str
    percorso: tuple[str, ...]
    metadati: dict[str, Any]
    jwks: dict[str, Any]
    scade: int
    #: R16: tipi dei trust mark verificati
    trust_marks: tuple[str, ...] = ()


def controlla_crit(claims: Mapping[str, Any]) -> None:
    """R16: ``crit`` elenca i claim che vanno compresi; uno non supportato invalida."""
    crit = claims.get("crit")
    if crit is None:
        return
    if not isinstance(crit, list) or not all(isinstance(c, str) for c in crit):
        raise CatenaNonValida("crit non è un elenco di nomi")
    ignoti = [c for c in crit if c not in CLAIM_COMPRESI]
    if ignoti:
        raise CatenaNonValida(f"claim critici non supportati: {', '.join(ignoti[:5])}")


def _intestazione(token: str) -> dict[str, Any]:
    try:
        return jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise CatenaNonValida("dichiarazione non leggibile") from None


def verifica_firmata(token: str, jwks: Mapping[str, Any], *, adesso: int) -> dict[str, Any]:
    """Claim di una dichiarazione firmata da una chiave di ``jwks`` (scelta per kid)."""
    if not isinstance(token, str) or len(token) > DIMENSIONE_MASSIMA:
        raise CatenaNonValida("dichiarazione assente o troppo grande")
    testa = _intestazione(token)
    if testa.get("alg") not in ALGORITMI:
        raise CatenaNonValida(f"algoritmo {testa.get('alg')!r} non ammesso")
    if str(testa.get("typ") or "").lower() != TIPO_DICHIARAZIONE:
        raise CatenaNonValida("tipo della dichiarazione diverso da entity-statement+jwt")
    kid = testa.get("kid")
    try:
        insieme = jwt.PyJWKSet.from_dict(dict(jwks))
    except jwt.PyJWTError:
        raise CatenaNonValida("chiavi non leggibili") from None
    chiavi = [k for k in insieme.keys if kid is not None and k.key_id == kid]
    if len(chiavi) != 1:
        raise CatenaNonValida("chiave della firma non fra quelle attese")
    try:
        claims: dict[str, Any] = jwt.decode(
            token, chiavi[0].key, algorithms=[testa["alg"]],
            options={"require": ["iss", "sub", "iat", "exp"], "verify_aud": False},
            leeway=30)
    except jwt.ExpiredSignatureError:
        raise CatenaNonValida("dichiarazione scaduta") from None
    except jwt.PyJWTError as exc:
        raise CatenaNonValida(f"firma della dichiarazione non valida: {type(exc).__name__}"
                              ) from None
    if int(claims["iat"]) > adesso + 60:
        raise CatenaNonValida("dichiarazione emessa nel futuro")
    controlla_crit(claims)
    return claims


def _autofirmata(token: str, entita: str, *, adesso: int) -> tuple[dict[str, Any], str]:
    """Entity Configuration: (claim, kid della firma), firmata con la propria jwks."""
    try:
        non_verificati = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        raise CatenaNonValida("configurazione dell'entità non leggibile") from None
    jwks = non_verificati.get("jwks")
    if not isinstance(jwks, dict):
        raise CatenaNonValida("configurazione dell'entità senza jwks")
    claims = verifica_firmata(token, jwks, adesso=adesso)
    if claims["iss"] != entita or claims["sub"] != entita:
        raise CatenaNonValida("configurazione dell'entità con iss/sub diversi dall'entità")
    return claims, str(_intestazione(token).get("kid"))


def _kid_presenti(jwks: Any) -> set[str]:
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        return set()
    return {str(k.get("kid")) for k in jwks["keys"] if isinstance(k, dict) and k.get("kid")}


#: membri obbligatori per tipo di chiave (RFC 7638 §3.2)
_MEMBRI_IMPRONTA = {"RSA": ("e", "kty", "n"), "EC": ("crv", "kty", "x", "y"),
                    "OKP": ("crv", "kty", "x")}


def impronta_jwk(jwk: Mapping[str, Any]) -> str:
    """Thumbprint RFC 7638 (SHA-256, base64url) della chiave pubblica."""
    membri = _MEMBRI_IMPRONTA.get(str(jwk.get("kty")))
    if membri is None or any(not isinstance(jwk.get(m), str) for m in membri):
        raise CatenaNonValida("chiave senza i membri richiesti dal thumbprint RFC 7638")
    canonica = json.dumps({m: jwk[m] for m in membri}, separators=(",", ":"),
                          sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(canonica).digest()).rstrip(b"=").decode()


def _impronte(jwks: Any) -> dict[str, str]:
    """kid → thumbprint delle chiavi di un JWKS (kid ripetuti: rifiutato)."""
    esito: dict[str, str] = {}
    for k in (jwks or {}).get("keys", []) if isinstance(jwks, dict) else []:
        if not isinstance(k, dict) or not k.get("kid"):
            continue
        kid = str(k["kid"])
        if kid in esito:
            raise CatenaNonValida("JWKS con kid ripetuti")
        esito[kid] = impronta_jwk(k)
    return esito


def _lega_configurazione(ec_testo: str, ec: Mapping[str, Any], attestate: Mapping[str, Any],
                         *, adesso: int) -> None:
    """La configurazione dell'entità deve essere firmata con una chiave ATTESTATA dal
    Subordinate Statement del superiore (unica fonte delle chiavi di federazione) e le
    chiavi che dichiara per sé devono avere lo stesso thumbprint RFC 7638 di quelle
    attestate con lo stesso kid (revisione R13b, rilievo 2: il solo kid non basta)."""
    # (a) firma verificata SOLO con le chiavi attestate (scelta per kid, poi la firma
    # lega la chiave intera); (b) thumbprint delle chiavi dichiarate = attestate.
    # Poiché la configurazione è anche autofirmata con la propria jwks, (a) e (b) si
    # coprono a vicenda sulla chiave di firma: difesa doppia voluta.
    verifica_firmata(ec_testo, attestate, adesso=adesso)
    proprie, garantite = _impronte(ec.get("jwks")), _impronte(attestate)
    for k, impronta in proprie.items():
        if k in garantite and garantite[k] != impronta:
            raise CatenaNonValida("chiave dell'entità diversa da quella attestata con lo "
                                  "stesso kid")


def controlla_indirizzo(url: Any, host_ammessi: Collection[str]) -> str:
    """Indirizzo di federazione ammesso: solo https, host nell'elenco (se dato), niente
    credenziali né frammento (revisione R13b, rilievo 4)."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise CatenaNonValida("indirizzo di federazione assente o troppo lungo")
    parti = urlsplit(url)
    if parti.scheme != "https" or not parti.hostname:
        raise CatenaNonValida("indirizzo di federazione non https")
    if parti.username or parti.password or parti.fragment:
        raise CatenaNonValida("indirizzo di federazione con credenziali o frammento")
    elenco = {h.strip().lower() for h in host_ammessi if h and h.strip()}
    if elenco and parti.hostname.lower() not in elenco:
        raise CatenaNonValida(f"host di federazione non ammesso: {parti.hostname}")
    return url


async def risolvi(entita: str, *, trust_anchor: str, chiavi_trust_anchor: Mapping[str, Any],
                  scarica: Scarica, tipo_metadati: str = "openid_provider",
                  adesso: int | None = None,
                  host_ammessi: Collection[str] = (),
                  trust_marks_richiesti: Mapping[str, Collection[str]] | None = None,
                  invia: Invia | None = None,
                  verifica_stato_trust_marks: bool = False) -> CatenaVerificata:
    """Verifica la catena da ``entita`` al Trust Anchor e restituisce i metadati
    (``tipo_metadati``) e le chiavi dell'entità attestate dalla catena.

    A ogni livello le chiavi di federazione di un'entità sono SOLO quelle del Subordinate
    Statement del superiore (per il Trust Anchor: quelle fissate). La configurazione di
    ogni entità e le dichiarazioni che ha emesso verso il basso sono verificate con quelle
    chiavi; il kid da solo non lega nulla (thumbprint RFC 7638)."""
    ora = int(adesso if adesso is not None else time.time())
    if not _kid_presenti(chiavi_trust_anchor):
        raise CatenaNonValida("chiavi del Trust Anchor non configurate")
    controlla_indirizzo(trust_anchor, host_ammessi)

    async def configurazione(soggetto: str) -> str:
        url = controlla_indirizzo(soggetto.rstrip("/") + "/.well-known/openid-federation",
                                  host_ammessi)
        return (await scarica(url)).strip()

    ec_testo = await configurazione(entita)
    ec, _kid = _autofirmata(ec_testo, entita, adesso=ora)
    metadati = (ec.get("metadata") or {}).get(tipo_metadati)
    if not isinstance(metadati, dict):
        raise CatenaNonValida(f"l'entità non dichiara metadati {tipo_metadati}")
    #: R16: Subordinate Statement raccolti dal basso (superiore diretto) verso l'alto
    dichiarazioni: list[dict[str, Any]] = []
    scadenza = int(ec["exp"])
    jwks_entita: dict[str, Any] | None = None
    # livello corrente: soggetto, sua configurazione (testo, claim) e la dichiarazione che
    # ha emesso sul livello sotto (da riverificare con le chiavi attestate dal superiore)
    corrente, corrente_testo, corrente_ec = entita, ec_testo, ec
    emessa_sotto: str | None = None
    percorso = [entita]
    suggerimenti = list(ec.get("authority_hints") or [])
    for _ in range(PROFONDITA_MASSIMA):
        if not suggerimenti:
            raise CatenaNonValida("catena senza authority_hints verso il Trust Anchor")
        ultimo_errore: CatenaNonValida | None = None
        for autorita in suggerimenti[:5]:
            try:
                controlla_indirizzo(autorita, host_ammessi)
                ec_aut_testo = await configurazione(autorita)
                if autorita == trust_anchor:
                    ec_aut = verifica_firmata(ec_aut_testo, chiavi_trust_anchor, adesso=ora)
                    if ec_aut["iss"] != autorita or ec_aut["sub"] != autorita:
                        raise CatenaNonValida("configurazione del Trust Anchor non coerente")
                    jwks_aut: Mapping[str, Any] = dict(chiavi_trust_anchor)
                else:
                    # provvisorie: legate al livello sopra nel giro successivo
                    ec_aut, _k = _autofirmata(ec_aut_testo, autorita, adesso=ora)
                    jwks_aut = ec_aut["jwks"]
                fetch = ((ec_aut.get("metadata") or {}).get("federation_entity") or {}
                         ).get("federation_fetch_endpoint")
                controlla_indirizzo(fetch, host_ammessi)
                ss_testo = (await scarica(f"{fetch}?sub={quote(corrente, safe='')}")).strip()
                ss = verifica_firmata(ss_testo, jwks_aut, adesso=ora)
                if ss["iss"] != autorita or ss["sub"] != corrente:
                    raise CatenaNonValida("dichiarazione dell'autorità con iss/sub inattesi")
                attestate = ss.get("jwks")
                if not _kid_presenti(attestate):
                    raise CatenaNonValida("dichiarazione dell'autorità senza jwks")
                _lega_configurazione(corrente_testo, corrente_ec, attestate, adesso=ora)
                if emessa_sotto is not None:
                    verifica_firmata(emessa_sotto, attestate, adesso=ora)
                if corrente == entita:
                    jwks_entita = dict(attestate)
                scadenza = min(scadenza, int(ss["exp"]), int(ec_aut["exp"]))
                percorso.append(autorita)
                dichiarazioni.append(ss)
                if autorita == trust_anchor:
                    assert jwks_entita is not None
                    finali, scadenza_marchi, tipi = await _completa(
                        entita, ec, dict(metadati), tipo_metadati, dichiarazioni, percorso,
                        ec_ta=ec_aut, trust_anchor=trust_anchor,
                        chiavi_trust_anchor=chiavi_trust_anchor, scarica=scarica, ora=ora,
                        host_ammessi=host_ammessi, richiesti=trust_marks_richiesti or {},
                        invia=invia, verifica_stato=verifica_stato_trust_marks)
                    return CatenaVerificata(entita, tuple(percorso), finali, jwks_entita,
                                            min(scadenza, scadenza_marchi), tipi)
                corrente, corrente_testo, corrente_ec = autorita, ec_aut_testo, ec_aut
                emessa_sotto = ss_testo
                suggerimenti = list(ec_aut.get("authority_hints") or [])
                ultimo_errore = None
                break
            except CatenaNonValida as exc:
                ultimo_errore = exc
        if ultimo_errore is not None:
            raise ultimo_errore
    raise CatenaNonValida("catena più lunga del massimo ammesso")


async def _completa(entita: str, ec: Mapping[str, Any], metadati: dict[str, Any],
                    tipo_metadati: str, dichiarazioni: list[dict[str, Any]],
                    percorso: list[str], *, ec_ta: Mapping[str, Any], trust_anchor: str,
                    chiavi_trust_anchor: Mapping[str, Any], scarica: Scarica, ora: int,
                    host_ammessi: Collection[str], richiesti: Mapping[str, Collection[str]],
                    invia: Invia | None, verifica_stato: bool
                    ) -> tuple[dict[str, Any], int, tuple[str, ...]]:
    """R16: vincoli, politiche dei metadati e trust mark su una catena già verificata."""
    try:
        # vincoli: il Subordinate Statement i-esimo (0 = superiore diretto) è emesso da
        # percorso[i+1]; sotto di lui stanno percorso[0..i]; intermedi fra lui e la foglia = i
        tipi = set((ec.get("metadata") or {}).keys())
        for i, ss in enumerate(dichiarazioni):
            tipi = controlla_vincoli(ss.get("constraints"), livello=i,
                                     sotto=percorso[:i + 1], tipi_foglia=tipi)
        if tipo_metadati not in tipi:
            raise CatenaNonValida(f"tipo d'entità {tipo_metadati} escluso dai vincoli")
        # metadata del superiore diretto, poi politiche combinate dal Trust Anchor in giù
        sovrascritti = ((dichiarazioni[0].get("metadata") or {}).get(tipo_metadati)
                        if dichiarazioni else None)
        if sovrascritti is not None:
            if not isinstance(sovrascritti, Mapping):
                raise CatenaNonValida("metadata del superiore non è un oggetto")
            metadati = {**metadati, **dict(sovrascritti)}
        politiche, critici = [], []
        for ss in reversed(dichiarazioni):
            politica = (ss.get("metadata_policy") or {})
            if not isinstance(politica, Mapping):
                raise CatenaNonValida("metadata_policy non è un oggetto")
            crit = ss.get("metadata_policy_crit") or []
            if not isinstance(crit, list):
                raise CatenaNonValida("metadata_policy_crit non è un elenco")
            critici += [str(c) for c in crit]
            if tipo_metadati in politica:
                politiche.append(politica[tipo_metadati])
        metadati = applica(metadati, combina(politiche, critici))
    except PoliticaNonValida as exc:
        raise CatenaNonValida(f"politica o vincolo della federazione: {exc}") from None
    scadenza, verificati = 2**63 - 1, []
    for tipo, emittenti in sorted(richiesti.items()):
        scade = await _trust_mark(entita, ec, tipo, set(emittenti), ec_ta=ec_ta,
                                  trust_anchor=trust_anchor,
                                  chiavi_trust_anchor=chiavi_trust_anchor, scarica=scarica,
                                  ora=ora, host_ammessi=host_ammessi, invia=invia,
                                  verifica_stato=verifica_stato)
        scadenza = min(scadenza, scade)
        verificati.append(tipo)
    return metadati, scadenza, tuple(verificati)


def _tipo_marchio(claims: Mapping[str, Any]) -> str:
    return str(claims.get("trust_mark_type") or claims.get("id") or "")


def _jwt_firmato(token: str, jwks: Mapping[str, Any], tipo: str, *, ora: int,
                 richiesti: tuple[str, ...] = ("iss", "sub", "iat")) -> dict[str, Any]:
    """Trust mark, delega o risposta di stato: tipo dell'intestazione, algoritmo, chiave
    (per kid fra quelle date), firma, ``exp`` se presente, ``iat`` non nel futuro."""
    if not isinstance(token, str) or len(token) > DIMENSIONE_MASSIMA:
        raise CatenaNonValida(f"{tipo} assente o troppo grande")
    testa = _intestazione(token)
    if testa.get("alg") not in ALGORITMI or str(testa.get("typ") or "").lower() != tipo:
        raise CatenaNonValida(f"{tipo}: intestazione non ammessa")
    try:
        insieme = jwt.PyJWKSet.from_dict(dict(jwks))
    except jwt.PyJWTError:
        raise CatenaNonValida(f"{tipo}: chiavi dell'emittente non leggibili") from None
    chiavi = [k for k in insieme.keys if k.key_id == testa.get("kid")]
    if len(chiavi) != 1:
        raise CatenaNonValida(f"{tipo}: chiave della firma non fra quelle dell'emittente")
    try:
        claims: dict[str, Any] = jwt.decode(token, chiavi[0].key, algorithms=[testa["alg"]],
                                            options={"require": list(richiesti),
                                                     "verify_aud": False}, leeway=30)
    except jwt.ExpiredSignatureError:
        raise CatenaNonValida(f"{tipo} scaduto") from None
    except jwt.PyJWTError as exc:
        raise CatenaNonValida(f"{tipo}: firma non valida ({type(exc).__name__})") from None
    if "iat" in claims and int(claims["iat"]) > ora + 60:
        raise CatenaNonValida(f"{tipo} emesso nel futuro")
    return claims


async def _trust_mark(entita: str, ec: Mapping[str, Any], tipo: str, emittenti: set[str], *,
                      ec_ta: Mapping[str, Any], trust_anchor: str,
                      chiavi_trust_anchor: Mapping[str, Any], scarica: Scarica, ora: int,
                      host_ammessi: Collection[str], invia: Invia | None,
                      verifica_stato: bool) -> int:
    """Un trust mark richiesto: restituisce la sua scadenza (o il massimo se non scade)."""
    voci = ec.get("trust_marks") or []
    if not isinstance(voci, list):
        raise CatenaNonValida("trust_marks non è un elenco")
    ammessi_ta = (ec_ta.get("trust_mark_issuers") or {})
    proprietari = (ec_ta.get("trust_mark_owners") or {})
    if not isinstance(ammessi_ta, Mapping) or not isinstance(proprietari, Mapping):
        raise CatenaNonValida("trust_mark_issuers/owners del Trust Anchor non validi")
    ultimo: CatenaNonValida | None = None
    for voce in voci[:20]:
        if not isinstance(voce, Mapping) or _tipo_marchio(voce) != tipo:
            continue
        token = voce.get("trust_mark")
        try:
            non_verificati = jwt.decode(str(token), options={"verify_signature": False})
        except jwt.PyJWTError:
            ultimo = CatenaNonValida("trust mark non leggibile")
            continue
        emittente = str(non_verificati.get("iss") or "")
        try:
            if emittente not in emittenti:
                raise CatenaNonValida(f"emittente del trust mark {tipo} non ammesso: "
                                      f"{emittente}")
            if tipo in ammessi_ta and emittente not in list(ammessi_ta[tipo] or []):
                raise CatenaNonValida(f"emittente del trust mark {tipo} non autorizzato dal "
                                      "Trust Anchor")
            if emittente == trust_anchor:
                chiavi: Mapping[str, Any] = chiavi_trust_anchor
                metadati_emittente: Mapping[str, Any] = (
                    (ec_ta.get("metadata") or {}).get("federation_entity") or {})
            else:
                catena = await risolvi(emittente, trust_anchor=trust_anchor,
                                       chiavi_trust_anchor=chiavi_trust_anchor,
                                       scarica=scarica, tipo_metadati="federation_entity",
                                       adesso=ora, host_ammessi=host_ammessi)
                chiavi, metadati_emittente = catena.jwks, catena.metadati
            claims = _jwt_firmato(str(token), chiavi, TIPO_TRUST_MARK, ora=ora)
            if claims.get("iss") != emittente or claims.get("sub") != entita \
                    or _tipo_marchio(claims) != tipo:
                raise CatenaNonValida(f"trust mark {tipo} di un'altra entità o di un altro tipo")
            if tipo in proprietari:
                _delega(claims, proprietari[tipo], tipo, emittente, ora=ora)
            if verifica_stato:
                await _stato_trust_mark(str(token), metadati_emittente, chiavi, invia=invia,
                                        host_ammessi=host_ammessi, ora=ora)
            return int(claims["exp"]) if claims.get("exp") is not None else 2**63 - 1
        except CatenaNonValida as exc:
            ultimo = exc
    raise ultimo or CatenaNonValida(f"trust mark richiesto assente: {tipo}")


def _delega(claims: Mapping[str, Any], proprietario: Any, tipo: str, emittente: str, *,
            ora: int) -> None:
    """Trust mark con proprietario dichiarato dal Trust Anchor: delega firmata dal
    proprietario all'emittente (OpenID Federation 1.0, ``trust_mark_owners``)."""
    if not isinstance(proprietario, Mapping) or not isinstance(proprietario.get("jwks"), dict):
        raise CatenaNonValida(f"proprietario del trust mark {tipo} senza chiavi")
    delega = claims.get("delegation")
    if not isinstance(delega, str):
        raise CatenaNonValida(f"trust mark {tipo} senza la delega del proprietario")
    d = _jwt_firmato(delega, proprietario["jwks"], TIPO_DELEGA, ora=ora)
    if d.get("iss") != proprietario.get("sub") or d.get("sub") != emittente \
            or _tipo_marchio(d) != tipo:
        raise CatenaNonValida(f"delega del trust mark {tipo} non coerente")


async def _stato_trust_mark(token: str, metadati_emittente: Mapping[str, Any],
                            chiavi: Mapping[str, Any], *, invia: Invia | None,
                            host_ammessi: Collection[str], ora: int) -> None:
    """Revoca: lo stato del trust mark all'endpoint dell'emittente deve essere attivo."""
    url = (metadati_emittente or {}).get("federation_trust_mark_status_endpoint")
    if not url or invia is None:
        raise CatenaNonValida("stato del trust mark non verificabile (endpoint assente)")
    controlla_indirizzo(url, host_ammessi)
    risposta = (await invia(str(url), {"trust_mark": token})).strip()
    if risposta.count(".") == 2 and not risposta.startswith("{"):
        stato = _jwt_firmato(risposta, chiavi, TIPO_STATO, ora=ora,
                             richiesti=("iss", "iat"))
        attivo = stato.get("status") == "active" and stato.get("trust_mark") in (None, token)
    else:
        try:
            corpo = json.loads(risposta)
        except json.JSONDecodeError:
            raise CatenaNonValida("risposta di stato del trust mark non leggibile") from None
        attivo = isinstance(corpo, dict) and (corpo.get("active") is True
                                              or corpo.get("status") == "active")
    if not attivo:
        raise CatenaNonValida("trust mark revocato, scaduto o non valido per l'emittente")


def chiavi_fissate(testo: Any) -> dict[str, Any]:
    """Chiavi del Trust Anchor dalla configurazione (oggetto JWKS o sua forma JSON)."""
    if isinstance(testo, str):
        try:
            testo = json.loads(testo)
        except json.JSONDecodeError:
            raise CatenaNonValida("chiavi del Trust Anchor non in formato JWKS") from None
    if not isinstance(testo, dict) or not _kid_presenti(testo):
        raise CatenaNonValida("chiavi del Trust Anchor non in formato JWKS (con kid)")
    return testo


__all__ = ["CatenaNonValida", "CatenaVerificata", "chiavi_fissate", "controlla_crit",
           "controlla_indirizzo", "impronta_jwk", "risolvi", "verifica_firmata"]
