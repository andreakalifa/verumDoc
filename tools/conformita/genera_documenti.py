# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
tools/conformita/genera_documenti.py — documentazione di conformità generata (R15, VD-C-177).

Due usi.

1. Edizione del repository (senza database), allineata a ogni rilascio (REGOLE §0.2, §28):

    python tools/conformita/genera_documenti.py               # rigenera
    python tools/conformita/genera_documenti.py --controlla   # uscita 1 se non allineata

   Scrive (o controlla) ``docs/manuale/manuale_conservazione.md``,
   ``docs/manuale/piano_sicurezza.md``, ``GUIDA_FUNZIONALITA.md``, ``docs/catalogo/route.md``,
   ``docs/catalogo/permessi.md`` e la fotografia del repository
   ``libs/conformita/dati/fonti_repository.json`` che la console usa. I dati della
   configurazione in esercizio risultano «DA COMPILARE».

2. Generazione versionata con il database (sola lettura, login dell'auditor):

    python tools/conformita/genera_documenti.py --database-url postgresql+asyncpg://vd_auditor@… \\
        --uscita /percorso/documentazione

   Nella cartella d'uscita scrive per ogni documento cambiato ``<nome>_v<N>.md`` e
   ``<nome>_v<N>.html`` (stampabile) e aggiorna ``indice.json`` (versione, data, impronte,
   fonti, sezioni, differenze rispetto alla versione precedente). Con lo stesso contenuto
   della versione precedente non crea una versione nuova. Le variabili ``CONSERVATORE_*``
   si leggono dall'ambiente. La copia da consegnare e da firmare si genera dalla console
   («Documentazione del servizio»), che archivia le versioni nel WORM.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

RADICE = Path(__file__).resolve().parents[2]
if str(RADICE) not in sys.path:
    sys.path.insert(0, str(RADICE))

from libs.conformita import database, repository  # noqa: E402
from libs.conformita.documenti import Fatti, matrice_permessi  # noqa: E402
from libs.conformita.generazione import genera  # noqa: E402
from libs.conformita.modello import CATALOGO_ROUTE, GUIDA, MANUALE, PIANO_SICUREZZA  # noqa: E402
from libs.conformita.rendering import blocco_markdown  # noqa: E402

INTESTAZIONE = ("<!-- File generato da tools/conformita/genera_documenti.py: non modificare a "
                "mano (REGOLE_SVILUPPO §0.2, §28). -->\n\n")
DESTINAZIONI = {
    MANUALE: "docs/manuale/manuale_conservazione.md",
    PIANO_SICUREZZA: "docs/manuale/piano_sicurezza.md",
    GUIDA: "GUIDA_FUNZIONALITA.md",
    CATALOGO_ROUTE: "docs/catalogo/route.md",
}
PERMESSI = "docs/catalogo/permessi.md"
FOTOGRAFIA = "libs/conformita/dati/fonti_repository.json"


def permessi_md(repo: dict) -> str:  # type: ignore[type-arg]
    p = repo["permessi"]
    return (INTESTAZIONE + "# Catalogo dei permessi e matrice ruoli × permessi\n\n"
            f"{len(p['permessi'])} permessi, {len(p['mai_delegabili'])} mai delegabili "
            "(`libs/verumdoc_rules/permessi.py`; controllo centrale in "
            "`libs/shared/security/permessi.py`). Concedibili ai ruoli personalizzati, se il "
            "conservatore lo consente: " + ", ".join(f"`{c}`" for c in p["concedibili"])
            + ".\n\n" + blocco_markdown(matrice_permessi(repo)) + "\n")


def edizione_repository(radice: Path = RADICE) -> dict[str, str]:
    """{percorso relativo: contenuto atteso} dell'edizione del repository."""
    repo = repository.raccogli(radice)
    fatti = Fatti(repo, database.vuoti(), {})
    attesi = {rel: INTESTAZIONE + g.markdown for tipo, rel in DESTINAZIONI.items()
              for g in [genera(fatti, tipi=(tipo,))[tipo]]}
    attesi[PERMESSI] = permessi_md(repo)
    attesi[FOTOGRAFIA] = repository.serializza(repo)
    return attesi


def scrivi_o_controlla(controlla: bool, radice: Path = RADICE) -> list[str]:
    vecchi = []
    for rel, atteso in edizione_repository(radice).items():
        percorso = radice / rel
        attuale = percorso.read_text(encoding="utf-8") if percorso.exists() else None
        if attuale == atteso:
            continue
        if controlla:
            vecchi.append(rel)
        else:
            percorso.parent.mkdir(parents=True, exist_ok=True)
            percorso.write_text(atteso, encoding="utf-8")
            print(f"scritto {rel}")
    return vecchi


async def _fatti_database(url: str) -> dict:  # type: ignore[type-arg]
    from sqlalchemy.ext.asyncio import create_async_engine

    motore = create_async_engine(url)
    try:
        async with motore.connect() as connessione:
            async with connessione.begin():
                return await database.raccogli(connessione)
    finally:
        await motore.dispose()


def genera_versioni(url: str, uscita: Path, adesso: datetime | None = None) -> dict:  # type: ignore[type-arg]
    """Generazione versionata nella cartella ``uscita`` (indice.json = storico)."""
    uscita.mkdir(parents=True, exist_ok=True)
    indice_percorso = uscita / "indice.json"
    indice = json.loads(indice_percorso.read_text("utf-8")) if indice_percorso.exists() else {}
    precedenti = {tipo: voci[-1] for tipo, voci in indice.items() if voci}
    fatti = Fatti(repository.raccogli(), asyncio.run(_fatti_database(url)), dict(os.environ))
    generata_il = (adesso or datetime.now(UTC)).replace(microsecond=0).isoformat()
    esito = {"generata_il": generata_il, "nuove": [], "invariate": []}
    for tipo, g in genera(fatti, generata_il=generata_il, precedenti=precedenti).items():
        if g.versione is None:
            esito["invariate"].append(tipo)
            continue
        base = uscita / f"{g.nome_file}_v{g.versione}"
        base.with_suffix(".md").write_text(g.markdown, encoding="utf-8")
        base.with_suffix(".html").write_text(g.html, encoding="utf-8")
        indice.setdefault(tipo, []).append({**g.riepilogo(), "impronta_html": g.impronta_html,
                                            "file": base.name})
        esito["nuove"].append({"tipo": tipo, "versione": g.versione,
                               "impronta_sha256": g.impronta_sha256})
    indice_percorso.write_text(json.dumps(indice, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return esito


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--controlla", "--verifica", action="store_true", dest="controlla",
                    help="non scrive; uscita 1 se l'edizione del repository non è allineata")
    ap.add_argument("--database-url", default=os.environ.get("VD_CONFORMITA_DATABASE_URL"),
                    help="URL del database (login dell'auditor, sola lettura)")
    ap.add_argument("--uscita", type=Path, help="cartella della generazione versionata")
    a = ap.parse_args(argv)
    if a.database_url or a.uscita:
        if not (a.database_url and a.uscita):
            print("servono insieme --database-url e --uscita", file=sys.stderr)
            return 2
        print(json.dumps(genera_versioni(a.database_url, a.uscita), ensure_ascii=False,
                         indent=1))
        return 0
    vecchi = scrivi_o_controlla(a.controlla)
    if vecchi:
        print("documentazione generata non allineata (rigenerare con python "
              "tools/conformita/genera_documenti.py): " + ", ".join(vecchi), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
