# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lettore dei file del PdV su contenuti in memoria, per i test: stessa interfaccia
e stessi controlli di ``LettoreS3`` (lettura comunque a blocchi, errori dello
storage come ``ErroreVerificaTransitorio``).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.services.lettura_file import FileLetto, FileOltreLimite, RiferimentoFile, calcolatori
from app.validators.base import ErroreVerificaTransitorio


def riferimento(nome: str, *, bucket: str = "pdv",
                chiave: str | None = None, **altri) -> RiferimentoFile:
    """Riferimento di prova (chiave predefinita = nome del file)."""
    return RiferimentoFile(nome_file=nome, bucket=bucket, chiave=chiave or nome, **altri)


class LettoreMemoria:
    """Contenuti indicizzati per (bucket, chiave); registra i file letti per intero."""

    def __init__(self, contenuti: Mapping[tuple[str, str], bytes], *,
                 dimensione_blocco: int = 7) -> None:
        self.contenuti = dict(contenuti)
        self.dimensione_blocco = dimensione_blocco
        self.letture: list[str] = []

    def _contenuto(self, rif: RiferimentoFile) -> bytes:
        try:
            return self.contenuti[(rif.bucket, rif.chiave)]
        except KeyError as exc:
            raise ErroreVerificaTransitorio(
                f"Lettura dallo storage non riuscita per {rif.nome_file!r}: NoSuchKey"
            ) from exc

    async def dimensione(self, rif: RiferimentoFile) -> int:
        return len(self._contenuto(rif))

    async def leggi(
        self, rif: RiferimentoFile, *, algoritmi: Sequence[str], limite_byte: int | None = None,
    ) -> FileLetto:
        contenuto = self._contenuto(rif)
        self.letture.append(rif.nome_file)
        calc = calcolatori(algoritmi)
        letti = 0
        for i in range(0, len(contenuto), self.dimensione_blocco):
            blocco = contenuto[i:i + self.dimensione_blocco]
            letti += len(blocco)
            if limite_byte is not None and letti > limite_byte:
                raise FileOltreLimite(limite_byte, letti)
            for c in calc.values():
                c.update(blocco)
        return FileLetto(dimensione=letti, impronte={n: c.hexdigest() for n, c in calc.items()})

    async def copia_in(self, rif: RiferimentoFile, destinazione, *, limite_byte: int) -> int:
        contenuto = self._contenuto(rif)
        if len(contenuto) > limite_byte:
            raise FileOltreLimite(limite_byte, len(contenuto))
        destinazione.write_bytes(contenuto)
        self.letture.append(f"copia:{rif.nome_file}")
        return len(contenuto)

    async def testa_coda(self, rif: RiferimentoFile, n: int) -> tuple[bytes, bytes]:
        contenuto = self._contenuto(rif)
        self.letture.append(f"testa_coda:{rif.nome_file}")
        return contenuto[:n], contenuto[-n:]

    async def contiene(self, rif: RiferimentoFile, marcatore: bytes) -> bool:
        from libs.shared.firme.rilevazione import cerca_in_blocchi

        contenuto = self._contenuto(rif)
        self.letture.append(f"contiene:{rif.nome_file}")
        return cerca_in_blocchi((contenuto[i:i + self.dimensione_blocco] for i in range(
            0, len(contenuto), self.dimensione_blocco)), marcatore)


def lettore_per_nomi(file_map: Mapping[str, bytes], *, bucket: str = "pdv"
                     ) -> tuple[dict[str, RiferimentoFile], LettoreMemoria]:
    """Riferimenti e lettore per ``{nome_file: contenuto}`` (chiave = nome del file)."""
    riferimenti = {n: riferimento(n, bucket=bucket) for n in file_map}
    return riferimenti, LettoreMemoria({(bucket, n): c for n, c in file_map.items()})
