#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
tools/validatore/valida_pdv.py — validatore preventivo di un versamento VerumDoc
(R05 Q1; REGOLE_SVILUPPO §7.3, §9.2; piano R03-R05 §6.1).

Usa LO STESSO motore del servizio (``libs/verumdoc_rules.validatore``): stessi
controlli e stessi codici del versamento (VRS, SEC, RGL). Due modi:

  senza rete, con un profilo esportato dal servizio
  (``GET /api/v1/configurazione/profilo-validatore?classe=…``)::

      python tools/validatore/valida_pdv.py indice.json --profilo profilo.json \\
          --file fattura.pdf --file allegato.pdf

  in linea, chiedendo al servizio (``POST /api/v1/configurazione/valida``) con la
  chiave API dell'ente (variabile ``VERUMDOC_API_KEY`` o ``--chiave``)::

      python tools/validatore/valida_pdv.py indice.json --url https://api.verumdoc.it \\
          --file fattura.pdf

  per scaricare il profilo da usare poi senza rete::

      python tools/validatore/valida_pdv.py --url https://api.verumdoc.it \\
          --scarica-profilo profilo.json --classe fattura [--accordo AV-1]

I file si leggono a blocchi (mai interi in memoria): se ne calcolano impronta
(con l'algoritmo dichiarato nell'indice) e dimensione. Il FORMATO usato è quello
dichiarato nell'indice (``formato``): il servizio lo riconosce dal contenuto, il
validatore no (è fra i ``controlli_non_eseguiti`` del risultato).

Codice d'uscita: 0 conforme, 1 non conforme, 2 errore d'uso o di rete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_RADICE = Path(__file__).resolve().parents[2]
if str(_RADICE) not in sys.path:
    sys.path.insert(0, str(_RADICE))

from libs.verumdoc_rules.override import OverrideNonAmmesso  # noqa: E402
from libs.verumdoc_rules.validatore import (  # noqa: E402
    CONTROLLI_NON_ESEGUITI,
    EsportazioneNonValida,
    ambiente_da_esportazione,
    controlla_forma,
    rilievo,
    valida_versamento,
)

BLOCCO = 1024 * 1024
_FUNZIONI = {"SHA-256": hashlib.sha256, "SHA-512": hashlib.sha512, "SHA3-256": hashlib.sha3_256}
USCITA_CONFORME, USCITA_NON_CONFORME, USCITA_ERRORE = 0, 1, 2


def info_file(percorsi: list[Path], indice: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Impronta (algoritmo dichiarato), dimensione e formato dichiarato per nome file."""
    dichiarati = {str(d.get("nome_file")): d for d in indice.get("documenti") or ()
                  if isinstance(d, dict)}
    info: dict[str, dict[str, Any]] = {}
    for percorso in percorsi:
        doc = dichiarati.get(percorso.name, {})
        algoritmo = str((doc.get("hash") or {}).get("algoritmo") or "SHA-256")
        funzione = _FUNZIONI.get(algoritmo, hashlib.sha256)()
        dimensione = 0
        with percorso.open("rb") as f:
            while blocco := f.read(BLOCCO):
                funzione.update(blocco)
                dimensione += len(blocco)
        info[percorso.name] = {"impronta": funzione.hexdigest(), "algoritmo": algoritmo,
                               "dimensione": dimensione, "formato": doc.get("formato")}
    return info


def valida_offline(indice: Any, profilo: dict[str, Any], file_info: dict[str, Any] | None,
                   adesso: datetime) -> dict[str, Any]:
    """Validazione senza rete con il profilo esportato."""
    intestazione, forma = controlla_forma(indice)
    if intestazione is None:
        return {"esito": "NON_CONFORME", "rilievi": forma, "avvisi": [],
                "controlli_non_eseguiti": list(CONTROLLI_NON_ESEGUITI)}
    if intestazione.classe != profilo.get("classe_documentale"):
        forma.append(rilievo("VRS-105", f"Il profilo è della classe "
                             f"{profilo.get('classe_documentale')!r}, l'indice della classe "
                             f"{intestazione.classe!r}: scaricare il profilo giusto",
                             campo="classe_documentale"))
    accordo = profilo.get("accordo") or {}
    if intestazione.accordo and intestazione.accordo != accordo.get("codice"):
        forma.append(rilievo("RGL-112", f"Il profilo non contiene l'accordo "
                             f"{intestazione.accordo!r}: scaricarlo con --accordo",
                             campo="accordo"))
    try:
        ambiente, avvisi = ambiente_da_esportazione(profilo, adesso=adesso,
                                                    override=intestazione.override)
    except OverrideNonAmmesso as exc:
        forma.append(rilievo("RGL-111", str(exc)[:500], campo="override"))
        return {"esito": "NON_CONFORME", "rilievi": forma, "avvisi": [],
                "controlli_non_eseguiti": list(CONTROLLI_NON_ESEGUITI)}
    esito = valida_versamento(indice, ambiente, file_info=file_info, rilievi_forma=forma)
    risultato = esito.come_dict()
    risultato["avvisi"] = [*avvisi, *risultato["avvisi"]]
    return risultato


def _richiesta(url: str, *, chiave: str | None, corpo: Any = None) -> Any:
    intestazioni = {"Accept": "application/json"}
    if chiave:
        intestazioni["X-Api-Key"] = chiave
    dati = None
    if corpo is not None:
        dati = json.dumps(corpo).encode("utf-8")
        intestazioni["Content-Type"] = "application/json"
    richiesta = urllib.request.Request(url, data=dati, headers=intestazioni,  # noqa: S310
                                       method="POST" if dati else "GET")
    with urllib.request.urlopen(richiesta, timeout=60) as risposta:  # noqa: S310 — URL dato
        return json.loads(risposta.read().decode("utf-8"))


def _stampa(esito: dict[str, Any], come_json: bool) -> None:
    if come_json:
        print(json.dumps(esito, ensure_ascii=False, indent=2))
        return
    print(f"Esito: {esito['esito']}")
    for titolo, voci in (("Rilievi bloccanti", esito.get("rilievi") or []),
                         ("Avvisi", esito.get("avvisi") or [])):
        if voci:
            print(f"{titolo}:")
            for r in voci:
                dove = " ".join(x for x in (r.get("documento"), r.get("campo")) if x)
                print(f"  - {r['codice']}{' [' + dove + ']' if dove else ''}: {r['messaggio']}")
    # R08 (D02): file che verrebbero rifiutati ed esito del catalogo dei formati
    for f in esito.get("file") or []:
        catalogo = f.get("catalogo") or {}
        stato = "RIFIUTATO" if f.get("rifiutato") else (catalogo.get("esito") or "-")
        print(f"File {f['nome_file']}: {stato}"
              + (f" ({catalogo['motivo']})" if catalogo.get("motivo") else ""))
    if esito.get("firme"):
        print(f"Firme: livello {esito['firme']['livello']} ({esito['firme']['descrizione']}), "
              "verificate dopo il versamento")
    print("Non verificato qui (lo fa solo il versamento): "
          + "; ".join(esito.get("controlli_non_eseguiti") or CONTROLLI_NON_ESEGUITI))


def main(argomenti: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validatore preventivo dei versamenti VerumDoc")
    parser.add_argument("indice", nargs="?", type=Path, help="indice del versamento (JSON)")
    parser.add_argument("--file", action="append", type=Path, default=[],
                        help="file da versare (ripetibile)")
    parser.add_argument("--profilo", type=Path, help="profilo esportato (validazione senza rete)")
    parser.add_argument("--url", help="indirizzo del servizio (validazione in linea)")
    parser.add_argument("--chiave", help="chiave API (altrimenti VERUMDOC_API_KEY)")
    parser.add_argument("--scarica-profilo", type=Path, help="salva il profilo esportato qui")
    parser.add_argument("--classe", help="classe del profilo da scaricare")
    parser.add_argument("--accordo", help="accordo del profilo da scaricare")
    parser.add_argument("--json", action="store_true", help="risultato in JSON")
    a = parser.parse_args(argomenti)
    chiave = a.chiave or os.environ.get("VERUMDOC_API_KEY")
    try:
        if a.scarica_profilo:
            if not (a.url and a.classe):
                parser.error("--scarica-profilo richiede --url e --classe")
            query = {"classe": a.classe, **({"accordo": a.accordo} if a.accordo else {})}
            profilo = _richiesta(f"{a.url.rstrip('/')}/api/v1/configurazione/profilo-validatore?"
                                 + urllib.parse.urlencode(query), chiave=chiave)
            a.scarica_profilo.write_text(json.dumps(profilo, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
            print(f"Profilo salvato in {a.scarica_profilo}")
            return USCITA_CONFORME
        if a.indice is None or bool(a.profilo) == bool(a.url):
            parser.error("indicare l'indice e uno fra --profilo e --url")
        indice = json.loads(a.indice.read_text(encoding="utf-8"))
        file_info = info_file(a.file, indice if isinstance(indice, dict) else {}) \
            if a.file else None
        if a.profilo:
            profilo = json.loads(a.profilo.read_text(encoding="utf-8"))
            esito = valida_offline(indice, profilo, file_info, datetime.now(UTC))
        else:
            esito = _richiesta(f"{a.url.rstrip('/')}/api/v1/configurazione/valida",
                               chiave=chiave, corpo={"indice": indice,
                                                     "file_impronte": file_info})
    except (OSError, json.JSONDecodeError, EsportazioneNonValida, urllib.error.URLError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return USCITA_ERRORE
    _stampa(esito, a.json)
    return USCITA_CONFORME if esito.get("esito") == "CONFORME" else USCITA_NON_CONFORME


if __name__ == "__main__":
    sys.exit(main())
