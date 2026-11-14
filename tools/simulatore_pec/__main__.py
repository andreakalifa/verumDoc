# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Avvio del simulatore PEC (SMTP + IMAP con ricevute firmate) per il collaudo
(SOLO sviluppo, R13b):

    python -m tools.simulatore_pec --porta-smtp 2525 --porta-imap 2143

Stampa la configurazione da registrare in console (pagina «Canali PEC», tipo
SVILUPPO, con la radice del gestore di prova in ``radici_pem``) e la voce da
mettere in ``PEC_SEGRETI_JSON`` (``infra/dev_pec.env``) del lavoratore della posta.
Le ricevute dipendono dal destinatario: ``…nonaccetta…`` (non accettazione),
``…errore…`` (mancata consegna), ``…silenzio…`` (solo accettazione), altrimenti
accettazione e consegna.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulatore PEC SMTP+IMAP (solo collaudo)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--host-pubblico", default="",
                        help="nome con cui il lavoratore raggiunge il simulatore "
                             "(es. host.docker.internal)")
    parser.add_argument("--porta-smtp", type=int, default=2525)
    parser.add_argument("--porta-imap", type=int, default=2143)
    parser.add_argument("--casella", default="conservazione@pec.sviluppo.test")
    args = parser.parse_args()

    from tools.simulatore_pec.gestore import Pki
    from tools.simulatore_pec.server import SimulatorePec

    password = secrets.token_urlsafe(16)
    host = (args.host_pubblico or args.host).lower()
    sim = SimulatorePec(Pki(), utenti={args.casella: password})
    configurazione = {
        "codice": "pec-sviluppo", "tipo": "SVILUPPO", "nome": "Simulatore PEC locale",
        "configurazione": {
            "indirizzo": args.casella, "smtp_host": host, "smtp_porta": args.porta_smtp,
            "smtp_sicurezza": "nessuna", "imap_host": host, "imap_porta": args.porta_imap,
            "imap_sicurezza": "nessuna", "radici_pem": sim.pki.radice_pem},
        "PEC_SEGRETI_JSON": {"pec-sviluppo": {"host": [host], "utente": args.casella,
                                              "password": password}},
    }
    print(json.dumps(configurazione, indent=2, ensure_ascii=False))  # noqa: T201

    async def _gira() -> None:
        await sim.avvia(args.host, porta_smtp=args.porta_smtp, porta_imap=args.porta_imap)
        await asyncio.Event().wait()

    asyncio.run(_gira())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
