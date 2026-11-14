# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/elenchi_ambiente.py — elenchi nelle variabili d'ambiente, un solo formato.

Riferimenti: R03 i1 (richiesta dell'agente infra i2): ``SFTP_TIPI_CHIAVE_AMMESSI``
era letto come elenco separato da virgole da onboarding e come JSON da
sftp-ingest; la stessa variabile, impostata una volta per i due servizi, faceva
fallire uno dei due.

Formato unico: **elenco separato da virgole** (``ssh-ed25519,ecdsa-sha2-nistp256``),
spazi ignorati, voci vuote scartate. Per compatibilità resta accettato un
array JSON di stringhe (``["ssh-ed25519", "ecdsa-sha2-nistp256"]``). Uso con
pydantic-settings: campo ``Annotated[list[str], NoDecode]`` e un
``field_validator(mode="before")`` che chiama ``elenco_da_ambiente``.
"""
from __future__ import annotations

import json
from typing import Any


def elenco_da_ambiente(valore: Any) -> Any:
    """Elenco di stringhe da una variabile d'ambiente (virgole o array JSON).

    Valori non testuali (già elenchi, predefiniti) passano invariati alla
    validazione di pydantic. ``ValueError`` per un JSON che non è un array di
    stringhe: nessun valore indovinato."""
    if not isinstance(valore, str):
        return valore
    testo = valore.strip()
    if testo.startswith("["):
        try:
            dati = json.loads(testo)
        except json.JSONDecodeError as exc:
            raise ValueError(f"elenco JSON non valido: {exc.msg}") from None
        if not isinstance(dati, list) or not all(isinstance(v, str) for v in dati):
            raise ValueError("l'elenco JSON deve contenere solo stringhe")
        return [v.strip() for v in dati if v.strip()]
    return [p.strip() for p in testo.split(",") if p.strip()]


__all__ = ["elenco_da_ambiente"]
