#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
tools/sincro/verifica_ipda.py — schema XSD dell'IPdA: registrazione e verifica
(R11: decisione D03, VD-C-071; ``libs/sincro/validatore.py``).

Quattro comandi:

  stato dello schema (nessun file: dice se lo schema ufficiale è caricato)::

      python tools/sincro/verifica_ipda.py stato

  registrazione dello schema acquistato (azione del conservatore; scrive
  ``schema.json`` con l'impronta SHA-256 di ogni file ``.xsd``)::

      python tools/sincro/verifica_ipda.py registra \\
          --cartella libs/sincro/schemas/uni11386-2020 --principale <file>.xsd \\
          --versione uni11386-2020 --ufficiale --fonte "UNI 11386:2020, acquisto del …"

  impronte dei file di una cartella (da fissare nel codice in
  ``libs/sincro/schemi_ufficiali.py`` dopo l'acquisto, con revisione e decisione VD-N)::

      python tools/sincro/verifica_ipda.py impronte --cartella libs/sincro/schemas/uni11386-2020

  ``registra --ufficiale`` è accettato solo se i file coincidono con quelle impronte.

  verifica di uno o più IPdA (es. ``IndiceAIP_SInCRO.xml`` di un PdA)::

      python tools/sincro/verifica_ipda.py verifica ipda.xml [altro.xml …] [--versione …]

Codici di uscita: 0 tutti CONFORMI (o stato con schema ufficiale integro);
1 almeno uno NON CONFORME, schema alterato o non utilizzabile; 2 schema ufficiale non
disponibile o non ufficiale (nessuna conformità dichiarata); 3 errore d'uso o
registrazione rifiutata.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

QUI = Path(__file__).resolve().parent
sys.path.insert(0, str(QUI.parents[1]))

from libs.sincro.validatore import (  # noqa: E402
    CARTELLA_SCHEMI,
    EsitoXsd,
    SchemaNonValido,
    alterazioni,
    impronte_cartella,
    registra_schema,
    schemi_registrati,
    valida_ipda,
)


def _stato(cartella: Path) -> int:
    try:
        schemi = schemi_registrati(cartella)
    except SchemaNonValido as exc:
        print(json.dumps({"esito": "SCHEMA_NON_VALIDO", "messaggio": str(exc)},
                         ensure_ascii=False, indent=1))
        return 1
    voci = [{**s.riferimento(), "alterazioni": alterazioni(s)} for s in schemi.values()]
    ufficiali = [v for v in voci if v["ufficiale"] and not v["alterazioni"]]
    print(json.dumps({"cartella": str(cartella), "schemi": voci,
                      "messaggio": ("schema ufficiale disponibile" if ufficiali else
                                    "schema ufficiale non disponibile: l'IPdA non si dichiara "
                                    "conforme")}, ensure_ascii=False, indent=1))
    if any(v["alterazioni"] for v in voci):
        return 1
    return 0 if ufficiali else 2


def _verifica(file: list[Path], versione: str | None, cartella: Path) -> int:
    peggiore = 0
    for percorso in file:
        esito = valida_ipda(percorso.read_bytes(), versione=versione, cartella=cartella)
        print(json.dumps({"file": str(percorso), **esito.come_dict()}, ensure_ascii=False,
                         indent=1))
        if esito.esito in (EsitoXsd.NON_CONFORME, EsitoXsd.SCHEMA_ALTERATO,
                           EsitoXsd.SCHEMA_NON_UTILIZZABILE):
            codice = 1
        elif esito.conforme:
            codice = 0
        else:
            codice = 2
        peggiore = 1 if 1 in (peggiore, codice) else max(peggiore, codice)
    return peggiore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--schemi", type=Path, default=CARTELLA_SCHEMI,
                        help="cartella degli schemi (predefinita libs/sincro/schemas)")
    sotto = parser.add_subparsers(dest="comando", required=True)
    sotto.add_parser("stato", help="schemi registrati e loro integrità")
    reg = sotto.add_parser("registra", help="registra uno schema (impronte SHA-256)")
    reg.add_argument("--cartella", type=Path, required=True)
    reg.add_argument("--principale", required=True)
    reg.add_argument("--versione", required=True)
    reg.add_argument("--fonte", required=True)
    reg.add_argument("--ufficiale", action="store_true",
                     help="solo per lo schema UNI acquistato con le impronte fissate nel codice")
    imp = sotto.add_parser("impronte", help="impronte SHA-256 dei file di una cartella")
    imp.add_argument("--cartella", type=Path, required=True)
    ver = sotto.add_parser("verifica", help="valida uno o più IPdA")
    ver.add_argument("file", type=Path, nargs="+")
    ver.add_argument("--versione")
    args = parser.parse_args(argv)
    if args.comando == "stato":
        return _stato(args.schemi)
    if args.comando == "impronte":
        if not args.cartella.is_dir():
            print(f"errore: cartella non trovata: {args.cartella}", file=sys.stderr)
            return 3
        print(json.dumps(impronte_cartella(args.cartella), ensure_ascii=False, indent=1))
        return 0
    if args.comando == "registra":
        try:
            schema = registra_schema(args.cartella, principale=args.principale,
                                     versione=args.versione, ufficiale=args.ufficiale,
                                     fonte=args.fonte)
        except SchemaNonValido as exc:
            print(f"errore: {exc}", file=sys.stderr)
            return 3
        print(json.dumps(schema.riferimento(), ensure_ascii=False, indent=1))
        return 0
    mancanti = [str(f) for f in args.file if not f.is_file()]
    if mancanti:
        print(f"errore: file non trovati: {', '.join(mancanti)}", file=sys.stderr)
        return 3
    return _verifica(args.file, args.versione, args.schemi)


if __name__ == "__main__":
    sys.exit(main())
