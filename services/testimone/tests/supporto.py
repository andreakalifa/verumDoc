# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Registro di prova per i test del testimone: costruito SOLO con libs/catena
(nessuna copia dell'algoritmo). Può comportarsi in modo onesto o malevolo
(riscrivere una foglia già pubblicata, firmare due radici per la stessa
dimensione).
"""

from __future__ import annotations

import base64

from libs.catena import nota as nota_lib
from libs.catena import rfc6962


class RegistroDiProva:
    def __init__(self, origine: str, chiave_privata: str) -> None:
        self.origine = origine
        self.firmatario = nota_lib.analizza_firmatario(chiave_privata)
        self.foglie: list[bytes] = []

    def aggiungi(self, quante: int, *, seme: str = "pda") -> None:
        inizio = len(self.foglie)
        for i in range(inizio, inizio + quante):
            self.foglie.append(rfc6962.hash_foglia(f"{seme}-{i}".encode()))

    def riscrivi(self, indice: int, nuovi_dati: bytes) -> None:
        """Comportamento malevolo: cambia una foglia già pubblicata."""
        self.foglie[indice] = rfc6962.hash_foglia(nuovi_dati)

    def radice(self, dimensione: int | None = None) -> bytes:
        n = len(self.foglie) if dimensione is None else dimensione
        return rfc6962.radice(self.foglie[:n])

    def checkpoint(self, dimensione: int | None = None, *, radice: bytes | None = None) -> str:
        n = len(self.foglie) if dimensione is None else dimensione
        cp = nota_lib.Checkpoint(self.origine, n, radice if radice is not None else self.radice(n))
        corpo = nota_lib.formatta(cp)
        return nota_lib.componi(corpo, [self.firmatario.firma(corpo)])

    def prova(self, da: int, a: int | None = None) -> list[bytes]:
        n = len(self.foglie) if a is None else a
        return rfc6962.prova_consistenza(self.foglie, da, n)

    @staticmethod
    def corpo(old: int, prova: list[bytes], nota: str) -> bytes:
        righe = [f"old {old}"] + [base64.b64encode(p).decode() for p in prova]
        return ("\n".join(righe) + "\n\n" + nota).encode("utf-8")

    def richiesta(self, old: int, dimensione: int | None = None) -> bytes:
        n = len(self.foglie) if dimensione is None else dimensione
        prova = self.prova(old, n) if old else []
        return self.corpo(old, prova, self.checkpoint(n))
