# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verifica delle chiamate webhook ricevute da VerumDoc (contratto C3).

VerumDoc firma ogni consegna con l'intestazione::

    VerumDoc-Firma: t=<unix>,v1=<hex HMAC-SHA256(segreto, "<t>." + corpo)>

dove ``segreto`` è quello mostrato una sola volta alla creazione del webhook e
``corpo`` sono i byte ricevuti, così come sono arrivati (non riformattare il JSON).

Esempio (gli stessi valori di ``docs/integrazione/webhook.md``)::

    from verumdoc_sdk.webhook import verifica_firma, leggi_evento

    corpo = (b'{"creato_il":"2026-10-21T10:00:00+00:00","dati":{"pdv_id":'
             b'"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90"},"id":42,"risorsa":{"id":'
             b'"3f1c0b9e-2d4a-4c55-9a1e-7b0c2f6d8e90","tipo":"versamento"},'
             b'"tipo":"versamento.ricevuto"}')
    intestazione = ("t=1792576800,v1=362a779b910d922709231ed641f88061"
                    "f03db35fa8a4a1bc939f4afe65b228ad")
    assert verifica_firma("whsec_esempio-di-segreto-non-usare", intestazione, corpo,
                          adesso=1792576800)
    evento = leggi_evento(corpo)      # modello Evento
    print(evento.tipo, evento.risorsa.id)

In un'applicazione web (qualunque framework)::

    corpo = await request.body()                       # byte grezzi
    if not verifica_firma(SEGRETO, request.headers["VerumDoc-Firma"], corpo):
        return 401
    id_consegna = request.headers["VerumDoc-Id-Consegna"]   # per scartare i duplicati
    ...rispondere 2xx subito ed elaborare dopo...

La funzione è pura: non legge l'orologio se ``adesso`` è indicato e non fa I/O.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from verumdoc_sdk.modelli import Evento

INTESTAZIONE_FIRMA = "VerumDoc-Firma"
INTESTAZIONE_EVENTO = "VerumDoc-Evento"
INTESTAZIONE_ID_EVENTO = "VerumDoc-Id-Evento"
INTESTAZIONE_ID_CONSEGNA = "VerumDoc-Id-Consegna"
VERSIONE_FIRMA = "v1"
#: Scarto massimo fra ``t`` della firma e l'orologio del destinatario (anti-replay).
TOLLERANZA_SECONDI = 300


def calcola_firma(segreto: str, istante: int, corpo: bytes) -> str:
    """hex(HMAC-SHA256(segreto, "<istante>." + corpo))."""
    messaggio = str(int(istante)).encode("ascii") + b"." + corpo
    return hmac.new(segreto.encode("utf-8"), messaggio, hashlib.sha256).hexdigest()


def intestazione_firma(segreto: str, istante: int, corpo: bytes) -> str:
    """Valore dell'intestazione ``VerumDoc-Firma`` (utile nei test del destinatario)."""
    return f"t={int(istante)},{VERSIONE_FIRMA}={calcola_firma(segreto, istante, corpo)}"


def verifica_firma(segreto: str, intestazione: str | None, corpo: bytes, *,
                   adesso: int | None = None,
                   tolleranza_secondi: int = TOLLERANZA_SECONDI) -> bool:
    """Vero se ``intestazione`` è una firma valida di ``corpo`` con ``segreto`` e
    l'istante ``t`` dista al più ``tolleranza_secondi`` da ``adesso`` (predefinito:
    l'orologio di sistema). Accetta più valori ``v1=`` (rotazione del segreto).
    Confronto a tempo costante; un'intestazione malformata dà ``False``."""
    if not intestazione or not isinstance(corpo, (bytes, bytearray)):
        return False
    campi: dict[str, list[str]] = {}
    for parte in intestazione.split(","):
        chiave, _, valore = parte.strip().partition("=")
        campi.setdefault(chiave, []).append(valore.strip())
    try:
        istante = int(campi["t"][0])
    except (KeyError, ValueError, IndexError):
        return False
    riferimento = int(time.time()) if adesso is None else int(adesso)
    if abs(riferimento - istante) > tolleranza_secondi:
        return False
    attesa = calcola_firma(segreto, istante, bytes(corpo))
    return any(hmac.compare_digest(attesa, f) for f in campi.get(VERSIONE_FIRMA, []))


def leggi_evento(corpo: bytes | str) -> Evento:
    """Evento dal corpo della consegna (stessa forma dell'elenco eventi)."""
    return Evento.model_validate_json(corpo)
