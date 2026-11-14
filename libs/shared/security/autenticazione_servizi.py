# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/autenticazione_servizi.py — autenticazione unica dei servizi
pubblici diversi dal gateway (distribution, dashboard-api, notification).

Riferimenti: VD-C-099 (R03, agente b4); REGOLE_SVILUPPO §19.1 (ogni token
verificato), §19.8 (chiavi API), §14.3 (cliente non determinabile → negato).

Ogni servizio accetta le stesse credenziali del gateway:
  - ``Authorization: Bearer <JWT>`` emesso dal gateway (verificato dalla
    funzione del servizio: firma, iss, aud, exp, tenant_id, ruolo);
  - chiave API (``X-Api-Key`` oppure ``X-Api-Key-Id`` + ``X-Api-Key-Secret``),
    verificata con ``libs.shared.security.verifica_chiavi_api``.
Dopo la verifica si applica la revoca (``libs.shared.security.revoca``): utente
disattivato, spostato o con ruolo cambiato, ente SOSPESO/DISATTIVATO → 401.

Uso in un servizio::

    utente_corrente = crea_dipendenza_utente(
        verifica_jwt=verify_access_token,
        pepper=lambda: PEPPER,               # bytes | None (carica_pepper all'avvio)
    )

    @router.get("/x")
    async def x(user: dict = Depends(utente_corrente)) -> ...

Senza credenziali: 401 AUTH-000. Le intestazioni restano documentate in
OpenAPI. Il token di canale (contratto C2) NON è accettato qui: vale solo sul
gateway.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from libs.shared.security.verifica_chiavi_api import (
    ChiaveApiRifiutata,
    ChiaviApiNonDisponibili,
    VerificaChiaveNonEseguibile,
    chiave_da_intestazioni,
    errore_api_chiave,
    verifica_chiave_api,
)

VerificaJwt = Callable[[str], dict[str, Any]]


async def autentica(
    *,
    authorization: str | None,
    x_api_key: str | None,
    x_api_key_id: str | None,
    x_api_key_secret: str | None,
    verifica_jwt: VerificaJwt,
    pepper: bytes | None,
    controlla_revoca: bool = True,
) -> dict[str, Any]:
    """Payload verificato dell'utente o del sistema (chiave API); errori dal catalogo."""
    from libs.shared.errori import ErroreApi
    from libs.shared.security.revoca import richiedi_non_revocato

    if authorization and authorization.startswith("Bearer "):
        payload = verifica_jwt(authorization.split(" ", 1)[1].strip())
    else:
        try:
            chiave = chiave_da_intestazioni(x_api_key, x_api_key_id, x_api_key_secret)
            if chiave is None:
                raise ErroreApi(
                    "AUTH-000",
                    "Fornire un Bearer token oppure la chiave API (X-Api-Key).",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            payload = (await verifica_chiave_api(chiave, pepper)).payload()
        except (ChiaveApiRifiutata, ChiaviApiNonDisponibili, VerificaChiaveNonEseguibile) as exc:
            raise errore_api_chiave(exc) from None
    if controlla_revoca:
        await richiedi_non_revocato(payload)
    return payload


def crea_dipendenza_utente(
    *,
    verifica_jwt: VerificaJwt,
    pepper: Callable[[], bytes | None],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Dipendenza FastAPI che autentica la richiesta (Bearer o chiave API)."""
    from fastapi import Header

    async def utente_corrente(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
        x_api_key_id: str | None = Header(default=None, alias="X-Api-Key-Id"),
        x_api_key_secret: str | None = Header(default=None, alias="X-Api-Key-Secret"),
    ) -> dict[str, Any]:
        return await autentica(
            authorization=authorization, x_api_key=x_api_key, x_api_key_id=x_api_key_id,
            x_api_key_secret=x_api_key_secret, verifica_jwt=verifica_jwt, pepper=pepper(),
        )

    return utente_corrente


__all__ = ["VerificaJwt", "autentica", "crea_dipendenza_utente"]
