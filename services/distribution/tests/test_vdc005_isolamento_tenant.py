# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
VD-C-005 — esibizione/distribuzione: ogni accesso filtra per il tenant del
token verificato. Il tenant B riceve 404 sui pacchetti del tenant A.

Il database è simulato da una sessione che ESEGUE davvero la clausola WHERE
della query SQLAlchemy sugli AIP in memoria (uguaglianze in AND): se la query
non filtrasse per tenant, l'AIP di A verrebbe restituito anche a B.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from app.auth.jwt_handler import create_access_token
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList

from libs.shared.models import AipStato

TENANT_A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
TENANT_B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
AIP_A = uuid.UUID("aaaaaaaa-1111-0000-0000-000000000001")
URL = "/api/v2/distribution/richiedi"


def _valuta(clausola: Any, oggetto: Any) -> bool:
    """Valuta una WHERE fatta di uguaglianze colonna = parametro in AND."""
    if isinstance(clausola, BooleanClauseList) and clausola.operator is operators.and_:
        return all(_valuta(c, oggetto) for c in clausola.clauses)
    if isinstance(clausola, BinaryExpression) and clausola.operator is operators.eq:
        assert isinstance(clausola.right, BindParameter), "confronto non supportato"
        return bool(getattr(oggetto, clausola.left.key) == clausola.right.value)
    raise NotImplementedError(f"clausola non supportata dal finto DB: {clausola!r}")


class DbFinto:
    def __init__(self, aip: list[Any]) -> None:
        self.aip = aip
        self.select_eseguite = 0

    @asynccontextmanager
    async def sessione(self) -> Any:
        db = self

        class _Sessione:
            async def execute(self, stmt: Any, params: Any = None) -> Any:
                where = getattr(stmt, "whereclause", None)
                if where is None:  # INSERT testuali del registro: ignorati
                    return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
                db.select_eseguite += 1
                trovati = [a for a in db.aip if _valuta(where, a)]
                return SimpleNamespace(
                    scalar_one_or_none=lambda: trovati[0] if trovati else None
                )

            async def commit(self) -> None:
                return None

        yield _Sessione()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> DbFinto:
    from app.routers import distribution as router_mod
    from app.services import distribution_engine as engine

    aip_a = SimpleNamespace(
        id=AIP_A, tenant_id=TENANT_A, stato=AipStato.CONSERVATO, urn="urn:a",
        s3_key="a/manifest.json", s3_bucket="b", sincro_xml_key=None,
        hash_sha256="0" * 64, dimensione_bytes=1,
    )
    finto = DbFinto([aip_a])
    monkeypatch.setattr(engine, "get_async_session", finto.sessione)
    monkeypatch.setattr(router_mod, "get_async_session", finto.sessione)
    return finto


@pytest.fixture
def s3_e_kafka(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Registra gli URL firmati generati; Kafka (outbox) e hash del PdD neutralizzati."""
    from app.routers import distribution as router_mod
    from app.services import distribution_engine as engine

    url_firmati: list[str] = []

    async def _url(bucket: str, key: str, expiry: int, nome: str | None = None) -> str:
        url_firmati.append(key)
        return f"https://s3.test/{key}?firma=x"

    async def _kafka(**_: Any) -> int:
        return 0

    monkeypatch.setattr(engine, "generate_signed_url", _url)
    monkeypatch.setattr(router_mod, "pubblica_dopo_commit", _kafka)
    monkeypatch.setattr(engine, "compute_pdd_hash", lambda valori: "h" * 64)
    return url_firmati


def _bearer(tenant: uuid.UUID | str, ruolo: str = "ADMIN_TENANT") -> dict[str, str]:
    tok = create_access_token(tenant_id=str(tenant), user_id="u", ruolo=ruolo,
                              scopes=["distribution"])
    return {"Authorization": f"Bearer {tok}"}


def _corpo(tipo: str, aip_id: uuid.UUID | str = AIP_A) -> dict[str, Any]:
    corpo: dict[str, Any] = {"aip_id": str(aip_id), "tipo": tipo, "motivo": "Richiesta test",
                             "richiedente_id": str(uuid.uuid4())}
    if tipo == "ESTRATTO":
        corpo["file_richiesti"] = ["documento.pdf"]
    return corpo


async def _post(corpo: dict[str, Any], headers: dict[str, str]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(URL, json=corpo, headers=headers)


class TestRichiestaPdD:
    @pytest.mark.parametrize("tipo", ["COMPLETO", "ESTRATTO", "ESIBIZIONE", "MIGRAZIONE"])
    async def test_tenant_b_riceve_404_sui_pacchetti_di_a(self, db, s3_e_kafka, tipo) -> None:
        r = await _post(_corpo(tipo), _bearer(TENANT_B))
        assert r.status_code == 404, r.text
        assert r.json()["codice"] == "DST-404"
        assert s3_e_kafka == []  # nessun URL firmato generato
        assert db.select_eseguite == 1

    # R03 b4: l'esibizione del proprio AIP (con lettura e verifica dei file
    # sullo storage) è provata su PostgreSQL e S3 veri in test_b4_pdd.py.

    @pytest.mark.parametrize("ruolo", ["SUPER_ADMIN", "RSC_CONSERVATORE"])
    async def test_ruoli_conservatore_non_esibiscono_contenuti_altrui(
        self, db, s3_e_kafka, ruolo
    ) -> None:
        """Il filtro usa il tenant del token anche per il personale del conservatore
        (§19.9: il conservatore vede metadati, non contenuti)."""
        r = await _post(_corpo("ESIBIZIONE"), _bearer(TENANT_B, ruolo))
        assert r.status_code == 404
        assert s3_e_kafka == []

    @pytest.mark.parametrize("tenant", ["t-demo", "non-un-uuid"])
    async def test_tenant_non_determinabile_404_senza_query(self, db, s3_e_kafka,
                                                            tenant) -> None:
        r = await _post(_corpo("ESIBIZIONE"), _bearer(tenant))
        assert r.status_code == 404
        assert db.select_eseguite == 0

    async def test_aip_id_non_valido_404_non_500(self, db, s3_e_kafka) -> None:
        r = await _post(_corpo("ESIBIZIONE", aip_id="non-un-uuid"), _bearer(TENANT_A))
        assert r.status_code == 404


class TestEngineDiretto:
    async def test_get_aip_record_filtra_per_tenant(self, db) -> None:
        from app.services.distribution_engine import _get_aip_record

        from libs.shared.errori import ErroreApi

        assert (await _get_aip_record(str(AIP_A), str(TENANT_A))).id == AIP_A
        with pytest.raises(ErroreApi) as e:
            await _get_aip_record(str(AIP_A), str(TENANT_B))
        assert e.value.status == 404 and e.value.codice == "DST-404"


class TestAutorizzazioneRouter:
    def test_aip_senza_tenant_negato(self) -> None:
        from app.routers.distribution import _check_tenant_owns_aip
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as e:
            _check_tenant_owns_aip({"tenant_id": str(TENANT_A), "ruolo": "ADMIN_TENANT"}, None)
        assert e.value.status_code == 404

    def test_token_senza_tenant_negato(self) -> None:
        from app.routers.distribution import _check_tenant_owns_aip
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _check_tenant_owns_aip({"tenant_id": "", "ruolo": "ADMIN_TENANT"}, str(TENANT_A))

    def test_pdd_altro_tenant_non_visibile(self) -> None:
        from app.routers.distribution import _pdd_visibile

        admin_b = {"tenant_id": str(TENANT_B), "ruolo": "ADMIN_TENANT"}
        assert _pdd_visibile(admin_b, str(TENANT_A)) is False
        assert _pdd_visibile(admin_b, None) is False
        assert _pdd_visibile(admin_b, str(TENANT_B)) is True
