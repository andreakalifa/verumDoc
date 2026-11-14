# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/zip_limiti.py — limiti di decompressione per gli ZIP del versante
(revisione R08, X4: zip bomb).

Ogni lettura di un membro di uno ZIP che viene da fuori (busta ASiC, DOCX/ODT
esaminati dalla rilevazione delle firme, PdD ricevuti nel riversamento) passa
da qui:

  - dimensione DICHIARATA (``ZipInfo.file_size``) entro il massimo del chiamante;
  - rapporto di compressione dichiarato entro ``RAPPORTO_MAX`` (oltre una soglia
    minima, perché i file piccoli e molto ripetitivi sono normali);
  - dimensione REALE: si legge al più ``massimo + 1`` byte (``zipfile`` tronca
    comunque alla dimensione dichiarata e verifica il CRC: un membro che
    dichiara meno di quanto contiene è ``BadZipFile``);
  - revisione Y1: solo i metodi STORED e DEFLATED. Con BZIP2 e LZMA CPython
    decomprime per intero ogni blocco letto PRIMA di troncare alla dimensione
    dichiarata (``max_length`` solo per DEFLATE): 1 KB di bzip2 = GiB in
    memoria. Gli altri metodi si rifiutano senza decomprimere nulla.
"""
from __future__ import annotations

import zipfile

#: Rapporto massimo fra dimensione dichiarata e compressa di un membro.
RAPPORTO_MAX = 200
#: Revisione Y1: metodi di compressione ammessi (decompressione limitata).
METODI_AMMESSI = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
#: Sotto questa dimensione dichiarata il rapporto non si controlla (byte).
SOGLIA_RAPPORTO = 1024 * 1024


class MembroZipNonAmmesso(ValueError):
    """Membro oltre i limiti di decompressione (dimensione o rapporto)."""


def controlla_membro(info: zipfile.ZipInfo, *, massimo_byte: int | None = None,
                     rapporto_max: int = RAPPORTO_MAX, solo_stored: bool = False) -> None:
    """Solleva ``MembroZipNonAmmesso`` se il membro supera i limiti dichiarati o
    usa un metodo di compressione non ammesso (``solo_stored``: solo STORED)."""
    ammessi = frozenset({zipfile.ZIP_STORED}) if solo_stored else METODI_AMMESSI
    if info.compress_type not in ammessi:
        raise MembroZipNonAmmesso(
            f"membro {info.filename!r}: metodo di compressione {info.compress_type} non ammesso")
    if massimo_byte is not None and info.file_size > massimo_byte:
        raise MembroZipNonAmmesso(
            f"membro {info.filename!r}: {info.file_size} byte dichiarati, massimo {massimo_byte}")
    if info.file_size > SOGLIA_RAPPORTO and info.compress_type != zipfile.ZIP_STORED:
        rapporto = info.file_size / max(info.compress_size, 1)
        if rapporto > rapporto_max:
            raise MembroZipNonAmmesso(
                f"membro {info.filename!r}: rapporto di compressione {rapporto:.0f}:1 "
                f"oltre {rapporto_max}:1")


def leggi_membro(archivio: zipfile.ZipFile, nome: str | zipfile.ZipInfo, *,
                 massimo_byte: int, solo_stored: bool = False) -> bytes:
    """Contenuto di un membro entro ``massimo_byte`` (dichiarati e reali)."""
    info = nome if isinstance(nome, zipfile.ZipInfo) else archivio.getinfo(nome)
    controlla_membro(info, massimo_byte=massimo_byte, solo_stored=solo_stored)
    with archivio.open(info) as f:
        dati = f.read(massimo_byte + 1)
    if len(dati) > massimo_byte:
        raise MembroZipNonAmmesso(f"membro {info.filename!r} oltre {massimo_byte} byte")
    return dati


__all__ = [
    "METODI_AMMESSI", "RAPPORTO_MAX", "SOGLIA_RAPPORTO", "MembroZipNonAmmesso", "controlla_membro",
           "leggi_membro"]
