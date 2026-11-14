# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/conferme_decisione.py — conferma FIRMATA DAL GATEWAY delle decisioni sensibili
del ciclo di vita (R17, VD-P-164; chiude il residuo di VD-C-120 e VD-C-141).

Perché: un'API di ciclo-vita compromessa che agisce come un utente legittimo poteva costruire
una richiesta coerente in ogni passo (cronologia, audit, dati della pratica: righe scrivibili
dal suo login) e ottenere dal lavoratore uno scarto, un annullamento, una rettifica o un
riversamento in uscita. Ora ogni decisione sensibile porta una CONFERMA che l'API non può
fabbricare:

  1. la console chiede al GATEWAY (``POST /api/v1/auth/conferme-decisione``) di confermare
     la decisione; il gateway pretende una conferma dell'identità RECENTE di quella sessione
     (riautenticazione R12b, o l'accesso stesso se fatto da meno della finestra) e la
     CONSUMA (una conferma dell'identità vale per una sola decisione), poi firma con una
     chiave SOLO SUA (Ed25519, ``kid`` per la rotazione) l'oggetto canonico
     {pratica, azione, versione attesa, impronta del contenuto deciso, utente, ente,
     istante, scadenza breve, nonce};
  2. l'API di ciclo-vita la controlla e la REGISTRA (tabella ``conferme_decisione``, il
     database fissa ``registrata_il`` e la versione della pratica al momento) nella stessa
     transazione della decisione;
  3. il LAVORATORE, prima di eseguire, la verifica con le chiavi PUBBLICHE del gateway lette
     dalla propria configurazione (Secret/ConfigMap, mai dal database scrivibile dall'API):
     firma valida di una chiave nota e non revocata, registrata prima della scadenza, campi
     uguali a quelli ricostruiti dalla pratica (impronta ricalcolata dal contenuto che sta
     per eseguire), versione uguale a quella registrata, nonce mai usato per un'altra
     conferma (registro del solo lavoratore).

Questo modulo è PURO nella parte di confronto (nessun database, nessun orologio: il tempo
arriva come argomento) e contiene la forma canonica condivisa da gateway, API e lavoratore.
La stessa impronta la calcola la console (``frontend/src/console/conferme.ts``): le due
implementazioni sono provate con gli stessi vettori.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# ── Azioni sensibili ──────────────────────────────────────────────────────
#: Scarto: approvazione (→ ESEGUIBILE) ed esecuzione (anche la ripresa dopo le eccezioni)
SCARTO_APPROVA = "SCARTO_APPROVA"
SCARTO_ESEGUI = "SCARTO_ESEGUI"
#: Riversamento verso un altro conservatore: esecuzione
RIVERSAMENTO_USCITA_ESEGUI = "RIVERSAMENTO_USCITA_ESEGUI"
#: Annullamento: richiesta (crea la pratica) e seconda approvazione
ANNULLAMENTO_RICHIEDI = "ANNULLAMENTO_RICHIEDI"
ANNULLAMENTO_APPROVA = "ANNULLAMENTO_APPROVA"
#: Rettifica: richiesta (crea la pratica)
RETTIFICA_RICHIEDI = "RETTIFICA_RICHIEDI"
#: Ripresa di una pratica sospesa dopo i tentativi esauriti (R14)
PRATICA_RIPRENDI = "PRATICA_RIPRENDI"

AZIONI: tuple[str, ...] = (SCARTO_APPROVA, SCARTO_ESEGUI, RIVERSAMENTO_USCITA_ESEGUI,
                           ANNULLAMENTO_RICHIEDI, ANNULLAMENTO_APPROVA, RETTIFICA_RICHIEDI,
                           PRATICA_RIPRENDI)
#: Azioni che CREANO la pratica: nella conferma ``pratica`` è nullo e la versione è 0
AZIONI_CREAZIONE = frozenset({ANNULLAMENTO_RICHIEDI, RETTIFICA_RICHIEDI})
#: Azioni del conservatore (ruoli fissi SUPER_ADMIN e RSC) e dell'ente
AZIONI_CONSERVATORE = frozenset({SCARTO_APPROVA, SCARTO_ESEGUI, RIVERSAMENTO_USCITA_ESEGUI,
                                 PRATICA_RIPRENDI})
AZIONI_ENTE = frozenset({ANNULLAMENTO_RICHIEDI, ANNULLAMENTO_APPROVA, RETTIFICA_RICHIEDI})

#: Durata massima di una conferma (oltre, anche se il gateway la firmasse, si rifiuta)
DURATA_MASSIMA_SECONDI = 900
#: Tolleranza fra gli orologi del gateway e del database
TOLLERANZA_OROLOGIO_SECONDI = 60
VERSIONE_FORMATO = 1

ATTIVO = "ATTIVO"
DISATTIVO = "DISATTIVO"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_KID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CAMPI = ("v", "kid", "azione", "pratica", "versione", "impronta", "utente", "ente", "istante",
          "scadenza", "nonce")


# ── Forma canonica e impronte ─────────────────────────────────────────────

def json_canonico(valore: Any) -> bytes:
    """JSON canonico: chiavi ordinate, nessuno spazio, UTF-8 senza escape dei non ASCII.
    Stessa forma della console (``JSON.stringify`` con chiavi ordinate)."""
    return json.dumps(valore, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def impronta_contenuto(azione: str, campi: Mapping[str, Any]) -> str:
    """SHA-256 esadecimale del contenuto DECISO: ``{"azione": …, **campi}`` canonico.

    Campi per azione (ricostruiti dal lavoratore dai dati che sta per eseguire):
      - SCARTO_APPROVA, SCARTO_ESEGUI, RIVERSAMENTO_USCITA_ESEGUI: ``impronta_piano``;
      - ANNULLAMENTO_RICHIEDI, ANNULLAMENTO_APPROVA: ``pdv_id``, ``motivo`` della richiesta;
      - RETTIFICA_RICHIEDI: ``riferimento`` come indicato dalla persona (``aip:<id>`` o
        ``pdv:<id>``), ``metadati_nuovi``, ``motivo``;
      - PRATICA_RIPRENDI: ``lavoro`` sospeso."""
    if azione not in AZIONI:
        raise ValueError(f"azione sconosciuta: {azione!r}")
    if "azione" in campi:
        raise ValueError("«azione» è riservata")
    return hashlib.sha256(json_canonico({"azione": azione, **campi})).hexdigest()


def campi_impronta(azione: str, pratica: Mapping[str, Any]) -> dict[str, Any]:
    """Campi dell'impronta ricostruiti da una pratica (riga del database)."""
    dati = pratica.get("dati") or {}
    if isinstance(dati, str):
        dati = json.loads(dati)
    if azione in (SCARTO_APPROVA, SCARTO_ESEGUI, RIVERSAMENTO_USCITA_ESEGUI):
        return {"impronta_piano": pratica.get("impronta_piano")}
    if azione in (ANNULLAMENTO_RICHIEDI, ANNULLAMENTO_APPROVA):
        return {"pdv_id": _testo(pratica.get("pdv_id")), "motivo": pratica.get("motivo")}
    if azione == RETTIFICA_RICHIEDI:
        return {"riferimento": dati.get("riferimento"),
                "metadati_nuovi": dati.get("metadati_nuovi"), "motivo": pratica.get("motivo")}
    if azione == PRATICA_RIPRENDI:
        # il lavoro che il lavoratore sta per rimettere in esecuzione
        return {"lavoro": pratica.get("lavoro")}
    raise ValueError(f"azione sconosciuta: {azione!r}")


def _testo(valore: Any) -> str | None:
    return None if valore is None else str(valore)


def riferimento_rettifica(aip_id: Any, pdv_id: Any) -> str:
    """Riferimento della rettifica come lo indica la persona (uno dei due)."""
    return f"aip:{aip_id}" if aip_id else f"pdv:{pdv_id}"


def riferimento_coerente(riferimento: Any, pratica: Mapping[str, Any]) -> bool:
    """Il riferimento registrato indica davvero il PdV (o il suo PdA) della pratica."""
    dati = pratica.get("dati") or {}
    if isinstance(dati, str):
        dati = json.loads(dati)
    if not isinstance(riferimento, str) or ":" not in riferimento:
        return False
    tipo, valore = riferimento.split(":", 1)
    if tipo == "pdv":
        return valore == _testo(pratica.get("pdv_id"))
    if tipo == "aip":
        return valore == _testo(dati.get("aip_id"))
    return False


def b64url(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).rstrip(b"=").decode("ascii")


def da_b64url(testo: str) -> bytes:
    if not isinstance(testo, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", testo):
        raise ValueError("base64url non valido")
    return base64.urlsafe_b64decode(testo + "=" * (-len(testo) % 4))


def _iso(istante: datetime) -> str:
    return istante.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _da_iso(testo: Any) -> datetime:
    if not isinstance(testo, str):
        raise ValueError("istante non valido")
    return datetime.strptime(testo, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


# ── Chiavi ────────────────────────────────────────────────────────────────

def chiave_privata(testo: str) -> Any:
    """Chiave privata Ed25519 del gateway: PEM PKCS#8 o 32 byte in base64 (seme)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    testo = (testo or "").strip()
    if not testo:
        raise ValueError("chiave privata assente")
    if testo.startswith("-----BEGIN"):
        chiave = serialization.load_pem_private_key(testo.encode(), password=None)
        if not isinstance(chiave, Ed25519PrivateKey):
            raise ValueError("la chiave privata non è Ed25519")
        return chiave
    try:
        seme = base64.b64decode(testo, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("chiave privata: né PEM né base64") from None
    if len(seme) != 32:
        raise ValueError("chiave privata: attesi 32 byte")
    return Ed25519PrivateKey.from_private_bytes(seme)


def chiave_pubblica_b64(privata: Any) -> str:
    from cryptography.hazmat.primitives import serialization

    return base64.b64encode(privata.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")


def chiavi_pubbliche(testo: str | Mapping[str, str] | None) -> dict[str, Any]:
    """``{kid: chiave pubblica Ed25519}`` da un JSON ``{"kid": "<32 byte base64>"}`` (o PEM).
    Un elenco malformato è un errore (il servizio non parte), mai un elenco vuoto."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not testo:
        return {}
    grezzo = json.loads(testo) if isinstance(testo, str) else dict(testo)
    if not isinstance(grezzo, dict):
        raise ValueError("chiavi pubbliche: atteso un oggetto {kid: chiave}")
    chiavi: dict[str, Any] = {}
    for kid, valore in grezzo.items():
        if not isinstance(kid, str) or not _KID.match(kid) or not isinstance(valore, str):
            raise ValueError(f"chiavi pubbliche: voce non valida {kid!r}")
        valore = valore.strip()
        if valore.startswith("-----BEGIN"):
            chiave = serialization.load_pem_public_key(valore.encode())
            if not isinstance(chiave, Ed25519PublicKey):
                raise ValueError(f"chiave {kid}: non Ed25519")
        else:
            crudi = base64.b64decode(valore, validate=True)
            if len(crudi) != 32:
                raise ValueError(f"chiave {kid}: attesi 32 byte")
            chiave = Ed25519PublicKey.from_public_bytes(crudi)
        chiavi[kid] = chiave
    return chiavi


def kid_revocati(testo: str | Iterable[str] | None) -> frozenset[str]:
    if not testo:
        return frozenset()
    elenco = json.loads(testo) if isinstance(testo, str) and testo.strip().startswith("[") \
        else (testo.split(",") if isinstance(testo, str) else list(testo))
    return frozenset(str(k).strip() for k in elenco if str(k).strip())


# ── Firma (gateway) ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Richiesta:
    """Ciò che la persona conferma (dal corpo della richiesta al gateway)."""

    azione: str
    pratica: str | None
    versione: int
    impronta: str
    ente: str


def controlla_richiesta(r: Richiesta) -> str | None:
    """None se la richiesta è ben formata, altrimenti il motivo."""
    if r.azione not in AZIONI:
        return "azione non prevista"
    if r.azione in AZIONI_CREAZIONE:
        if r.pratica is not None or r.versione != 0:
            return "una richiesta che crea la pratica non indica pratica né versione"
    elif r.pratica is None or r.versione < 1:
        return "pratica e versione attesa obbligatorie"
    if not _HEX64.match(r.impronta or ""):
        return "impronta del contenuto non valida (SHA-256 esadecimale)"
    return None


def firma(r: Richiesta, *, utente: str, kid: str, privata: Any, adesso: datetime,
          durata_secondi: int, nonce: str) -> dict[str, str]:
    """Oggetto canonico firmato: ``{"payload": b64url, "firma": b64url}``."""
    if not _KID.match(kid):
        raise ValueError("kid non valido")
    if not _NONCE.match(nonce):
        raise ValueError("nonce non valido")
    durata = max(1, min(int(durata_secondi), DURATA_MASSIMA_SECONDI))
    contenuto = {"v": VERSIONE_FORMATO, "kid": kid, "azione": r.azione, "pratica": r.pratica,
                 "versione": int(r.versione), "impronta": r.impronta, "utente": utente,
                 "ente": r.ente, "istante": _iso(adesso),
                 "scadenza": _iso(adesso + timedelta(seconds=durata)), "nonce": nonce}
    crudi = json_canonico(contenuto)
    return {"payload": b64url(crudi), "firma": b64url(privata.sign(crudi))}


# ── Lettura e verifica (API e lavoratore) ─────────────────────────────────

class ConfermaNonValida(ValueError):
    """La conferma è illeggibile (non un difetto di confronto)."""


def leggi(conferma: Mapping[str, Any]) -> tuple[bytes, bytes, dict[str, Any]]:
    """``(payload, firma, campi)`` di una conferma ricevuta; solleva ``ConfermaNonValida``."""
    try:
        crudi = da_b64url(str(conferma.get("payload") or ""))
        firma_crudi = da_b64url(str(conferma.get("firma") or ""))
        campi = json.loads(crudi.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        raise ConfermaNonValida("conferma illeggibile") from None
    if not isinstance(campi, dict) or set(campi) != set(_CAMPI):
        raise ConfermaNonValida("conferma con campi diversi da quelli previsti")
    if json_canonico(campi) != crudi:
        raise ConfermaNonValida("conferma non in forma canonica")
    if len(firma_crudi) != 64:
        raise ConfermaNonValida("firma di lunghezza non valida")
    if not isinstance(campi["nonce"], str) or not _NONCE.match(campi["nonce"]):
        raise ConfermaNonValida("nonce non valido")
    return crudi, firma_crudi, campi


@dataclass(frozen=True)
class Atteso:
    """Ciò che la conferma deve dire, ricostruito da chi verifica (mai dalla conferma)."""

    azione: str
    pratica: str | None
    impronta: str
    utente: str | None
    ente: str


@dataclass(frozen=True)
class Registrazione:
    """Riga ``conferme_decisione`` (campi fissati dal DATABASE)."""

    id: str
    payload: bytes
    firma: bytes
    registrata_il: datetime
    versione_pratica: int


def difetti(reg: Registrazione, atteso: Atteso, *, chiavi: Mapping[str, Any],
            revocati: frozenset[str], usata_da: str | None = None) -> list[str]:
    """Difetti della conferma registrata rispetto all'atteso (vuoto = valida).

    ``usata_da``: id della conferma che ha già consumato lo stesso nonce (registro del
    lavoratore), ``None`` se nessuna. Controlli: forma; firma Ed25519 della chiave ``kid``
    nota e non revocata sui byte registrati; azione, pratica, ente, utente, impronta uguali
    all'atteso; versione uguale a quella che il database ha fissato alla registrazione;
    registrata entro la scadenza (e non prima dell'istante, salvo la tolleranza); durata non
    oltre il massimo; nonce non già consumato da un'altra conferma."""
    from cryptography.exceptions import InvalidSignature

    try:
        _, _, campi = leggi({"payload": b64url(reg.payload), "firma": b64url(reg.firma)})
    except ConfermaNonValida as exc:
        return [f"CONFERMA_ILLEGGIBILE: {exc}"]
    errori: list[str] = []
    kid = str(campi["kid"])
    chiave = chiavi.get(kid)
    if kid in revocati:
        errori.append(f"CONFERMA_CHIAVE_REVOCATA: chiave {kid} revocata")
    elif chiave is None:
        errori.append(f"CONFERMA_CHIAVE_IGNOTA: chiave {kid} non configurata")
    else:
        try:
            chiave.verify(reg.firma, reg.payload)
        except InvalidSignature:
            errori.append("CONFERMA_FIRMA_NON_VALIDA: la firma del gateway non corrisponde")
    if campi["v"] != VERSIONE_FORMATO:
        errori.append("CONFERMA_FORMATO: versione del formato non prevista")
    if campi["azione"] != atteso.azione:
        errori.append(f"CONFERMA_AZIONE_DIVERSA: {campi['azione']} invece di {atteso.azione}")
    if campi["pratica"] != atteso.pratica:
        errori.append("CONFERMA_PRATICA_DIVERSA: la conferma riguarda un'altra pratica")
    if campi["ente"] != atteso.ente:
        errori.append("CONFERMA_ENTE_DIVERSO: la conferma riguarda un altro ente")
    if atteso.utente is None or campi["utente"] != atteso.utente:
        errori.append("CONFERMA_UTENTE_DIVERSO: non l'ha confermata chi ha deciso")
    if campi["impronta"] != atteso.impronta:
        errori.append("CONFERMA_CONTENUTO_DIVERSO: il contenuto da eseguire non è quello "
                      "confermato")
    if not isinstance(campi["versione"], int) or campi["versione"] != reg.versione_pratica:
        errori.append(f"CONFERMA_VERSIONE_DIVERSA: confermata la versione {campi['versione']}, "
                      f"registrata alla {reg.versione_pratica}")
    try:
        istante, scadenza = _da_iso(campi["istante"]), _da_iso(campi["scadenza"])
    except ValueError:
        errori.append("CONFERMA_ISTANTI: istante o scadenza illeggibili")
    else:
        tolleranza = timedelta(seconds=TOLLERANZA_OROLOGIO_SECONDI)
        if scadenza - istante > timedelta(seconds=DURATA_MASSIMA_SECONDI) or scadenza <= istante:
            errori.append("CONFERMA_DURATA: scadenza oltre la durata massima")
        if reg.registrata_il > scadenza:
            errori.append("CONFERMA_SCADUTA: registrata dopo la scadenza")
        if reg.registrata_il + tolleranza < istante:
            errori.append("CONFERMA_ISTANTE_FUTURO: registrata prima di essere firmata")
    if usata_da is not None and usata_da != reg.id:
        errori.append("CONFERMA_RIUSATA: il nonce è già stato usato da un'altra conferma")
    return errori


def nonce_di(payload: bytes) -> str:
    """Nonce di un payload registrato (per il registro del lavoratore)."""
    return str(json.loads(payload.decode("utf-8"))["nonce"])


__all__ = [
    "ANNULLAMENTO_APPROVA", "ANNULLAMENTO_RICHIEDI", "ATTIVO", "AZIONI", "AZIONI_CONSERVATORE",
    "AZIONI_CREAZIONE", "AZIONI_ENTE", "Atteso", "ConfermaNonValida", "DISATTIVO",
    "DURATA_MASSIMA_SECONDI", "PRATICA_RIPRENDI", "RETTIFICA_RICHIEDI",
    "RIVERSAMENTO_USCITA_ESEGUI", "Registrazione", "Richiesta", "SCARTO_APPROVA",
    "SCARTO_ESEGUI", "b64url", "campi_impronta", "chiave_privata", "chiave_pubblica_b64",
    "chiavi_pubbliche", "controlla_richiesta", "da_b64url", "difetti", "firma",
    "impronta_contenuto", "json_canonico", "kid_revocati", "leggi", "nonce_di",
    "riferimento_coerente", "riferimento_rettifica",
]
