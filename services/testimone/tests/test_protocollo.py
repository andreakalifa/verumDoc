# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""Regole pure del testimone (app/domain/protocollo.py, contratto K3).

Le prove di consistenza dei casi «noti» usano i vettori pubblicati della
RFC 6962 (certificate-transparency / transparency-dev: foglie "", 00, 10,
2021, 3031, 40414243, 5051525354555657, 606162636465666768696a6b6c6d6e6f).
"""

from __future__ import annotations

import base64

import pytest
from app.domain.protocollo import (
    Esito,
    Richiesta,
    RichiestaNonValida,
    RichiestaTroppoGrande,
    analizza_richiesta,
    decidi,
    nota_controfirmata,
)
from app.domain.protocollo import (
    UltimoControfirmato as U,
)

from libs.catena import nota as nota_lib

H = bytes.fromhex
RADICE_6 = H("76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef")
RADICE_8 = H("5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328")
PROVA_6_8 = [
    H("0ebc5d3437fbe2db158b9f126a1d118e308181031d0a949f8dededebc558ef6a"),
    H("ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0"),
    H("d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7"),
]
NOTA = "verumdoc.it/registro/v1\n8\nXcnaeacGWamtVZy3Ad7Zoqudgjqtl0lgz+Nw7/RgQyg=\n\n— x AAAA\n"


def _corpo(old: int, prova: list[bytes], nota: str = NOTA) -> bytes:
    righe = [f"old {old}"] + [base64.b64encode(p).decode() for p in prova]
    return ("\n".join(righe) + "\n\n" + nota).encode()


# ── analizza_richiesta ─────────────────────────────────────────────────


def test_analizza_richiesta_con_prova_ok() -> None:
    r = analizza_richiesta(_corpo(6, PROVA_6_8), max_righe_prova=128)
    assert r.old == 6 and list(r.prova) == PROVA_6_8 and r.nota == NOTA


def test_analizza_richiesta_senza_prova_ok() -> None:
    r = analizza_richiesta(_corpo(0, []), max_righe_prova=128)
    assert r.old == 0 and r.prova == () and r.nota == NOTA


@pytest.mark.parametrize(
    "corpo, motivo",
    [
        (b"old 01\n\n" + NOTA.encode(), "old"),
        (b"vecchio 1\n\n" + NOTA.encode(), "old"),
        (b"old -1\n\n" + NOTA.encode(), "old"),
        (b"old 1\nverumdoc.it/registro/v1\n", "riga vuota"),
        (b"old 1\n!!!!\n\n" + NOTA.encode(), "base64"),
        (b"old 1\nAAAA\n\n" + NOTA.encode(), "32 byte"),
        (b"old 1\n\n", "checkpoint"),
        (b"old 1\r\n\r\n" + NOTA.encode(), "fine riga"),
        (b"\xff\xfe", "UTF-8"),
    ],
)
def test_analizza_richiesta_malformata_errore(corpo: bytes, motivo: str) -> None:
    with pytest.raises(RichiestaNonValida, match=motivo):
        analizza_richiesta(corpo, max_righe_prova=128)


def test_analizza_richiesta_troppe_righe_errore() -> None:
    with pytest.raises(RichiestaTroppoGrande):
        analizza_richiesta(_corpo(6, PROVA_6_8 * 3), max_righe_prova=8)


# ── decidi ─────────────────────────────────────────────────────────────


def _r(old: int, prova: list[bytes]) -> Richiesta:
    return Richiesta(old=old, prova=tuple(prova), nota=NOTA)


def test_decidi_consistenza_rfc6962_6_8_controfirma() -> None:
    """Vettore noto RFC 6962: la prova 6→8 dimostra che l'albero di 8 foglie estende quello di 6."""
    assert (
        decidi(
            _r(6, PROVA_6_8),
            dimensione=8,
            radice=RADICE_8,
            testa=U(6, RADICE_6),
            radice_stessa_dimensione=None,
        )
        is Esito.CONTROFIRMA
    )


def test_decidi_prova_alterata_non_valida() -> None:
    alterata = [PROVA_6_8[0], PROVA_6_8[2], PROVA_6_8[1]]
    assert (
        decidi(
            _r(6, alterata),
            dimensione=8,
            radice=RADICE_8,
            testa=U(6, RADICE_6),
            radice_stessa_dimensione=None,
        )
        is Esito.PROVA_NON_VALIDA
    )


def test_decidi_radice_riscritta_non_valida() -> None:
    """Registro che ha riscritto la storia: la radice a 8 non estende quella a 6."""
    falsa = bytes(32)
    assert (
        decidi(
            _r(6, PROVA_6_8),
            dimensione=8,
            radice=falsa,
            testa=U(6, RADICE_6),
            radice_stessa_dimensione=None,
        )
        is Esito.PROVA_NON_VALIDA
    )


def test_decidi_stessa_dimensione_stessa_radice_idempotente() -> None:
    assert (
        decidi(
            _r(0, []),
            dimensione=8,
            radice=RADICE_8,
            testa=U(8, RADICE_8),
            radice_stessa_dimensione=RADICE_8,
        )
        is Esito.IDEMPOTENTE
    )


def test_decidi_stessa_dimensione_radice_diversa_fork() -> None:
    """Fork: vale qualunque «old», anche quello giusto."""
    assert (
        decidi(
            _r(8, []),
            dimensione=8,
            radice=bytes(32),
            testa=U(8, RADICE_8),
            radice_stessa_dimensione=RADICE_8,
        )
        is Esito.FORK
    )


def test_decidi_old_diverso_409() -> None:
    assert (
        decidi(
            _r(5, []),
            dimensione=8,
            radice=RADICE_8,
            testa=U(6, RADICE_6),
            radice_stessa_dimensione=None,
        )
        is Esito.OLD_NON_COINCIDE
    )


def test_decidi_old_senza_testa_diverso_da_zero_409() -> None:
    assert (
        decidi(_r(3, []), dimensione=8, radice=RADICE_8, testa=None, radice_stessa_dimensione=None)
        is Esito.OLD_NON_COINCIDE
    )


def test_decidi_checkpoint_piu_piccolo_della_testa_old_oltre() -> None:
    assert (
        decidi(
            _r(8, []),
            dimensione=6,
            radice=RADICE_6,
            testa=U(8, RADICE_8),
            radice_stessa_dimensione=None,
        )
        is Esito.OLD_OLTRE
    )


def test_decidi_primo_checkpoint_senza_prova_controfirma() -> None:
    assert (
        decidi(_r(0, []), dimensione=8, radice=RADICE_8, testa=None, radice_stessa_dimensione=None)
        is Esito.CONTROFIRMA
    )


def test_decidi_primo_checkpoint_con_prova_non_valida() -> None:
    assert (
        decidi(
            _r(0, PROVA_6_8),
            dimensione=8,
            radice=RADICE_8,
            testa=None,
            radice_stessa_dimensione=None,
        )
        is Esito.PROVA_NON_VALIDA
    )


# ── nota_controfirmata ─────────────────────────────────────────────────


def test_nota_controfirmata_sostituisce_vecchia_riga_del_testimone() -> None:
    priv_r, _ = nota_lib.genera_chiave("verumdoc-registro")
    priv_t, pub_t = nota_lib.genera_chiave("verumdoc-testimone")
    priv_vecchia, _ = nota_lib.genera_chiave("verumdoc-testimone")
    corpo = nota_lib.formatta(nota_lib.Checkpoint("o", 8, RADICE_8))
    f_r = nota_lib.analizza_firmatario(priv_r).firma(corpo)
    f_vecchia = nota_lib.analizza_firmatario(priv_vecchia).firma(corpo)
    f_t = nota_lib.analizza_firmatario(priv_t).firma(corpo)
    nota = nota_controfirmata(nota_lib.componi(corpo, [f_r, f_vecchia]), f_t, "verumdoc-testimone")
    assert nota == corpo + "\n" + f_r + "\n" + f_t + "\n"
    assert len(nota_lib.verifica(nota, [pub_t])) == 1
