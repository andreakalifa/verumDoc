# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/security/ip_cliente.py — indirizzo IP reale del cliente dietro i proxy fidati (R10, V1).

Il gateway sta dietro l'ALB (target-type ip): l'indirizzo della connessione è
quello del nodo dell'ALB, uguale per migliaia di persone. L'IP del cliente sta in
``X-Forwarded-For``, a cui l'ALB AGGIUNGE in coda l'indirizzo da cui riceve la
connessione. Regola (come per il PROXY protocol di sftp-ingest, VD-P-101):
  - se l'indirizzo della connessione NON è un proxy fidato, è lui il cliente
    (l'intestazione, scritta da chiunque, si ignora);
  - altrimenti si scorre ``X-Forwarded-For`` da destra e si salta ogni hop fidato:
    il primo hop NON fidato è il cliente (le voci a sinistra le scrive il cliente
    stesso e si possono falsificare); se tutti gli hop sono fidati vale il più a
    sinistra; una voce non leggibile ferma la ricerca (vale l'ultimo hop fidato).
I proxy fidati sono reti CIDR di configurazione (gateway: ``PROXY_FIDATI`` = le
sottoreti dell'ALB, mai ``0.0.0.0/0``), mai un dato della richiesta.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from libs.shared.security.rete import ammesso, indirizzo


def ip_cliente(peer: str | None, intestazioni: Mapping[str, str],
               proxy_fidati: Iterable[str]) -> str:
    """IP del cliente (testo); ``"sconosciuto"`` se non si ricava."""
    fidati = list(proxy_fidati)
    corrente = str(peer or "")
    if not corrente or not fidati or not ammesso(corrente, fidati):
        return corrente or "sconosciuto"
    catena = []
    for valore in _valori(intestazioni, "x-forwarded-for"):
        catena += [h.strip() for h in valore.split(",") if h.strip()]
    for hop in reversed(catena):
        if indirizzo(hop) is None:
            return corrente  # voce illeggibile: ci si ferma all'ultimo hop fidato
        corrente = str(indirizzo(hop))
        if not ammesso(corrente, fidati):
            return corrente
    return corrente


def _valori(intestazioni: Mapping[str, str], nome: str) -> list[str]:
    """Tutte le occorrenze dell'intestazione (Starlette: ``getlist``), nell'ordine."""
    getlist = getattr(intestazioni, "getlist", None)
    if callable(getlist):
        return [str(v) for v in getlist(nome)]
    v = intestazioni.get(nome)
    return [str(v)] if v else []


__all__ = ["ip_cliente"]
