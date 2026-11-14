# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Avvio del simulatore CSC v2 + TSA per il collaudo (SOLO sviluppo):

    python -m tools.simulatore_firma --porta 8099
    python -m tools.simulatore_firma --host 0.0.0.0 \
        --url-pubblico http://host.docker.internal:8099 --personale responsabile-1:1234

Stampa i valori da registrare in console (pagina «Fornitori di firma e marca»)
e i segreti da mettere in FIRMA_SEGRETI_JSON del servizio di firma di sviluppo.
Con ``--personale CREDENTIAL_ID[:PIN]`` (ripetibile, R16) crea credenziali di firma
PERSONALI del responsabile: ne stampa credentialID e impronta del certificato (da
registrare con l'API di onboarding, ``PUT /api/v1/onboarding/firma/credenziali-personali``)
e, a ogni «Chiedi l'OTP» della console, stampa l'OTP monouso nel terminale.
"""
from __future__ import annotations

import argparse
import json
from typing import Any


def prepara(argv: list[str] | None = None) -> tuple[Any, dict[str, Any], argparse.Namespace]:
    """Simulatore e configurazione da stampare (senza avviare il server)."""
    parser = argparse.ArgumentParser(description="Simulatore CSC v2 e TSA (solo collaudo)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--porta", type=int, default=8099)
    parser.add_argument("--url-pubblico", default="",
                        help="indirizzo con cui i servizi raggiungono il simulatore "
                             "(predefinito http://HOST:PORTA)")
    parser.add_argument("--scal", default="2", choices=("1", "2"))
    parser.add_argument("--chiave", default="rsa", choices=("rsa", "ec"))
    parser.add_argument("--non-qualificato", action="store_true")
    parser.add_argument("--personale", action="append", default=[], metavar="ID[:PIN]",
                        help="credenziale personale del responsabile (R16), ripetibile")
    args = parser.parse_args(argv)

    from tools.simulatore_firma.app import Simulatore, configurazione_console

    url = args.url_pubblico or f"http://{args.host}:{args.porta}"
    sim = Simulatore(url_pubblico=url, scal=args.scal, chiave=args.chiave,
                     qualificato=not args.non_qualificato, stampa_otp=bool(args.personale))
    configurazione = configurazione_console(sim)
    personali = []
    for voce in args.personale:
        credenziale, _, pin = voce.partition(":")
        if not credenziale:
            parser.error("--personale: credentialID vuoto")
        impronta = sim.aggiungi_personale(credenziale, f"Responsabile di prova {credenziale}",
                                          pin=pin)
        personali.append({"credential_id": credenziale, "certificato_sha256": impronta,
                          "pin": bool(pin)})
    if personali:
        configurazione["credenziali_personali"] = personali
    return sim, configurazione, args


def main(argv: list[str] | None = None) -> int:
    sim, configurazione, args = prepara(argv)
    import uvicorn

    from tools.simulatore_firma.app import crea_app

    print(json.dumps(configurazione, indent=2, ensure_ascii=False))  # noqa: T201
    uvicorn.run(crea_app(sim), host=args.host, port=args.porta, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
