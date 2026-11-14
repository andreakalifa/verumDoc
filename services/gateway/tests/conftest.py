# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Fixtures pytest per test F1.1 Gateway.
Fornisce: app FastAPI test client, tenant mock, JWT test.
"""
from __future__ import annotations

import os
from typing import Any

# Prima di importare l'app: ambiente di test. Senza chiavi in ambiente il
# gateway usa una coppia RS256 effimera in memoria (solo con ENV=test, VD-C-004).
# Il login di sviluppo resta spento: i test che lo usano lo attivano esplicitamente.
os.environ["ENV"] = "test"
os.environ.pop("VERUMDOC_DEV_AUTH", None)
# Revisione R08 (X11): la validation lanciata dai test end-to-end non ripiega più
# in silenzio sulla firma binaria se Siegfried manca. Qui i file sono sintetici
# (es. «PDF» con corpo casuale): firma binaria scelta esplicitamente, come prima;
# Siegfried reale si prova nelle suite di validation e libs/shared/formati
os.environ.setdefault("FORMATI_RICONOSCITORE", "firma_binaria")

import pytest  # noqa: E402
from app.auth.jwt_handler import create_access_token  # noqa: E402


@pytest.fixture
def test_tenant_id() -> str:
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def test_user_id() -> str:
    return "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def admin_token(test_tenant_id, test_user_id) -> str:
    """JWT per ADMIN_TENANT con tutti i scope."""
    return create_access_token(
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        ruolo="ADMIN_TENANT",
        scopes=["versamento:ud", "versamento:doc", "read"],
    )


@pytest.fixture
def operatore_token(test_tenant_id, test_user_id) -> str:
    """JWT per OPERATORE con scope versamento:ud."""
    return create_access_token(
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        ruolo="OPERATORE",
        scopes=["versamento:ud", "read"],
    )


@pytest.fixture
def expired_token(test_tenant_id, test_user_id) -> str:
    """JWT scaduto per test errori auth."""
    import time

    import jwt
    from app.auth.jwt_handler import chiavi_jwt
    from app.config import settings

    private_key = chiavi_jwt().privata_pem
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600,  # scaduto 1h fa
        "tenant_id": test_tenant_id,
        "user_id": test_user_id,
        "ruolo": "OPERATORE",
        "scopes": ["read"],
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


@pytest.fixture
def indice_ud_valido(test_tenant_id) -> dict:
    """IndiceUD valido per test versamento."""
    return {
        "classe_documentale": "fattura",
        "chiave": {"numero": "FT-2026-TEST", "anno": "2026", "registro": "FATTURE"},
        "documenti": [
            {
                "id": "a7f3d2e4-8891-4c3d-9d62-8f16a0b1e7c4",
                "tipo": "PRINCIPALE",
                "nome_file": "FT_2026_TEST.pdf",
                "formato": "application/pdf",
                "hash": {"impronta": "abc123" * 10 + "ab", "algoritmo": "SHA-256"},
                "allegati": [],
            }
        ],
    }


class RedisFinto:
    """Redis in memoria per i test (solo i comandi usati da spid.py e dall'accesso
    locale). R12b: scadenze vere (SETEX, EXPIRE, TTL) e ``scadi`` per farle passare."""

    def __init__(self) -> None:
        self.dati: dict[str, str] = {}
        self.scadenze: dict[str, float] = {}

    def _vivo(self, chiave: str) -> None:
        import time

        if chiave in self.scadenze and self.scadenze[chiave] <= time.monotonic():
            self.dati.pop(chiave, None)
            self.scadenze.pop(chiave, None)

    def scadi(self, chiave: str) -> None:
        """Il tempo passa: la chiave è scaduta."""
        if chiave in self.dati:
            self.scadenze[chiave] = 0.0

    async def setex(self, chiave: str, ttl: int, valore: str) -> None:
        import time

        self.dati[chiave] = valore
        self.scadenze[chiave] = time.monotonic() + int(ttl)

    async def getdel(self, chiave: str) -> str | None:
        self._vivo(chiave)
        self.scadenze.pop(chiave, None)
        return self.dati.pop(chiave, None)

    async def get(self, chiave: str) -> str | None:
        self._vivo(chiave)
        return self.dati.get(chiave)

    async def ttl(self, chiave: str) -> int:
        import math
        import time

        self._vivo(chiave)
        if chiave not in self.dati:
            return -2
        if chiave not in self.scadenze:
            return -1
        return max(1, math.ceil(self.scadenze[chiave] - time.monotonic()))

    async def incr(self, chiave: str) -> int:
        self._vivo(chiave)
        self.dati[chiave] = str(int(self.dati.get(chiave, "0")) + 1)
        return int(self.dati[chiave])

    async def decr(self, chiave: str) -> int:
        self._vivo(chiave)
        self.dati[chiave] = str(int(self.dati.get(chiave, "0")) - 1)
        return int(self.dati[chiave])

    async def delete(self, chiave: str) -> int:
        self.scadenze.pop(chiave, None)
        return int(self.dati.pop(chiave, None) is not None)

    async def expire(self, chiave: str, ttl: int) -> None:
        import time

        if chiave in self.dati:
            self.scadenze[chiave] = time.monotonic() + int(ttl)

    async def set(self, chiave: str, valore: str, *, nx: bool = False,
                  ex: int | None = None) -> bool | None:
        """SET con NX/EX (R17: conferma dell'identità consumata da una sola decisione)."""
        import time

        self._vivo(chiave)
        if nx and chiave in self.dati:
            return None
        self.dati[chiave] = str(valore)
        if ex is not None:
            self.scadenze[chiave] = time.monotonic() + int(ex)
        return True

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        """Solo lo script di ``accesso_locale.SCRIPT_INCR_CON_SCADENZA``, eseguito senza
        ``await`` intermedi (atomico come in Redis)."""
        import time

        if "vd:incr_con_scadenza" not in script:
            raise NotImplementedError("script non previsto dal Redis finto")
        chiave, secondi = str(args[0]), int(args[1])
        self._vivo(chiave)
        self.dati[chiave] = str(int(self.dati.get(chiave, "0")) + 1)
        if chiave not in self.scadenze:
            self.scadenze[chiave] = time.monotonic() + secondi
        return int(self.dati[chiave])

    async def aclose(self) -> None:
        return None


@pytest.fixture
def redis_finto(monkeypatch: pytest.MonkeyPatch) -> RedisFinto:
    """Sostituisce il Redis dello state SPID con uno in memoria."""
    from app.auth import spid

    finto = RedisFinto()

    async def _get_redis() -> RedisFinto:
        return finto

    monkeypatch.setattr(spid, "_get_redis", _get_redis)
    return finto


@pytest.fixture
def login_sviluppo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Attiva esplicitamente il login di sviluppo (flag + ENV=test)."""
    from app.config import settings

    monkeypatch.setattr(settings, "verumdoc_dev_auth", True)
    monkeypatch.setattr(settings, "env", "test")


@pytest.fixture(autouse=True)
def _quote_versamento_senza_redis():
    """Quota PdV/giorno del caso d'uso (R03 i1) esclusa per default nei test: senza
    questo ``versa`` userebbe il Redis di ``settings.redis_url``. I test delle quote
    installano un Redis in memoria con ``imposta_fornitore_redis``."""
    from app.services.quota_versamento import imposta_fornitore_redis

    precedente = imposta_fornitore_redis(lambda: None)
    yield
    imposta_fornitore_redis(precedente)


@pytest.fixture
def ambito_del_conservatore():
    """R05 (VD-C-080): per i test che chiamano DIRETTAMENTE funzioni usate in
    esercizio dai lavoratori multi-ente o dal personale del conservatore (ambito
    conservatore impostato dal lavoratore o dal token verificato)."""
    from libs.shared.db.ambito import ambito_conservatore

    with ambito_conservatore("test di una funzione dei lavoratori o del conservatore"):
        yield
