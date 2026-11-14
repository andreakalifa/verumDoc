# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Supporto ai test del ciclo di vita: token, dati di prova sul database reale,
oggetti WORM con retention, finti dei sistemi esterni che RISPETTANO i contratti
(Q3 del registro, C1/C2 del gateway, API di migrazione di distribution).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import httpx

from tests.infra_reale import sql

# ── Token e client HTTP ────────────────────────────────────────────────

def token(tenant_id: Any, ruolo: str, user_id: Any = None, *,
          chiave_privata: str | None = None) -> str:
    """Token RS256 come quelli del gateway (chiavi effimere di ENV=test)."""
    from app.api.autenticazione import chiavi_jwt
    from app.config import settings

    from libs.shared.security.jwt_rs256 import firma_token

    utente = str(user_id or uuid.uuid4())
    return firma_token(
        {"sub": utente, "tenant_id": str(tenant_id), "user_id": utente, "ruolo": ruolo,
         "scopes": ["read"]},
        chiave_privata_pem=chiave_privata or chiavi_jwt().privata_pem,
        emittente=settings.jwt_issuer, pubblico=settings.jwt_audience, durata_secondi=600)


def intestazioni(tenant_id: Any, ruolo: str, user_id: Any = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(tenant_id, ruolo, user_id)}"}


def client_app() -> httpx.AsyncClient:
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cv")


# ── Dati di prova ──────────────────────────────────────────────────────

async def crea_ente(url: str, *, pa: bool = False, natura: str | None = "",
                    firmatari: bool = True, doppia_approvazione: str | None = "MAI",
                    deroga_riversamento: bool = True) -> uuid.UUID:
    """Ente ATTIVO. ``natura`` (revisione R05, R9): predefinita «PA» con ``pa`` e
    «PRIVATO» senza; ``natura=None`` = non dichiarata (trattata come PA).

    R09 (D23): ``doppia_approvazione`` dell'annullamento per l'ente (valore e
    limite concesso dal conservatore). «MAI» per i test che precedono R09
    (annullamento subito, come prima); None = predefinito del servizio
    (DOPO_CONSERVAZIONE).

    R17 (VD-P-155): ``deroga_riversamento`` = il conservatore concede all'ente la deroga
    NON_RICHIESTA dell'autorizzazione del produttore al riversamento in uscita (i test
    che precedono R17 la danno per scontata); False = predefinito RICHIESTA."""
    tenant = uuid.uuid4()
    if natura == "":
        natura = "PA" if pa else "PRIVATO"
    await sql(url,
              "INSERT INTO public.tenants (id, codice_fiscale, ragione_sociale, schema_name, "
              "stato, email_referente, codice_ipa, natura) VALUES ($1, $2, 'Ente di prova', $3, "
              "'ATTIVO', 'prova@example.org', $4, $5)",
              tenant, tenant.hex[:16], f"t_{tenant.hex[:20]}", "c_prova" if pa else None, natura)
    if firmatari:
        # Revisione R08 (X3): il conservatore configura i firmatari autorizzati
        # dell'ente (qui: il titolare della CA di prova dei test)
        from tests.firme_prova import CF_PROVA
        for codice in ("CICLO_VITA_FIRMATARI_AUTORIZZATI", "CICLO_VITA_FIRMATARI_NULLA_OSTA"):
            await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                           "valore, motivo) VALUES ('ENTE', $1, $2, CAST($3 AS jsonb), "
                           "'firmatari di prova')", tenant, codice, json.dumps([CF_PROVA]))
    if doppia_approvazione is not None:
        for codice in ("ANNULLAMENTO_DOPPIA_APPROVAZIONE",
                       "LIMITE.ANNULLAMENTO_DOPPIA_APPROVAZIONE"):
            await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                           "valore, motivo) VALUES ('ENTE', $1, $2, CAST($3 AS jsonb), "
                           "'regola di prova')", tenant, codice, json.dumps(doppia_approvazione))
    if deroga_riversamento:
        await sql(url, "INSERT INTO public.impostazioni_valori (livello, tenant_id, codice, "
                       "valore, motivo) VALUES ('ENTE', $1, "
                       "'RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE', "
                       "'\"NON_RICHIESTA\"'::jsonb, 'deroga del conservatore (prova)')", tenant)
    return tenant


async def crea_utente(url: str, tenant: uuid.UUID, ruolo: str) -> uuid.UUID:
    utente = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.users (id, tenant_id, email, nome, cognome, ruolo, attivo) "
              "VALUES ($1, $2, $3, 'Nome', 'Cognome', CAST($4 AS public.user_ruolo), true)",
              utente, tenant, f"{utente.hex[:10]}@example.org", ruolo)
    return utente


async def crea_classe(url: str, tenant: uuid.UUID, codice: str = "contratti", *,
                      anni: int = 10, permanente: bool = False) -> uuid.UUID:
    classe = uuid.uuid4()
    await sql(url,
              "INSERT INTO public.classi_documentali (id, tenant_id, codice, nome, "
              "anni_conservazione, conservazione_permanente, formati_ammessi, attiva) "
              "VALUES ($1, $2, $3, $3, $4, $5, '[]'::jsonb, true)",
              classe, tenant, codice, anni, permanente)
    return classe


PARAMETRI_PRINCIPALE = {
    "identificativo_documento": "DOC-1", "modalita_formazione": "a",
    "tipologia_documentale": "contratto", "data_documento": "2015-03-01",
    "soggetto_produttore_id": "c_prova", "soggetto_produttore_nome": "Ente di prova",
    "oggetto": "Contratto di prova",
}


async def crea_pdv(url: str, tenant: uuid.UUID, classe: uuid.UUID, utente: uuid.UUID, *,
                   stato: str = "CONSERVATO") -> uuid.UUID:
    pdv = uuid.uuid4()
    indice = {"classe_documentale": "contratti",
              "chiave": {"registro": "REG", "anno": "2015", "numero": pdv.hex[:8]},
              "documenti": [{"id": str(uuid.uuid4()), "tipo": "PRINCIPALE",
                             "nome_file": "contratto.pdf", "formato": "application/pdf",
                             "hash": {"impronta": "ab" * 32, "algoritmo": "SHA-256"},
                             "parametri": PARAMETRI_PRINCIPALE}]}
    await sql(url,
              "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
              "metadata_json) VALUES ($1, 'PdV di prova', CAST($2 AS public.pdv_stato), $3, $4, "
              "$5, CAST($6 AS jsonb))",
              pdv, stato, classe, utente, tenant, json.dumps({"indice": indice}))
    return pdv


async def crea_pda(url: str, tenant: uuid.UUID, pdv: uuid.UUID, *, scadenza: datetime | None,
                   stato: str = "CONSERVATO", versato: datetime | None = None) -> tuple[
                       uuid.UUID, str]:
    aip = uuid.uuid4()
    urn = f"urn:verumdoc:prova:{aip.hex}"
    await sql(url,
              "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, s3_bucket, "
              "hash_sha256, scadenza_conservazione, data_versamento, data_conservazione) "
              "VALUES ($1, $2, $3, $4, CAST($5 AS public.aip_stato), $6, 'verumdoc-aip', $7, $8, "
              "$9, $9)",
              aip, pdv, tenant, urn, stato, f"{tenant}/{aip}/manifest.json", "cd" * 32,
              scadenza, versato or datetime(2015, 3, 2, tzinfo=UTC))
    return aip, urn


def s3(endpoint: str) -> Any:
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id="test",
                        aws_secret_access_key="test", region_name="us-east-1")


def bucket_worm(endpoint: str, nome: str = "verumdoc-aip") -> Any:
    client = s3(endpoint)
    try:
        client.create_bucket(Bucket=nome, ObjectLockEnabledForBucket=True)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    except Exception as exc:  # noqa: BLE001 — già creato da un altro test della sessione
        if "BucketAlreadyExists" not in str(exc) and "BucketAlreadyOwned" not in str(exc):
            raise
    return client


def oggetti_pda(client: Any, tenant: uuid.UUID, aip: uuid.UUID, *, fino_a: datetime,
                legal_hold: bool = False, bucket: str = "verumdoc-aip") -> list[str]:
    """Oggetti del PdA nel WORM con retention (passata = scaduta, simulata in moto)."""
    chiavi = []
    for nome, corpo in (("files/0000-contratto.pdf", b"%PDF-1.7 prova"),
                        ("IndiceAIP_SInCRO.xml", b"<SInCRO/>"), ("manifest.json", b"{}")):
        chiave = f"{tenant}/{aip}/{nome}"
        client.put_object(Bucket=bucket, Key=chiave, Body=corpo, ObjectLockMode="COMPLIANCE",
                          ObjectLockRetainUntilDate=fino_a,
                          ObjectLockLegalHoldStatus="ON" if legal_hold else "OFF")
        chiavi.append(chiave)
    return chiavi


def versioni(client: Any, prefisso: str, bucket: str = "verumdoc-aip") -> list[str]:
    risposta = client.list_object_versions(Bucket=bucket, Prefix=prefisso)
    return [v["Key"] for v in risposta.get("Versions", [])]


PASSATO = datetime.now(UTC) - timedelta(days=30)
FUTURO = datetime.now(UTC) + timedelta(days=3650)


# ── Finti che rispettano i contratti ───────────────────────────────────

@dataclass
class RegistroFinto:
    """Contratto Q3: ``POST …/interno/foglie`` idempotente per (tipo, pda,
    riferimento.pratica); riferimento piatto con «pratica» obbligatoria;
    impronta esadecimale SHA-256."""

    foglie: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    errore: Exception | None = None

    async def aggiungi_foglia(self, *, tipo: str, pda: str, riferimento: dict[str, Any],
                              impronta_documento_pratica: str) -> str:
        if self.errore is not None:
            raise self.errore
        from libs.catena.foglie import TIPI_CICLO_VITA

        assert tipo in TIPI_CICLO_VITA, tipo
        assert pda, "pda obbligatorio"
        assert isinstance(riferimento.get("pratica"), str) and riferimento["pratica"]
        assert all(isinstance(v, str | int | bool) or v is None for v in riferimento.values())
        assert len(impronta_documento_pratica) == 64
        int(impronta_documento_pratica, 16)
        chiave = (tipo, pda, riferimento["pratica"])
        if chiave not in self.foglie:
            self.foglie[chiave] = {"foglia_id": str(uuid.uuid4()), "riferimento": riferimento,
                                   "impronta": impronta_documento_pratica}
        return self.foglie[chiave]["foglia_id"]


@dataclass
class VersatoreFinto:
    """Contratti C1/C2 del gateway: una sessione per file con impronta verificata
    sui byte ricevuti, poi versamento per riferimento che crea il PdV RICEVUTO
    (come il caso d'uso ``versa``); stessa Idempotency-Key = stesso PdV."""

    url: str
    caricamenti: dict[str, dict[str, Any]] = field(default_factory=dict)
    versamenti: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_conto: list[dict[str, str]] = field(default_factory=list)
    rifiuta_con: dict[str, Any] | None = None

    async def carica(self, per_conto: dict[str, str], *, nome_file: str, dimensione: int,
                     sha256: str, blocchi: Callable[[], AsyncIterator[bytes]],
                     chiave: str) -> str:
        self.per_conto.append(per_conto)
        h = hashlib.sha256()
        n = 0
        async for b in blocchi():
            h.update(b)
            n += len(b)
        assert n == dimensione, (n, dimensione)
        assert h.hexdigest() == sha256
        cid = str(uuid.uuid5(uuid.NAMESPACE_URL, chiave))
        self.caricamenti[cid] = {"nome_file": nome_file, "sha256": sha256, "dimensione": n}
        return cid

    async def versa(self, per_conto: dict[str, str], *, indice: dict[str, Any],
                    file: dict[str, str], chiave: str) -> dict[str, Any]:
        from app.infrastructure.gateway import ErroreGateway

        if self.rifiuta_con is not None:
            raise ErroreGateway("rifiuto", status=422, problema=self.rifiuta_con,
                                ritentabile=False)
        if chiave in self.versamenti:
            return {"pdv_id": self.versamenti[chiave]["pdv_id"], "stato": "RICEVUTO"}
        for doc in indice["documenti"]:
            cid = file[doc["nome_file"]]
            assert self.caricamenti[cid]["sha256"] == doc["hash"]["impronta"]
        tenant = uuid.UUID(per_conto["tenant_id"])
        classe = (await sql(self.url, "SELECT id FROM public.classi_documentali WHERE codice = $1 "
                                      "AND (tenant_id = $2 OR tenant_id IS NULL) LIMIT 1",
                            indice["classe_documentale"], tenant))[0]["id"]
        pdv = uuid.uuid4()
        await sql(self.url,
                  "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, tenant_id, "
                  "metadata_json) VALUES ($1, 'PdV dal ciclo di vita', 'RICEVUTO', $2, $3, $4, "
                  "CAST($5 AS jsonb))",
                  pdv, classe, uuid.UUID(per_conto["user_id"]), tenant,
                  json.dumps({"indice": indice, "canale": "CICLO_VITA"}))
        self.versamenti[chiave] = {"pdv_id": str(pdv), "indice": indice, "file": file}
        return {"pdv_id": str(pdv), "stato": "RICEVUTO"}


@dataclass
class EsportatoreFinto:
    """API di migrazione di distribution: un PdD MIGRAZIONE per PdA, poi PRONTO."""

    pdd: dict[str, dict[str, Any]] = field(default_factory=dict)
    pronti: bool = True
    richieste: list[dict[str, Any]] = field(default_factory=list)

    async def esporta(self, *, ente: str, aip_ids: list[str], destinatario: str,
                      motivo: str) -> dict[str, Any]:
        assert len(aip_ids) <= 100
        self.richieste.append({"ente": ente, "aip_ids": aip_ids, "destinatario": destinatario})
        risultati = []
        for a in aip_ids:
            pdd_id = str(uuid.uuid4())
            self.pdd[pdd_id] = {"aip_id": a, "ente": ente}
            risultati.append({"aip_id": a, "pdd_id": pdd_id, "stato": "IN_PREPARAZIONE"})
        return {"results": risultati, "errori": []}

    async def stato(self, *, ente: str, pdd_id: str) -> dict[str, Any]:
        assert self.pdd[pdd_id]["ente"] == ente
        if not self.pronti:
            return {"stato": "IN_PREPARAZIONE"}
        return {"stato": "PRONTO", "hash_pdd": "ee" * 32,
                "zip": {"hash_sha256": hashlib.sha256(pdd_id.encode()).hexdigest(),
                        "dimensione_bytes": 1234}}


def dipendenze(url: str) -> Any:
    from app.application.lavoratore import Dipendenze

    return Dipendenze(registro=RegistroFinto(), versatore=VersatoreFinto(url),
                      esportatore=EsportatoreFinto())


async def lavora(dip: Any, *, giri: int = 20) -> list[str]:
    """Giri del lavoratore finché c'è lavoro dovuto (al più ``giri``)."""
    from app.application.lavoratore import giro

    esiti = []
    for _ in range(giri):
        esito = await giro(dip, "test-lavoratore")
        if esito is None:
            break
        esiti.append(esito)
    return esiti


async def outbox(url: str, topic: str) -> list[dict[str, Any]]:
    righe = await sql(url, "SELECT payload FROM public.outbox_eventi WHERE topic = $1 "
                           "AND origine = 'ciclo-vita' ORDER BY sequenza", topic)
    return [json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
            for r in righe]
