# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/jwt_rs256.py — chiavi e token RS256 condivisi dai servizi.

Regole applicate (REGOLE_SVILUPPO.md):
  §19.1  ogni token si verifica con firma, emittente (iss), pubblico (aud) e
         scadenza (exp); vietato decodificare un JWT senza verificarne la firma.
  §19.3  le chiavi arrivano SOLO da variabili d'ambiente / secret manager:
         questo modulo non legge mai file, né del repository né del disco.
  §19.4  impostazioni mancanti o pericolose fermano l'avvio con un messaggio chiaro.

Chiavi effimere: solo con ENV=test e senza alcuna chiave configurata si genera
una coppia in memoria (mai scritta su disco), utile ai test automatici. In ogni
altro ambiente (development compreso) la chiave mancante blocca l'avvio.

R04 servizi (VD-C-085) — rotazione senza disconnettere tutti:
  - ``JWT_PUBLIC_KEY`` può contenere PIÙ chiavi pubbliche PEM concatenate (il
    «JWKS interno»): un token firmato con una qualsiasi di esse è valido;
  - ogni chiave ha un ``kid`` = impronta JWK RFC 7638 (SHA-256, base64url),
    calcolato e mai configurato a mano: non si può sbagliare;
  - ``firma_token`` mette nell'intestazione il ``kid`` della chiave privata in
    uso (``JWT_PRIVATE_KEY``, una sola), che deve stare nel mazzo pubblico;
  - ``verifica_token`` sceglie la chiave dal ``kid``; un ``kid`` sconosciuto è
    rifiutato (AUTH-001); un token senza ``kid`` (emesso prima di R04) si prova
    con ciascuna chiave del mazzo;
  - ``jwks(...)`` e ``python -m libs.shared.security.jwt_rs256 --jwks``
    mostrano le chiavi pubbliche in formato JWKS (RFC 7517) con il loro ``kid``.
Procedura: ``docs/runbooks/rotazione_chiavi_jwt.md``.

R05 (VD-C-080): un token verificato da ``verifica_token`` imposta l'ambito
d'ente della Row Level Security per le transazioni successive dello stesso
contesto (richiesta): ruolo del conservatore → tutti gli enti, altrimenti
l'ente del token, senza ente → nessuna riga (``libs.shared.db.ambito``).
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import re
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import jwt
import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

logger = structlog.get_logger(__name__)

ALGORITMO = "RS256"
#: Ambienti in cui, senza chiavi configurate, si genera una coppia effimera.
AMBIENTI_CHIAVI_EFFIMERE: frozenset[str] = frozenset({"test"})
#: Lunghezza minima accettata per le chiavi RSA.
BIT_MINIMI_RSA = 2048
#: Claim che ogni token VerumDoc deve avere, oltre a quelli registrati.
CLAIM_REGISTRATI_OBBLIGATORI = ("exp", "iat", "iss", "aud")

#: Token di servizio verso il servizio di firma (revisione di sicurezza M2):
#: audience SEPARATA da quella dei token utente («verumdoc-api»), quindi un
#: token di servizio trapelato non vale come token utente altrove, e uno
#: utente non vale per firmare.
PUBBLICO_SERVIZIO_FIRMA = "verumdoc-signature"
#: Scope richiesto da /firma e /marca del servizio signature.
SCOPE_FIRMA_SERVIZIO = "firma:servizio"
#: Durata massima di un token di servizio (giorni): va ruotato prima.
DURATA_MASSIMA_TOKEN_SERVIZIO_GIORNI = 90

#: Messaggio al client per ogni token rifiutato che non sia scaduto (B2): il
#: motivo tecnico (firma, audience, emittente, claim) resta nei log.
DETTAGLIO_TOKEN_NON_VALIDO = "Autenticazione non valida: effettuare di nuovo l'accesso."


class ErroreChiaviJwt(RuntimeError):
    """Configurazione delle chiavi JWT assente o non valida: il servizio non parte."""


class ErroreTokenNonValido(Exception):
    """Token rifiutato. `codice` è stabile (AUTH-001 non valido, AUTH-002 scaduto,
    AUTH-003 accesso revocato).

    `dettaglio` è il testo destinato al client (generico); `motivo` è quello
    tecnico, solo per i log (revisione di sicurezza B2).
    """

    def __init__(self, codice: str, titolo: str, dettaglio: str = "", motivo: str = "") -> None:
        super().__init__(f"{codice}: {titolo}")
        self.codice = codice
        self.titolo = titolo
        self.dettaglio = dettaglio
        self.motivo = motivo or dettaglio


@dataclass(frozen=True)
class ChiaviJwt:
    """Chiavi caricate. La privata non compare mai nel repr.

    ``pubblica_pem``: una o più chiavi pubbliche PEM concatenate (mazzo valido
    per la verifica, VD-C-085); ``kid_firma``: ``kid`` della chiave privata."""

    pubblica_pem: str
    privata_pem: str | None = field(default=None, repr=False)
    effimere: bool = False
    kid_firma: str | None = None

    @property
    def kid_validi(self) -> tuple[str, ...]:
        """``kid`` delle chiavi pubbliche accettate in verifica."""
        return tuple(_anello(self.pubblica_pem))


#: Blocco PEM di una chiave pubblica (il mazzo è una concatenazione di blocchi).
_BLOCCO_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.+?-----END [A-Z ]+-----", re.DOTALL)


def blocchi_pem(testo: str) -> list[str]:
    """I blocchi PEM di un mazzo (una chiave per blocco), nell'ordine dato."""
    return [m.group(0).strip() for m in _BLOCCO_PEM.finditer(testo or "")]


def _b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode("ascii")


def _intero_b64url(n: int) -> str:
    return _b64url(n.to_bytes((n.bit_length() + 7) // 8 or 1, "big"))


def jwk_pubblica(chiave: rsa.RSAPublicKey) -> dict[str, str]:
    """JWK RSA pubblica (RFC 7517/7518) con ``kid`` = impronta RFC 7638."""
    numeri = chiave.public_numbers()
    base = {"e": _intero_b64url(numeri.e), "kty": "RSA", "n": _intero_b64url(numeri.n)}
    # RFC 7638 §3: membri obbligatori in ordine lessicografico, senza spazi
    canonico = json.dumps(base, sort_keys=True, separators=(",", ":")).encode("utf-8")
    kid = _b64url(hashlib.sha256(canonico).digest())
    return {**base, "kid": kid, "alg": ALGORITMO, "use": "sig"}


def kid_di(chiave: rsa.RSAPublicKey) -> str:
    """``kid`` di una chiave pubblica RSA (impronta JWK RFC 7638)."""
    return jwk_pubblica(chiave)["kid"]


@functools.lru_cache(maxsize=64)
def _anello(mazzo_pem: str) -> dict[str, rsa.RSAPublicKey]:
    """Chiavi pubbliche del mazzo per ``kid`` (in cache: si analizza una volta)."""
    anello: dict[str, rsa.RSAPublicKey] = {}
    for blocco in blocchi_pem(normalizza_pem(mazzo_pem)) or [normalizza_pem(mazzo_pem)]:
        chiave = serialization.load_pem_public_key(blocco.encode())
        if not isinstance(chiave, rsa.RSAPublicKey):
            raise ValueError("chiave pubblica non RSA")
        anello[kid_di(chiave)] = chiave
    return anello


@functools.lru_cache(maxsize=16)
def _kid_privata(privata_pem: str) -> str:
    chiave = serialization.load_pem_private_key(privata_pem.encode(), password=None)
    if not isinstance(chiave, rsa.RSAPrivateKey):
        raise ErroreChiaviJwt("La chiave privata JWT deve essere RSA.")
    return kid_di(chiave.public_key())


def jwks(mazzo_pem: str) -> dict[str, list[dict[str, str]]]:
    """Il mazzo di chiavi pubbliche come JWKS (RFC 7517 §5), per ispezione e strumenti."""
    return {"keys": [jwk_pubblica(c) for c in _anello(mazzo_pem).values()]}


def normalizza_pem(valore: str | None) -> str:
    """Accetta il PEM su una riga con «\\n» letterali (formato .env / docker-compose)."""
    if not valore:
        return ""
    testo = valore.strip().strip('"').strip("'")
    if "\\n" in testo:
        testo = testo.replace("\\n", "\n")
    return testo.strip()


def _genera_coppia_effimera() -> tuple[str, str]:
    privata = rsa.generate_private_key(public_exponent=65537, key_size=BIT_MINIMI_RSA)
    privata_pem = privata.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pubblica_pem = privata.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pubblica_pem, privata_pem


def _carica_pubblica(pem: str, servizio: str) -> rsa.RSAPublicKey:
    try:
        chiave = serialization.load_pem_public_key(pem.encode())
    except (ValueError, TypeError) as exc:
        raise ErroreChiaviJwt(
            f"[{servizio}] JWT_PUBLIC_KEY non è una chiave pubblica PEM leggibile."
        ) from exc
    if not isinstance(chiave, rsa.RSAPublicKey):
        raise ErroreChiaviJwt(f"[{servizio}] JWT_PUBLIC_KEY deve essere una chiave RSA.")
    if chiave.key_size < BIT_MINIMI_RSA:
        raise ErroreChiaviJwt(
            f"[{servizio}] JWT_PUBLIC_KEY troppo corta ({chiave.key_size} bit, "
            f"minimo {BIT_MINIMI_RSA})."
        )
    return chiave


def _carica_privata(pem: str, servizio: str) -> rsa.RSAPrivateKey:
    try:
        chiave = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise ErroreChiaviJwt(
            f"[{servizio}] JWT_PRIVATE_KEY non è una chiave privata PEM leggibile "
            "(le chiavi protette da password non sono supportate)."
        ) from exc
    if not isinstance(chiave, rsa.RSAPrivateKey):
        raise ErroreChiaviJwt(f"[{servizio}] JWT_PRIVATE_KEY deve essere una chiave RSA.")
    return chiave


def carica_chiavi_jwt(
    *,
    env: str,
    servizio: str,
    chiave_pubblica: str | None,
    chiave_privata: str | None,
    richiede_privata: bool,
) -> ChiaviJwt:
    """
    Carica e valida le chiavi dai valori già letti dall'ambiente (Settings).

    - `richiede_privata=True` per chi firma token (gateway): senza privata non parte.
    - Se c'è solo la privata, la pubblica si ricava da essa.
    - Se ci sono entrambe, devono essere una coppia.
    - Senza chiavi: coppia effimera solo con ENV in AMBIENTI_CHIAVI_EFFIMERE,
      altrimenti ErroreChiaviJwt (il servizio rifiuta di partire).
    """
    ambiente = (env or "").strip().lower()
    pubblica = normalizza_pem(chiave_pubblica)
    privata = normalizza_pem(chiave_privata)

    if not pubblica and not privata and ambiente in AMBIENTI_CHIAVI_EFFIMERE:
        pubblica, privata = _genera_coppia_effimera()
        logger.warning(
            "jwt_chiavi_effimere",
            servizio=servizio,
            nota="Coppia RS256 generata in memoria: valida solo per i test automatici",
        )
        _registra_anello(pubblica)
        return ChiaviJwt(pubblica_pem=pubblica, privata_pem=privata, effimere=True,
                         kid_firma=_kid_privata(privata))

    chiave_priv_obj = _carica_privata(privata, servizio) if privata else None

    if not pubblica and chiave_priv_obj is not None:
        pubblica = chiave_priv_obj.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    if not pubblica:
        raise ErroreChiaviJwt(
            f"[{servizio}] JWT_PUBLIC_KEY mancante (ENV={ambiente or '?'}). "
            "Impostarla come variabile d'ambiente o secret: il servizio non parte "
            "senza la chiave pubblica con cui verificare i token. "
            "In sviluppo locale: bash tools/dev/genera_chiavi_dev.sh."
        )
    if richiede_privata and chiave_priv_obj is None:
        raise ErroreChiaviJwt(
            f"[{servizio}] JWT_PRIVATE_KEY mancante (ENV={ambiente or '?'}). "
            "Questo servizio firma token: impostare la chiave privata come secret. "
            "Il servizio non parte. In sviluppo locale: bash tools/dev/genera_chiavi_dev.sh."
        )

    # VD-C-085: JWT_PUBLIC_KEY può essere un mazzo di più chiavi (rotazione)
    blocchi = blocchi_pem(pubblica) or [pubblica]
    chiavi_pub = [_carica_pubblica(b, servizio) for b in blocchi]
    kid_firma: str | None = None
    if chiave_priv_obj is not None:
        if chiave_priv_obj.key_size < BIT_MINIMI_RSA:
            raise ErroreChiaviJwt(
                f"[{servizio}] JWT_PRIVATE_KEY troppo corta ({chiave_priv_obj.key_size} bit)."
            )
        numeri = chiave_priv_obj.public_key().public_numbers()
        if all(c.public_numbers() != numeri for c in chiavi_pub):
            raise ErroreChiaviJwt(
                f"[{servizio}] JWT_PRIVATE_KEY e JWT_PUBLIC_KEY non sono una coppia "
                "(la pubblica della chiave di firma deve essere nel mazzo JWT_PUBLIC_KEY)."
            )
        kid_firma = kid_di(chiave_priv_obj.public_key())
    if len(chiavi_pub) > 1:
        logger.info("jwt_mazzo_chiavi", servizio=servizio,
                    kid=[kid_di(c) for c in chiavi_pub], kid_firma=kid_firma)

    _registra_anello("\n".join(blocchi))
    return ChiaviJwt(pubblica_pem="\n".join(blocchi), privata_pem=privata or None,
                     effimere=False, kid_firma=kid_firma)


#: R06 (revisione V4): chiavi pubbliche caricate dal processo (``carica_chiavi_jwt``),
#: con cui il middleware della sessione verifica la FIRMA di un token del portale
#: prima di qualunque scrittura sul database (rinnovo della sessione).
_ANELLO_PROCESSO: dict[str, rsa.RSAPublicKey] = {}


def _registra_anello(mazzo_pem: str) -> None:
    try:
        _ANELLO_PROCESSO.update(_anello(mazzo_pem))
    except (ValueError, TypeError):  # pragma: no cover — già validato da carica_chiavi_jwt
        pass


def chiavi_del_processo_presenti() -> bool:
    return bool(_ANELLO_PROCESSO)


def verifica_firma_e_scadenza(token: str, *, tolleranza_secondi: int = 30
                              ) -> dict[str, Any] | None:
    """Payload se FIRMA (chiavi del processo) e scadenza sono valide, altrimenti
    None. Emittente e pubblico li verifica poi la dipendenza di autenticazione
    del servizio (``verifica_token``): qui serve solo a non fare lavoro sul
    database per un token falso o scaduto (REGOLE §19.1: la firma si verifica
    sempre). Nessuna chiave nel processo: ``ErroreChiaviJwt``."""
    if not _ANELLO_PROCESSO:
        raise ErroreChiaviJwt("nessuna chiave pubblica caricata nel processo")
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None
    if kid is None:
        chiavi = list(_ANELLO_PROCESSO.values())
    elif isinstance(kid, str) and kid in _ANELLO_PROCESSO:
        chiavi = [_ANELLO_PROCESSO[kid]]
    else:
        return None
    for chiave in chiavi:
        try:
            dati: dict[str, Any] = jwt.decode(
                token, chiave, algorithms=[ALGORITMO], leeway=tolleranza_secondi,
                options={"verify_aud": False, "verify_iss": False, "require": ["exp"]})
            return dati
        except jwt.InvalidSignatureError:
            continue
        except jwt.InvalidTokenError:
            return None
    return None


def firma_token(
    claims: Mapping[str, Any],
    *,
    chiave_privata_pem: str,
    emittente: str,
    pubblico: str,
    durata_secondi: int,
    adesso: int | None = None,
) -> str:
    """Firma un JWT RS256 con iat, exp, iss, aud e jti sempre presenti."""
    if not chiave_privata_pem:
        raise ErroreChiaviJwt("Chiave privata non disponibile: questo servizio non firma token.")
    ora = int(time.time()) if adesso is None else adesso
    dati: dict[str, Any] = dict(claims)
    dati.update({
        "iat": ora,
        "nbf": ora,
        "exp": ora + int(durata_secondi),
        "iss": emittente,
        "aud": pubblico,
        "jti": dati.get("jti") or str(uuid.uuid4()),
    })
    # VD-C-085: kid della chiave di firma, così la verifica sceglie la chiave giusta
    return jwt.encode(dati, chiave_privata_pem, algorithm=ALGORITMO,
                      headers={"kid": _kid_privata(chiave_privata_pem)})


def verifica_token(
    token: str,
    *,
    chiave_pubblica_pem: str,
    emittente: str,
    pubblico: str,
    claim_obbligatori: Iterable[str] = ("tenant_id", "ruolo"),
    tolleranza_secondi: int = 30,
) -> dict[str, Any]:
    """
    Verifica firma RS256, iss, aud, exp (e iat/nbf) e la presenza dei claim
    applicativi. Restituisce il payload verificato o solleva ErroreTokenNonValido.
    """
    if not token or not isinstance(token, str):
        raise ErroreTokenNonValido("AUTH-001", "Token JWT non valido", "token vuoto")
    candidate = _chiavi_per_token(token, chiave_pubblica_pem)
    try:
        payload = _decodifica(token, candidate, emittente=emittente, pubblico=pubblico,
                              tolleranza_secondi=tolleranza_secondi)
    except jwt.ExpiredSignatureError as exc:
        raise ErroreTokenNonValido(
            "AUTH-002", "Token scaduto", "Il token JWT è scaduto. Effettuare un nuovo login."
        ) from exc
    except jwt.InvalidTokenError as exc:
        logger.info("jwt_rifiutato", motivo=type(exc).__name__, messaggio=str(exc)[:200])
        raise ErroreTokenNonValido(
            "AUTH-001", "Token JWT non valido", DETTAGLIO_TOKEN_NON_VALIDO, motivo=str(exc)
        ) from exc

    for claim in claim_obbligatori:
        valore = payload.get(claim)
        if valore is None or (isinstance(valore, str) and not valore.strip()):
            motivo = f"Claim obbligatorio {claim!r} mancante"
            logger.info("jwt_rifiutato", motivo=motivo)
            raise ErroreTokenNonValido(
                "AUTH-001", "Token JWT non valido", DETTAGLIO_TOKEN_NON_VALIDO, motivo=motivo
            )
    imposta_ambito_da_identita(payload)
    return payload


def imposta_ambito_da_identita(payload: Mapping[str, Any]) -> None:
    """Ambito della Row Level Security dall'identità VERIFICATA (VD-C-080).

    Chiamata solo dopo una verifica riuscita (token, chiave API). Il modulo
    dell'ambito non dipende dal database: vale anche per i servizi senza DB."""
    from libs.shared.security.ambito_dati import imposta_ambito_identita

    imposta_ambito_identita(payload)


def _chiavi_per_token(token: str, mazzo_pem: str) -> list[Any]:
    """Chiavi con cui verificare: quella del ``kid`` dell'intestazione; senza
    ``kid`` (token emessi prima di R04) tutte quelle del mazzo. ``kid``
    sconosciuto o mazzo illeggibile: AUTH-001 (la firma non si verifica con
    chiavi scelte dal token). L'intestazione si legge solo per scegliere la
    chiave: firma e claim si verificano sempre dopo (REGOLE §19.1)."""
    try:
        anello = _anello(mazzo_pem)
        intestazione = jwt.get_unverified_header(token)
    except (ValueError, TypeError, jwt.InvalidTokenError) as exc:
        logger.info("jwt_rifiutato", motivo=type(exc).__name__)
        raise ErroreTokenNonValido("AUTH-001", "Token JWT non valido",
                                   DETTAGLIO_TOKEN_NON_VALIDO, motivo=str(exc)) from exc
    kid = intestazione.get("kid")
    if kid is None:
        return list(anello.values())
    chiave = anello.get(kid) if isinstance(kid, str) else None
    if chiave is None:
        motivo = ("kid sconosciuto: signature di una chiave non (più) nel mazzo "
                  "JWT_PUBLIC_KEY")
        logger.info("jwt_rifiutato", motivo=motivo, kid=str(kid)[:64])
        raise ErroreTokenNonValido("AUTH-001", "Token JWT non valido",
                                   DETTAGLIO_TOKEN_NON_VALIDO, motivo=motivo)
    return [chiave]


def _decodifica(token: str, chiavi: list[Any], *, emittente: str, pubblico: str,
                tolleranza_secondi: int) -> dict[str, Any]:
    """Prova le chiavi indicate; solleva l'errore dell'ultima se nessuna verifica.
    Un errore diverso dalla firma (scadenza, aud, iss) si propaga subito: la firma
    era valida, provare altre chiavi non cambierebbe l'esito."""
    ultimo: jwt.InvalidTokenError | None = None
    for chiave in chiavi:
        try:
            dati: dict[str, Any] = jwt.decode(
                token,
                chiave,
                algorithms=[ALGORITMO],
                audience=pubblico,
                issuer=emittente,
                leeway=tolleranza_secondi,
                options={"require": list(CLAIM_REGISTRATI_OBBLIGATORI)},
            )
            return dati
        except jwt.InvalidSignatureError as exc:
            ultimo = exc
    raise ultimo or jwt.InvalidSignatureError("nessuna chiave nel mazzo")


def firma_token_servizio(
    servizio: str,
    *,
    chiave_privata_pem: str,
    emittente: str,
    durata_secondi: int,
    pubblico: str = PUBBLICO_SERVIZIO_FIRMA,
    scopes: Iterable[str] = (SCOPE_FIRMA_SERVIZIO,),
    adesso: int | None = None,
) -> str:
    """Token di SERVIZIO (es. hashchain → signature): nessun tenant_id né ruolo
    (non passa la verifica dei token utente) e audience dedicata."""
    if not servizio or not servizio.strip():
        raise ValueError("Indicare il servizio chiamante")
    massimo = DURATA_MASSIMA_TOKEN_SERVIZIO_GIORNI * 86400
    if durata_secondi <= 0 or durata_secondi > massimo:
        raise ValueError(f"Durata del token di servizio fra 1 s e "
                         f"{DURATA_MASSIMA_TOKEN_SERVIZIO_GIORNI} giorni")
    return firma_token(
        {"sub": f"servizio:{servizio.strip()}", "servizio": servizio.strip(),
         "scopes": list(scopes)},
        chiave_privata_pem=chiave_privata_pem, emittente=emittente, pubblico=pubblico,
        durata_secondi=durata_secondi, adesso=adesso,
    )


def verifica_token_servizio(
    token: str,
    *,
    chiave_pubblica_pem: str,
    emittente: str,
    pubblico: str = PUBBLICO_SERVIZIO_FIRMA,
    tolleranza_secondi: int = 30,
) -> dict[str, Any]:
    """Verifica un token di servizio (firma, iss, aud dedicata, exp, claim
    `servizio` e `scopes`). Lo scope richiesto lo controlla il chiamante."""
    payload = verifica_token(
        token, chiave_pubblica_pem=chiave_pubblica_pem, emittente=emittente, pubblico=pubblico,
        claim_obbligatori=("servizio",), tolleranza_secondi=tolleranza_secondi,
    )
    if not isinstance(payload.get("scopes"), list):
        logger.info("jwt_rifiutato", motivo="token di servizio senza scopes")
        raise ErroreTokenNonValido("AUTH-001", "Token JWT non valido",
                                   DETTAGLIO_TOKEN_NON_VALIDO, motivo="scopes mancanti")
    return payload


def _main(argomenti: list[str]) -> int:  # pragma: no cover — comando operativo
    """``--jwks``: mostra il mazzo di ``JWT_PUBLIC_KEY`` come JWKS (solo chiavi
    pubbliche e ``kid``); ``--kid``: il ``kid`` della chiave privata di
    ``JWT_PRIVATE_KEY`` (non stampa mai la chiave)."""
    import os
    import sys

    if "--jwks" in argomenti:
        sys.stdout.write(json.dumps(jwks(os.environ.get("JWT_PUBLIC_KEY", "")), indent=2) + "\n")
        return 0
    if "--kid" in argomenti:
        privata = normalizza_pem(os.environ.get("JWT_PRIVATE_KEY", ""))
        if not privata:
            sys.stderr.write("JWT_PRIVATE_KEY non impostata\n")
            return 1
        sys.stdout.write(_kid_privata(privata) + "\n")
        return 0
    sys.stderr.write("Uso: python -m libs.shared.security.jwt_rs256 --jwks | --kid\n")
    return 2


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
