# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Emissione del token di servizio hashchain → signature (revisione di sicurezza M2).

Uso (dalla radice del repository, in un ambiente con la chiave PRIVATA del
gateway nella variabile JWT_PRIVATE_KEY, es. un Job/CI con accesso al Secret):

    python -m libs.shared.security.emetti_token_servizio --servizio hashchain --giorni 30

Stampa SOLO il token su stdout: va salvato nel Secret di hashchain
(SIGNATURE_SERVICE_TOKEN) senza passare da file o log. Il token:
  - ha audience «verumdoc-signature» (non «verumdoc-api»): non vale come token
    utente su gateway, dashboard-api, distribution, onboarding, hashchain;
  - non ha tenant_id né ruolo: la verifica dei token utente lo rifiuta;
  - porta lo scope «firma:servizio», l'unico ammesso da /firma e /marca;
  - scade (massimo 90 giorni): ruotarlo PRIMA della scadenza (vedi
    infra/k8s/secrets/README.md). Alla scadenza i blocchi restano DA_FIRMARE
    (ripresa automatica appena il nuovo token è in servizio).

Token di LETTURA DEL REGISTRO (R04, testimone e verificatore → hashchain
``GET /api/v1/hashchain/registro/foglie``): stessa forma con audience e scope
del registro, ripetibile per più scope:

    python -m libs.shared.security.emetti_token_servizio --servizio testimone \
        --pubblico verumdoc-registro --scope registro:leggi --giorni 30

Token di AGGIUNTA AL REGISTRO (R05, contratto Q3: ciclo-vita → hashchain
``POST/GET /api/v1/hashchain/interno/foglie``, foglie di scarto, annullamento e
rettifica): stessa audience del registro, scope ``registro:aggiungi``; va nel
Secret del servizio ciclo-vita. Un token di sola lettura non aggiunge foglie
(403), uno di aggiunta non legge le foglie del registro:

    python -m libs.shared.security.emetti_token_servizio --servizio ciclo-vita \
        --pubblico verumdoc-registro --scope registro:aggiungi --giorni 30

Token di MIGRAZIONE (R05, contratto Q2: ciclo-vita → distribution
``POST /api/v2/migration/export-batch`` e ``GET …/status/{id}?ente=``, PdD
MIGRAZIONE del riversamento in uscita e della restituzione per conto di un
ente): audience ``verumdoc-distribution``, scope ``migrazione:servizio``; va
nel Secret di ciclo-vita (DISTRIBUTION_TOKEN_SERVIZIO):

    python -m libs.shared.security.emetti_token_servizio --servizio ciclo-vita \
        --pubblico verumdoc-distribution --scope migrazione:servizio --giorni 30

Token di CANALE (contratto C2, R03 b4): con ``--canale`` si emette invece un
token ``libs.shared.security.token_canale`` per conto di un ente e di un
utente responsabile, firmato con la chiave privata DEL SERVIZIO DI CANALE
(JWT_PRIVATE_KEY del servizio, es. sftp-ingest), audience «verumdoc-canale»,
durata in secondi (massimo 3600). In esercizio il servizio di canale lo emette
da sé per ogni lotto con ``firma_token_canale``; la riga di comando serve per
prove e diagnosi:

    python -m libs.shared.security.emetti_token_servizio --servizio sftp-ingest \
        --canale SFTP --ente <tenant_id> --utente <user_id> --ruolo OPERATORE
"""
from __future__ import annotations

import argparse
import os
import sys

from libs.shared.security.jwt_rs256 import (
    DURATA_MASSIMA_TOKEN_SERVIZIO_GIORNI,
    PUBBLICO_SERVIZIO_FIRMA,
    SCOPE_FIRMA_SERVIZIO,
    carica_chiavi_jwt,
    firma_token_servizio,
)
from libs.shared.security.token_canale import (
    DURATA_PREDEFINITA_TOKEN_CANALE,
    PUBBLICO_TOKEN_CANALE,
    firma_token_canale,
)


def main(argomenti: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--servizio", required=True, help="servizio chiamante (es. hashchain)")
    parser.add_argument("--giorni", type=int, default=30,
                        help=f"durata (massimo {DURATA_MASSIMA_TOKEN_SERVIZIO_GIORNI})")
    parser.add_argument("--emittente", default=os.environ.get("JWT_ISSUER", "verumdoc.it"))
    parser.add_argument("--pubblico", default=None,
                        help=f"audience (predefinita: {PUBBLICO_SERVIZIO_FIRMA}, "
                             f"con --canale {PUBBLICO_TOKEN_CANALE})")
    parser.add_argument("--scope", action="append", default=None, dest="scopes",
                        help=f"scope del token di servizio, ripetibile (predefinito: "
                             f"{SCOPE_FIRMA_SERVIZIO}; es. registro:leggi con "
                             f"--pubblico verumdoc-registro); non con --canale")
    parser.add_argument("--canale", default=None, help="token di canale (es. SFTP)")
    parser.add_argument("--ente", default=None, help="tenant_id (solo con --canale)")
    parser.add_argument("--utente", default=None, help="user_id responsabile (solo con --canale)")
    parser.add_argument("--ruolo", default=None,
                        help="ruolo attuale dell'utente (solo con --canale)")
    parser.add_argument("--secondi", type=int, default=DURATA_PREDEFINITA_TOKEN_CANALE,
                        help="durata del token di canale in secondi")
    opzioni = parser.parse_args(argomenti)
    scopes = [x.strip() for x in (opzioni.scopes or [SCOPE_FIRMA_SERVIZIO])]
    if opzioni.canale and opzioni.scopes:
        print("errore: --scope vale solo per i token di servizio, non con --canale",  # noqa: T201 — interfaccia a riga di comando
              file=sys.stderr)
        return 2
    if any(not x or any(c.isspace() for c in x) for x in scopes):
        print("errore: scope vuoto o con spazi", file=sys.stderr)  # noqa: T201 — interfaccia a riga di comando
        return 2

    chiavi = carica_chiavi_jwt(
        env=os.environ.get("ENV", "production"),
        servizio="emetti_token_servizio",
        chiave_pubblica=os.environ.get("JWT_PUBLIC_KEY", ""),
        chiave_privata=os.environ.get("JWT_PRIVATE_KEY", ""),
        richiede_privata=True,
    )
    try:
        if opzioni.canale:
            token = firma_token_canale(
                opzioni.servizio, tenant_id=opzioni.ente or "", user_id=opzioni.utente or "",
                ruolo=opzioni.ruolo or "", canale=opzioni.canale,
                chiave_privata_pem=chiavi.privata_pem or "", emittente=opzioni.emittente,
                durata_secondi=opzioni.secondi,
                pubblico=opzioni.pubblico or PUBBLICO_TOKEN_CANALE,
            )
        else:
            token = firma_token_servizio(
                opzioni.servizio, chiave_privata_pem=chiavi.privata_pem or "",
                emittente=opzioni.emittente,
                pubblico=opzioni.pubblico or PUBBLICO_SERVIZIO_FIRMA,
                scopes=scopes,
                durata_secondi=opzioni.giorni * 86400,
            )
    except ValueError as exc:
        print(f"errore: {exc}", file=sys.stderr)  # noqa: T201 — interfaccia a riga di comando
        return 2
    print(token)  # noqa: T201 — interfaccia a riga di comando
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
