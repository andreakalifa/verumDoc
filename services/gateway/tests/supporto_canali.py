# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Supporto dei test dei canali R03 b1 (sessioni di caricamento, PDND, adattatori):
storage S3 (moto) effimero, utente autenticato, indici e file di prova.

Infrastruttura vera dove conta (REGOLE §23.3): PostgreSQL migrato
(VD_TEST_DATABASE_URL, fixture ``db_url``) e S3 moto in un thread.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
from app.auth.dependencies import get_current_user
from app.config import settings
from app.main import app
from app.models.versamento import ModalitaFormazione
from httpx import ASGITransport, AsyncClient

from tests.identita_prova import dipendenza_utente
from tests.infra_reale import client_s3

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF\n"
UTENTE = "00000000-0000-0000-0000-000000000002"
MINIMI: dict[str, Any] = {
    "identificativo_documento": "DOC-2026-001",
    "modalita_formazione": next(iter(ModalitaFormazione)).value,
    "tipologia_documentale": "fattura",
    "data_documento": "2026-03-19",
    "soggetto_produttore_id": "12345678901",
    "soggetto_produttore_nome": "Acme S.r.l.",
    "oggetto": "Documento di prova per i canali",
}
MB = 1024 * 1024


def sha256(dati: bytes) -> str:
    return hashlib.sha256(dati).hexdigest()


def pdf_di(dimensione: int, seme: int = 0) -> bytes:
    """PDF sintetico di ``dimensione`` byte (testa PDF, corpo pseudo-casuale ripetibile)."""
    testa = PDF[:16]
    blocco = hashlib.sha512(f"seme-{seme}".encode()).digest() * 64
    corpo = (blocco * (dimensione // len(blocco) + 1))[: dimensione - len(testa)]
    return testa + corpo


def indice(nome: str = "fattura.pdf", contenuto: bytes = PDF, *, numero: str = "FT-001",
           registro: str = "FATTURE", classe: str = "fattura",
           con_parametri: bool = True) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": "doc-001", "tipo": "PRINCIPALE", "nome_file": nome, "formato": "application/pdf",
        "hash": {"impronta": sha256(contenuto), "algoritmo": "SHA-256"},
    }
    if con_parametri:
        doc["parametri"] = dict(MINIMI)
    return {"classe_documentale": classe,
            "chiave": {"numero": numero, "anno": "2026", "registro": registro},
            "documenti": [doc]}


@pytest.fixture
def s3(s3_server, monkeypatch):
    """Client S3 su un bucket di staging nuovo; il gateway punta allo stesso server."""
    monkeypatch.setattr(settings, "s3_endpoint_url", s3_server)
    monkeypatch.setattr(settings, "s3_access_key", "test")
    monkeypatch.setattr(settings, "s3_secret_key", "test")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket_pdv", f"pdv-{uuid.uuid4().hex[:8]}")
    client = client_s3(s3_server)
    client.create_bucket(Bucket=settings.s3_bucket_pdv)
    return client


def chiavi_staging(s3) -> list[str]:
    return sorted(o["Key"] for o in s3.list_objects_v2(
        Bucket=settings.s3_bucket_pdv).get("Contents", []))


@pytest.fixture
def utente_di():
    """Imposta l'utente autenticato (dipendenza sovrascritta) per il cliente indicato."""

    def _imposta(tenant: uuid.UUID | str, *, ruolo: str = "OPERATORE",
                 scopes: list[str] | None = None, **extra: Any) -> None:
        app.dependency_overrides[get_current_user] = dipendenza_utente({
            "tenant_id": str(tenant), "user_id": UTENTE, "ruolo": ruolo,
            "scopes": scopes or ["versamento:ud", "read"], **extra,
        })

    yield _imposta
    app.dependency_overrides.pop(get_current_user, None)


def client_http() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class ProducerFinto:
    def __init__(self) -> None:
        self.inviati: list[tuple[str, str, dict]] = []

    async def send_and_wait(self, topic, value=None, key=None):
        self.inviati.append((topic, key, value))


@pytest.fixture
def senza_lavoratore(monkeypatch):
    """Nei test la verifica si esegue a mano (un giro di ``verifica_in_sospeso``)."""
    monkeypatch.setattr(settings, "caricamenti_lavoratore_attivo", False)
