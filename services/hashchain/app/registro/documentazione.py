# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Archivio WORM delle versioni della documentazione del servizio (R15, VD-C-177).

Servizio: hashchain (unico con la scrittura sotto ``registro/``; login membro di
``verumdoc_documentazione_archivio``, migrazione ``20261111_058``). A ogni giro del registro,
con il lease, le versioni di ``documentazione_versioni`` non ancora archiviate vanno nel WORM
come oggetti del registro (retention del registro, rinnovata «mai in riduzione», R12a):

    registro/documentazione/<tipo>/<impronta_sha256>.md     Markdown della versione
    registro/documentazione/<tipo>/<impronta_html>.html     HTML stampabile

La chiave contiene l'impronta del contenuto (come le buste di R13a): la scrittura è
idempotente (dopo un crash fra la scrittura e l'aggiornamento della riga la seconda
scrittura ritrova l'oggetto identico, ``archivio_worm.scrivi``; un oggetto diverso con la
stessa chiave è ``ContenutoWormDiverso``: il ciclo apre il blocco persistente del registro,
come per gli altri oggetti, e non si registra nulla). Prima di scrivere si RICALCOLANO le
impronte dai byte della riga: se non tornano non si archivia (allarme nei log, la riga resta
da archiviare). L'aggiornamento della riga (chiavi e versioni WORM, ``archiviata_il``) avviene
nella stessa transazione della verifica del lease (fencing) e il trigger del database lo
ammette una sola volta, solo a questo gruppo, solo con chiavi che contengono le impronte.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.registro import archivio, archivio_worm
from app.registro.metriche import metriche
from libs.shared.db import get_async_session

logger = structlog.get_logger(__name__)

LIMITE_PER_GIRO = 20
PREFISSO = "registro/documentazione"


class ImprontaDiversa(RuntimeError):
    """I byte della versione non hanno l'impronta registrata: non si archiviano."""


def chiavi(riga: dict[str, Any]) -> tuple[str, str]:
    tipo = str(riga["tipo"]).lower()
    return (f"{PREFISSO}/{tipo}/{str(riga['impronta_sha256']).strip()}.md",
            f"{PREFISSO}/{tipo}/{str(riga['impronta_html']).strip()}.html")


def _controlla(riga: dict[str, Any]) -> tuple[bytes, bytes]:
    md, html = bytes(riga["markdown"]), bytes(riga["html"])
    if hashlib.sha256(md).hexdigest() != str(riga["impronta_sha256"]).strip() \
            or hashlib.sha256(html).hexdigest() != str(riga["impronta_html"]).strip():
        raise ImprontaDiversa(f"versione {riga['id']}: impronta diversa dal contenuto")
    return md, html


async def da_archiviare(limite: int = LIMITE_PER_GIRO) -> list[dict[str, Any]]:
    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT id, tipo, versione, markdown, html, impronta_sha256, impronta_html "
            "FROM public.documentazione_versioni WHERE archiviata_il IS NULL "
            "ORDER BY created_at LIMIT :l"), {"l": limite})).mappings().all()
    return [dict(r) for r in righe]


async def archivia(*, detentore: str, adesso: datetime,
                   limite: int = LIMITE_PER_GIRO) -> dict[str, int]:
    """Un passo del ciclo del registro. Un errore su una versione non ferma le altre."""
    esito = {"archiviate": 0, "errori": 0}
    for riga in await da_archiviare(limite):
        try:
            md, html = _controlla(riga)
            chiave_md, chiave_html = chiavi(riga)
            _, versione_md = await archivio_worm.scrivi(chiave_md, md,
                                                        "text/markdown; charset=utf-8", adesso)
            _, versione_html = await archivio_worm.scrivi(chiave_html, html,
                                                          "text/html; charset=utf-8", adesso)
            async with get_async_session() as session:
                await archivio.stato_con_lease(session, detentore, adesso)
                await session.execute(text(
                    "UPDATE public.documentazione_versioni SET worm_chiave_md = :cm, "
                    "worm_versione_md = :vm, worm_chiave_html = :ch, worm_versione_html = :vh, "
                    "archiviata_il = :a WHERE id = :id AND archiviata_il IS NULL"),
                    {"cm": chiave_md, "vm": versione_md, "ch": chiave_html, "vh": versione_html,
                     "a": adesso, "id": riga["id"]})
            esito["archiviate"] += 1
        except (archivio.LeasePerso, archivio_worm.ContenutoWormDiverso):
            raise                      # lease perso; stesso nome con altro contenuto = blocco
        except Exception as exc:  # noqa: BLE001 — la versione resta da archiviare
            esito["errori"] += 1
            logger.error("documentazione_non_archiviata", versione_id=str(riga["id"]),
                         errore=type(exc).__name__)
    async with get_async_session() as session:
        restanti = (await session.execute(text(
            "SELECT count(*) FROM public.documentazione_versioni WHERE archiviata_il IS NULL"
        ))).scalar_one()
    metriche().documentazione_da_archiviare.set(int(restanti))
    return esito


__all__ = ["ImprontaDiversa", "archivia", "chiavi", "da_archiviare"]
