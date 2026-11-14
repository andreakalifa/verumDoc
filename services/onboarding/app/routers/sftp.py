# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Onboarding — account SFTP degli enti (canale di versamento sftp-ingest, piano R03 §3.2).

Chi: l'amministratore dell'ente (ADMIN_TENANT o RSC_CONSERVATORE, solo il
proprio ente; un altro ente risponde 404) e il SUPER_ADMIN; stessa dipendenza
``Chiamante`` degli utenti e delle chiavi API (REGOLE §14.9).

Cosa:
  - crea un account (nome di accesso unico fra tutti gli enti) con l'utente
    responsabile, scelto con le STESSE regole del responsabile di una chiave API
    (``utenti._responsabile_chiave``: utente attivo dell'ente, ruolo non superiore
    a chi lo indica; se assente, chi crea l'account);
  - modifica descrizione e responsabile, sospende e riattiva (mai elimina);
  - aggiunge chiavi pubbliche SSH (solo i tipi e le lunghezze ammessi; impronta
    SHA256 mostrata come ``ssh-keygen -l``) e le revoca (mai elimina).
Ogni operazione scrive una riga di ``audit_global`` nella stessa transazione
(se l'audit non si scrive, l'operazione non avviene).

Politica delle chiavi: variabili d'ambiente SFTP_TIPI_CHIAVE_AMMESSI e
SFTP_RSA_BIT_MINIMI, le stesse che sftp-ingest ricontrolla a ogni accesso.
Errori dal catalogo (famiglia SFTP, più AUTH-010 e ONB-404 delle regole comuni).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import struct
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth.jwt_handler import verify_access_token
from app.routers.utenti import Chiamante, ChiaveApiCreate, _responsabile_chiave
from libs.shared.db import get_async_session
from libs.shared.elenchi_ambiente import elenco_da_ambiente
from libs.shared.errori import RISPOSTE_ERRORE, ErroreApi, errore_registrato
from libs.shared.models import AuditGlobal, SftpAccount, SftpChiave, Tenant, User
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/onboarding/tenant/{tenant_id}/sftp/account",
    tags=["Account SFTP"],
    dependencies=[Depends(dipendenza_non_revocato(verify_access_token))],
    responses=RISPOSTE_ERRORE,
)


class ImpostazioniSftp(BaseSettings):
    """Politica delle chiavi e indirizzo pubblico del server (da ambiente)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")
    sftp_tipi_chiave_ammessi: Annotated[list[str], NoDecode] = [
        "ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521", "ssh-rsa",
    ]
    sftp_rsa_bit_minimi: int = 3072
    #: Indirizzo e porta che i clienti usano per collegarsi (mostrati nell'account).
    sftp_host_pubblico: str = ""
    sftp_porta_pubblica: int = 22

    @field_validator("sftp_tipi_chiave_ammessi", mode="before")
    @classmethod
    def _elenco(cls, valore: object) -> object:
        # Stesso formato di sftp-ingest (R03 i1): virgole; array JSON per compatibilità
        return elenco_da_ambiente(valore)


impostazioni = ImpostazioniSftp()


# ── Chiavi pubbliche SSH (funzioni pure) ─────────────────────────────

@dataclass(frozen=True)
class ChiaveAnalizzata:
    tipo: str
    chiave: str          # «tipo base64», senza commento
    bit: int | None
    impronta: str        # «SHA256:<base64 senza =>», come ssh-keygen -l
    commento: str | None


def _bit(pubblica: object) -> int | None:
    if isinstance(pubblica, rsa.RSAPublicKey | dsa.DSAPublicKey):
        return pubblica.key_size
    if isinstance(pubblica, ec.EllipticCurvePublicKey):
        return pubblica.curve.key_size
    if isinstance(pubblica, ed25519.Ed25519PublicKey):
        return 256
    return None


def analizza_chiave_pubblica(testo: str, *, tipi_ammessi: list[str],
                             rsa_bit_minimi: int) -> ChiaveAnalizzata:
    """Valida una riga OpenSSH «tipo base64 [commento]» e ne calcola l'impronta.

    SFTP-001 se il testo non è una chiave valida (o il tipo dichiarato non
    corrisponde al contenuto); SFTP-002 se tipo o lunghezza non sono ammessi."""
    righe = [r for r in testo.strip().splitlines() if r.strip()]
    parti = righe[0].split() if len(righe) == 1 else []
    if len(parti) < 2:
        raise ErroreApi("SFTP-001", "Attesa una sola riga «tipo base64 [commento]»")
    tipo, b64 = parti[0], parti[1]
    try:
        blob = base64.b64decode(b64, validate=True)
        (lunghezza,) = struct.unpack(">I", blob[:4])
        tipo_interno = blob[4:4 + lunghezza].decode("ascii")
    except (binascii.Error, struct.error, UnicodeDecodeError, ValueError) as exc:
        raise ErroreApi("SFTP-001", "Contenuto base64 della chiave non valido") from exc
    if tipo_interno != tipo:
        raise ErroreApi("SFTP-001", f"Il tipo dichiarato {tipo!r} non corrisponde al "
                                    f"contenuto ({tipo_interno!r})")
    if tipo not in tipi_ammessi:
        raise ErroreApi("SFTP-002", f"Tipo di chiave {tipo!r} non ammesso",
                        dettagli={"ammessi": list(tipi_ammessi)})
    try:
        pubblica = serialization.load_ssh_public_key(f"{tipo} {b64}".encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ErroreApi("SFTP-001", "Chiave pubblica non leggibile") from exc
    bit = _bit(pubblica)
    if tipo == "ssh-rsa" and (bit is None or bit < rsa_bit_minimi):
        raise ErroreApi("SFTP-002", f"Chiave RSA di {bit} bit: ne servono almeno "
                                    f"{rsa_bit_minimi}",
                        dettagli={"rsa_bit_minimi": rsa_bit_minimi})
    impronta = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    commento = " ".join(parti[2:])[:200] or None
    return ChiaveAnalizzata(tipo, f"{tipo} {b64}", bit, impronta, commento)


# ── Modelli ─────────────────────────────────────────────────────────

_PATTERN_NOME_UTENTE = r"^[a-z0-9][a-z0-9._-]{2,63}$"


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome_utente: str = Field(..., pattern=_PATTERN_NOME_UTENTE,
                             description="Nome di accesso SSH: 3-64 caratteri minuscoli, cifre, "
                                         "«.», «_», «-»; unico fra tutti gli enti")
    descrizione: str = Field("", max_length=200,
                             description="Sistema che userà l'account (es. protocollo)")
    user_id: uuid.UUID | None = Field(
        None, description="Utente dell'ente per conto del quale il canale versa; se assente, "
                          "chi crea l'account (stesse regole delle chiavi API)")


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    descrizione: str | None = Field(None, max_length=200)
    user_id: uuid.UUID | None = Field(None, description="Nuovo utente responsabile")


class ChiaveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chiave_pubblica: str = Field(..., min_length=20, max_length=16384,
                                 description="Riga del file .pub (es. «ssh-ed25519 AAAA… nome»)")


# ── Funzioni di supporto ─────────────────────────────────────────────

def _uuid_o_404(valore: str, codice: str = "SFTP-404",
                titolo: str = "Account o chiave SFTP non trovati") -> uuid.UUID:
    try:
        return uuid.UUID(str(valore))
    except ValueError as exc:
        raise ErroreApi(codice, titolo) from exc


def _uuid_o_none(valore: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valore))
    except (TypeError, ValueError):
        return None


def _iso(valore: datetime | None) -> str | None:
    return valore.isoformat() if valore else None


def _audit(session: Any, evento: str, tid: uuid.UUID, account_id: uuid.UUID,
           caller: dict[str, Any], **dettagli: Any) -> None:
    session.add(AuditGlobal(
        id=uuid.uuid4(), tenant_id=tid, user_id=_uuid_o_none(caller.get("user_id")),
        evento=evento,
        dettagli={"account_sftp_id": str(account_id), "ruolo_chiamante": caller.get("ruolo"),
                  **dettagli},
    ))


def _errore_db(evento: str, exc: Exception, **contesto: Any) -> ErroreApi:
    return errore_registrato("SFTP-503", exc, evento=evento, **contesto)


def _chiave_json(k: SftpChiave) -> dict[str, Any]:
    return {"id": str(k.id), "algoritmo": k.algoritmo, "bit": k.bit, "impronta": k.impronta,
            "commento": k.commento, "attiva": k.revocata_il is None,
            "created_at": _iso(k.created_at), "revocata_il": _iso(k.revocata_il)}


def _account_json(a: SftpAccount, responsabile: User | None,
                  chiavi: list[SftpChiave]) -> dict[str, Any]:
    connessione = {"protocollo": "sftp", "utente": a.nome_utente,
                   "host": impostazioni.sftp_host_pubblico or None,
                   "porta": impostazioni.sftp_porta_pubblica}
    return {
        "id": str(a.id), "nome_utente": a.nome_utente, "descrizione": a.descrizione,
        "stato": a.stato, "sospeso_il": _iso(a.sospeso_il),
        "responsabile": None if responsabile is None else {
            "id": str(responsabile.id), "email": responsabile.email,
            "nome": responsabile.nome, "cognome": responsabile.cognome,
            "ruolo": getattr(responsabile.ruolo, "value", responsabile.ruolo),
            "attivo": responsabile.attivo},
        "chiavi": [_chiave_json(k) for k in chiavi],
        # R10 (D12): indirizzi o reti da cui l'account si collega (vuoto = ogni indirizzo,
        # salvo SFTP_ALLOWLIST_OBBLIGATORIA dell'ente)
        "ip_ammessi": list(a.ip_ammessi or []),
        "connessione": connessione,
        "created_at": _iso(a.created_at),
        "azioni_consentite": {
            "sospendi": a.stato == "ATTIVO", "riattiva": a.stato == "SOSPESO",
            "aggiungi_chiave": True, "modifica": True, "ip_ammessi": True,
        },
    }


async def _account_dell_ente(session: Any, tid: uuid.UUID, account_id: str, *,
                             blocca: bool = False) -> SftpAccount:
    aid = _uuid_o_404(account_id)
    query = select(SftpAccount).where(SftpAccount.id == aid, SftpAccount.tenant_id == tid)
    if blocca:
        query = query.with_for_update()
    account = (await session.execute(query)).scalar_one_or_none()
    if account is None:  # anche se esiste in un altro ente (§14.4, §14.6)
        raise ErroreApi("SFTP-404", "Account SFTP non trovato in questo ente")
    return account


async def _dettaglio(session: Any, account: SftpAccount) -> dict[str, Any]:
    chiavi = (await session.execute(
        select(SftpChiave).where(SftpChiave.account_id == account.id)
        .order_by(SftpChiave.created_at, SftpChiave.id))).scalars().all()
    responsabile = await session.get(User, account.responsabile_id)
    return _account_json(account, responsabile, list(chiavi))


def _tenant_o_404(tenant_id: str) -> uuid.UUID:
    return _uuid_o_404(tenant_id, "ONB-404", "Tenant non trovato")


# ── Endpoint ─────────────────────────────────────────────────────────

@router.get("", summary="Account SFTP dell'ente")
async def lista_account(tenant_id: str, caller: Chiamante) -> dict[str, Any]:
    tid = _tenant_o_404(tenant_id)
    try:
        async with get_async_session() as session:
            accounts = (await session.execute(
                select(SftpAccount).where(SftpAccount.tenant_id == tid)
                .order_by(SftpAccount.nome_utente))).scalars().all()
            ids = [a.id for a in accounts]
            chiavi: dict[uuid.UUID, list[SftpChiave]] = {i: [] for i in ids}
            responsabili: dict[uuid.UUID, User] = {}
            if ids:  # due query in tutto, qualunque sia il numero di account (§23.9)
                for k in (await session.execute(
                        select(SftpChiave).where(SftpChiave.account_id.in_(ids))
                        .order_by(SftpChiave.created_at, SftpChiave.id))).scalars():
                    chiavi[k.account_id].append(k)
                for u in (await session.execute(
                        select(User).where(User.id.in_({a.responsabile_id for a in accounts}))
                )).scalars():
                    responsabili[u.id] = u
            items = [_account_json(a, responsabili.get(a.responsabile_id), chiavi[a.id])
                     for a in accounts]
    except SQLAlchemyError as exc:
        raise _errore_db("sftp_lista_account_errore", exc, tenant_id=tenant_id) from exc
    return {"total": len(items), "items": items}


@router.post("", status_code=201, summary="Crea un account SFTP")
async def crea_account(tenant_id: str, body: AccountCreate, caller: Chiamante) -> dict[str, Any]:
    """201 con l'account (senza chiavi: si aggiungono con POST …/chiavi); 409
    SFTP-004 se il nome utente è già usato (in qualunque ente, senza dire quale)."""
    tid = _tenant_o_404(tenant_id)
    try:
        async with get_async_session() as session:
            if await session.get(Tenant, tid) is None:
                raise ErroreApi("ONB-404", "Tenant non trovato")
            responsabile = await _responsabile_chiave(
                session, tid, ChiaveApiCreate(user_id=body.user_id), caller)
            account = SftpAccount(
                id=uuid.uuid4(), tenant_id=tid, nome_utente=body.nome_utente,
                descrizione=body.descrizione.strip(), responsabile_id=responsabile,
                stato="ATTIVO", creato_da=_uuid_o_none(caller.get("user_id")))
            session.add(account)
            await session.flush()
            await session.refresh(account)
            _audit(session, "sftp.account.creato", tid, account.id, caller,
                   nome_utente=account.nome_utente, responsabile_id=str(responsabile))
            risposta = await _dettaglio(session, account)
    except IntegrityError as exc:
        if "uq_sftp_account_nome_utente" in str(getattr(exc, "orig", exc)):
            raise ErroreApi("SFTP-004", "Nome utente SFTP non disponibile") from exc
        raise _errore_db("sftp_crea_account_errore", exc, tenant_id=tenant_id) from exc
    except SQLAlchemyError as exc:
        raise _errore_db("sftp_crea_account_errore", exc, tenant_id=tenant_id) from exc
    logger.info("sftp_account_creato", tenant_id=tenant_id, account_id=risposta["id"],
                creato_da=caller.get("user_id"))
    return risposta


@router.get("/{account_id}", summary="Dettaglio di un account SFTP")
async def dettaglio_account(tenant_id: str, account_id: str,
                            caller: Chiamante) -> dict[str, Any]:
    tid = _tenant_o_404(tenant_id)
    try:
        async with get_async_session() as session:
            account = await _account_dell_ente(session, tid, account_id)
            return await _dettaglio(session, account)
    except SQLAlchemyError as exc:
        raise _errore_db("sftp_dettaglio_errore", exc, tenant_id=tenant_id) from exc


@router.put("/{account_id}", summary="Modifica descrizione o responsabile")
async def modifica_account(tenant_id: str, account_id: str, body: AccountUpdate,
                           caller: Chiamante) -> dict[str, Any]:
    tid = _tenant_o_404(tenant_id)
    campi = body.model_dump(exclude_unset=True)
    if not campi or all(v is None for v in campi.values()):
        raise ErroreApi("ONB-400", "Nessun campo da aggiornare")
    try:
        async with get_async_session() as session:
            account = await _account_dell_ente(session, tid, account_id, blocca=True)
            precedente = {"descrizione": account.descrizione,
                          "responsabile_id": str(account.responsabile_id)}
            if body.user_id is not None:
                account.responsabile_id = await _responsabile_chiave(
                    session, tid, ChiaveApiCreate(user_id=body.user_id), caller)
            if body.descrizione is not None:
                account.descrizione = body.descrizione.strip()
            await session.flush()
            _audit(session, "sftp.account.modificato", tid, account.id, caller,
                   precedente=precedente,
                   nuovo={"descrizione": account.descrizione,
                          "responsabile_id": str(account.responsabile_id)})
            return await _dettaglio(session, account)
    except SQLAlchemyError as exc:
        raise _errore_db("sftp_modifica_errore", exc, tenant_id=tenant_id) from exc


class IpAmmessi(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ip_ammessi: list[str] = Field(default_factory=list, max_length=500,
                                  description="Indirizzi o reti CIDR (vuoto = nessun filtro)")


@router.put("/{account_id}/ip-ammessi", summary="Indirizzi da cui l'account si collega")
async def imposta_ip_ammessi(tenant_id: str, account_id: str, body: IpAmmessi,
                             caller: Chiamante) -> dict[str, Any]:
    """R10 (D12): elenco impostato dall'ente, entro i limiti del conservatore
    (``SFTP_ALLOWLIST_VOCI_MASSIME``, prefisso minimo IPv4/IPv6). Il server SFTP lo
    applica all'IP reale del cliente (PROXY protocol del bilanciatore)."""
    from libs.shared.parametri import parametri
    from libs.shared.security.rete import ElencoNonValido, LimitiElenco, normalizza_elenco
    from libs.verumdoc_rules.parametri_servizio import (
        SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4,
        SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV6,
        SFTP_ALLOWLIST_VOCI_MASSIME,
    )

    tid = _tenant_o_404(tenant_id)
    limiti = await parametri([SFTP_ALLOWLIST_VOCI_MASSIME, SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4,
                              SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV6], tid)
    try:
        elenco = normalizza_elenco(body.ip_ammessi, LimitiElenco(
            int(limiti[SFTP_ALLOWLIST_VOCI_MASSIME]),
            int(limiti[SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV4]),
            int(limiti[SFTP_ALLOWLIST_PREFISSO_MINIMO_IPV6])))
    except ElencoNonValido as exc:
        raise ErroreApi("SFTP-422-IP", str(exc), dettagli={"motivi": exc.motivi}) from None
    try:
        async with get_async_session() as session:
            account = await _account_dell_ente(session, tid, account_id, blocca=True)
            precedente = list(account.ip_ammessi or [])
            account.ip_ammessi = elenco
            await session.flush()
            _audit(session, "sftp.account.ip_ammessi", tid, account.id, caller,
                   precedente=precedente, nuovo=elenco)
            return await _dettaglio(session, account)
    except SQLAlchemyError as exc:
        raise _errore_db("sftp_ip_ammessi_errore", exc, tenant_id=tenant_id) from exc


async def _cambia_stato(tenant_id: str, account_id: str, caller: dict[str, Any],
                        nuovo: str) -> dict[str, Any]:
    tid = _tenant_o_404(tenant_id)
    try:
        async with get_async_session() as session:
            account = await _account_dell_ente(session, tid, account_id, blocca=True)
            if account.stato != nuovo:
                if nuovo == "ATTIVO":
                    responsabile = await session.get(User, account.responsabile_id)
                    if (responsabile is None or not responsabile.attivo
                            or responsabile.tenant_id != tid):
                        raise ErroreApi("SFTP-409", "L'utente responsabile non è più attivo: "
                                                    "indicarne un altro prima di riattivare")
                precedente = account.stato
                account.stato = nuovo
                account.sospeso_il = datetime.now(UTC) if nuovo == "SOSPESO" else None
                await session.flush()
                _audit(session, "sftp.account.sospeso" if nuovo == "SOSPESO"
                       else "sftp.account.riattivato", tid, account.id, caller,
                       precedente=precedente, nuovo=nuovo)
            return await _dettaglio(session, account)
    except SQLAlchemyError as exc:
        raise _errore_db("sftp_stato_errore", exc, tenant_id=tenant_id) from exc


@router.post("/{account_id}/sospendi", summary="Sospende l'account (accesso e lavorazione)")
async def sospendi_account(tenant_id: str, account_id: str, caller: Chiamante) -> dict[str, Any]:
    """Idempotente: un account già sospeso resta tale (nessuna nuova voce di audit).
    Da subito: nuovi accessi negati e cartelle non più lavorate."""
    return await _cambia_stato(tenant_id, account_id, caller, "SOSPESO")


@router.post("/{account_id}/riattiva", summary="Riattiva un account sospeso")
async def riattiva_account(tenant_id: str, account_id: str, caller: Chiamante) -> dict[str, Any]:
    return await _cambia_stato(tenant_id, account_id, caller, "ATTIVO")


@router.post("/{account_id}/chiavi", status_code=201, summary="Aggiunge una chiave pubblica SSH")
async def aggiungi_chiave(tenant_id: str, account_id: str, body: ChiaveCreate,
                          caller: Chiamante) -> dict[str, Any]:
    """201 con impronta («SHA256:…»), tipo e lunghezza; 422 SFTP-001/002 se la
    chiave non è valida o ammessa; 409 SFTP-003 se è già attiva sull'account."""
    tid = _tenant_o_404(tenant_id)
    analizzata = analizza_chiave_pubblica(
        body.chiave_pubblica, tipi_ammessi=impostazioni.sftp_tipi_chiave_ammessi,
        rsa_bit_minimi=impostazioni.sftp_rsa_bit_minimi)
    try:
        async with get_async_session() as session:
            account = await _account_dell_ente(session, tid, account_id, blocca=True)
            chiave = SftpChiave(
                id=uuid.uuid4(), account_id=account.id, tenant_id=tid,
                algoritmo=analizzata.tipo, bit=analizzata.bit,
                chiave_pubblica=analizzata.chiave, impronta=analizzata.impronta,
                commento=analizzata.commento, creata_da=_uuid_o_none(caller.get("user_id")))
            session.add(chiave)
            await session.flush()
            await session.refresh(chiave)
            _audit(session, "sftp.chiave.aggiunta", tid, account.id, caller,
                   chiave_id=str(chiave.id), impronta=chiave.impronta, algoritmo=chiave.algoritmo)
            risposta = _chiave_json(chiave)
    except IntegrityError as exc:
        if "uq_sftp_chiavi_attiva" in str(getattr(exc, "orig", exc)):
            raise ErroreApi("SFTP-003", "Chiave già registrata per l'account",
                            dettagli={"impronta": analizzata.impronta}) from exc
        raise _errore_db("sftp_aggiungi_chiave_errore", exc, tenant_id=tenant_id) from exc
    except SQLAlchemyError as exc:
        raise _errore_db("sftp_aggiungi_chiave_errore", exc, tenant_id=tenant_id) from exc
    logger.info("sftp_chiave_aggiunta", tenant_id=tenant_id, account_id=account_id,
                impronta=analizzata.impronta)
    return risposta


@router.delete("/{account_id}/chiavi/{chiave_id}", status_code=204, response_class=Response,
               response_model=None, summary="Revoca una chiave pubblica SSH")
async def revoca_chiave(tenant_id: str, account_id: str, chiave_id: str,
                        caller: Chiamante) -> None:
    """La chiave resta registrata con la data di revoca (non si elimina); da
    subito non permette più l'accesso. Una chiave già revocata risponde 404."""
    tid = _tenant_o_404(tenant_id)
    kid = _uuid_o_404(chiave_id)
    try:
        async with get_async_session() as session:
            account = await _account_dell_ente(session, tid, account_id, blocca=True)
            chiave = (await session.execute(
                select(SftpChiave).where(SftpChiave.id == kid,
                                         SftpChiave.account_id == account.id,
                                         SftpChiave.revocata_il.is_(None))
                .with_for_update())).scalar_one_or_none()
            if chiave is None:
                raise ErroreApi("SFTP-404", "Chiave non trovata o già revocata")
            chiave.revocata_il = func.now()
            chiave.revocata_da = _uuid_o_none(caller.get("user_id"))
            await session.flush()
            _audit(session, "sftp.chiave.revocata", tid, account.id, caller,
                   chiave_id=str(chiave.id), impronta=chiave.impronta)
    except SQLAlchemyError as exc:
        raise _errore_db("sftp_revoca_chiave_errore", exc, tenant_id=tenant_id) from exc
    logger.info("sftp_chiave_revocata", tenant_id=tenant_id, account_id=account_id,
                chiave_id=chiave_id)
