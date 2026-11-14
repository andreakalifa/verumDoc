# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/versioni.py — versione, impronta, fonti e differenze di una generazione (R15).

Ogni generazione di un documento ha:
  - ``impronta_contenuto``: SHA-256 del corpo Markdown (senza intestazione di versione):
    a parità di contenuto la generazione NON crea una versione nuova (idempotenza);
  - ``sezioni``: per ogni sezione di primo livello id, numero, titolo, impronta del suo
    Markdown, numero di dati «DA COMPILARE» e di requisiti «da verificare»;
  - ``differenze`` rispetto alla versione precedente: sezioni aggiunte, tolte, cambiate;
  - l'intestazione con versione, data, impronta, versione precedente, sezioni cambiate e
    l'elenco delle fonti (con le impronte dei file del repository).
Il file finale (Markdown con intestazione) ha la sua impronta SHA-256 (``impronta_sha256``):
è quella che il database riverifica e che compare nella chiave dell'oggetto nel WORM.

``contenuto_da_firmare`` è il PUNTO DI AGGANCIO della firma del responsabile (R16): i byte
dell'HTML stampabile di una versione, con nome e impronta; la firma (PDF/A firmato) è
compito di quel rilascio e non cambia la versione registrata qui.

Modulo puro.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from libs.conformita.modello import Documento
from libs.conformita.rendering import corpo_markdown, documento_html, sezione_markdown

NOMI_FILE = {
    "MANUALE_CONSERVAZIONE": "manuale_conservazione",
    "PIANO_SICUREZZA": "piano_sicurezza",
    "GUIDA_FUNZIONALITA": "GUIDA_FUNZIONALITA",
    "CATALOGO_ROUTE": "route",
}


def sha256(testo: str | bytes) -> str:
    return hashlib.sha256(testo.encode("utf-8") if isinstance(testo, str) else testo).hexdigest()


def impronte_sezioni(doc: Documento) -> list[dict[str, Any]]:
    return [{"id": s.id, "numero": s.numero, "titolo": s.titolo,
             "impronta": sha256(sezione_markdown(s)),
             "da_compilare": len(s.da_compilare()), "da_verificare": len(s.da_verificare())}
            for s in doc.sezioni]


def differenze(attuali: list[dict[str, Any]],
               precedenti: list[dict[str, Any]] | None) -> dict[str, list[dict[str, str]]]:
    """Sezioni aggiunte, tolte e cambiate (per ``id`` stabile)."""
    if precedenti is None:
        return {"aggiunte": [], "tolte": [], "cambiate": []}
    prima = {s["id"]: s for s in precedenti}
    ora = {s["id"]: s for s in attuali}

    def voce(s: dict[str, Any]) -> dict[str, str]:
        return {"id": s["id"], "numero": s["numero"], "titolo": s["titolo"]}

    return {"aggiunte": [voce(s) for s in attuali if s["id"] not in prima],
            "tolte": [voce(s) for s in precedenti if s["id"] not in ora],
            "cambiate": [voce(s) for s in attuali
                         if s["id"] in prima and prima[s["id"]]["impronta"] != s["impronta"]]}


@dataclass
class Edizione:
    """Dati di versione. ``versione`` None = edizione del repository (nessuna data)."""

    versione: int | None = None
    generata_il: str | None = None
    precedente: dict[str, Any] | None = None   # {"versione", "impronta_contenuto", "sezioni"}
    fonti: dict[str, str] = field(default_factory=dict)   # fonte → impronta o descrizione


@dataclass
class Generato:
    tipo: str
    titolo: str
    nome_file: str
    markdown: str
    html: str
    impronta_contenuto: str
    impronta_sha256: str
    impronta_html: str
    sezioni: list[dict[str, Any]]
    differenze: dict[str, list[dict[str, str]]]
    da_compilare: list[dict[str, Any]]
    da_verificare: list[dict[str, str]]
    fonti: dict[str, str]
    versione: int | None
    generata_il: str | None

    def riepilogo(self) -> dict[str, Any]:
        return {"tipo": self.tipo, "titolo": self.titolo, "versione": self.versione,
                "generata_il": self.generata_il, "impronta_contenuto": self.impronta_contenuto,
                "impronta_sha256": self.impronta_sha256, "sezioni": self.sezioni,
                "differenze": self.differenze, "fonti": self.fonti,
                "da_compilare": self.da_compilare, "da_verificare": self.da_verificare}


def _testo_differenze(diff: dict[str, list[dict[str, str]]]) -> str:
    parti = []
    for chiave, etichetta in (("cambiate", "cambiate"), ("aggiunte", "aggiunte"),
                              ("tolte", "tolte")):
        if diff[chiave]:
            parti.append(f"{etichetta}: " + "; ".join(f"{s['numero']} {s['titolo']}"
                                                      for s in diff[chiave]))
    return " — ".join(parti) or "nessuna sezione cambiata"


def intestazione(doc: Documento, ed: Edizione, impronta: str,
                 diff: dict[str, list[dict[str, str]]], n_da_compilare: int,
                 n_da_verificare: int) -> list[tuple[str, str]]:
    if ed.versione is None:
        voci = [("Edizione", "del repository: versione, data e firma si assegnano alla "
                 "generazione dalla console «Documentazione del servizio»")]
    else:
        voci = [("Versione", str(ed.versione)), ("Data di generazione", ed.generata_il or "")]
    voci.append(("Impronta del contenuto (SHA-256)", impronta))
    if ed.precedente:
        voci.append(("Versione precedente", f"{ed.precedente['versione']} (impronta "
                     f"{ed.precedente['impronta_contenuto']})"))
        voci.append(("Differenze rispetto alla precedente", _testo_differenze(diff)))
    elif ed.versione is not None:
        voci.append(("Versione precedente", "nessuna (prima versione)"))
    voci.append(("Dati da compilare", str(n_da_compilare)))
    voci.append(("Requisiti da verificare sul testo ufficiale", str(n_da_verificare)))
    voci.append(("Stato della firma", "non firmato: la firma del responsabile si appone "
                 "sulla versione stampabile"))
    return voci


def _fonti_markdown(fonti: dict[str, str]) -> str:
    if not fonti:
        return ""
    righe = ["<details><summary>Fonti della generazione</summary>", ""]
    righe += [f"- {k} — {v}" for k, v in sorted(fonti.items())]
    righe += ["", "</details>"]
    return "\n".join(righe)


def componi(doc: Documento, ed: Edizione) -> Generato:
    corpo = corpo_markdown(doc)
    impronta = sha256(corpo)
    sezioni = impronte_sezioni(doc)
    diff = differenze(sezioni, ed.precedente.get("sezioni") if ed.precedente else None)
    mancanti, verificare = doc.da_compilare(), doc.da_verificare()
    fonti = dict(ed.fonti) or dict.fromkeys(doc.fonti(), "dichiarata nelle sezioni")
    testa = intestazione(doc, ed, impronta, diff, len(mancanti), len(verificare))
    md_testa = [f"# {doc.titolo}", "", f"*{doc.sottotitolo}*", "",
                "| Voce | Valore |", "|---|---|"]
    md_testa += [f"| {k} | {v.replace('|', chr(92) + '|')} |" for k, v in testa]
    blocco_fonti = _fonti_markdown(fonti)
    markdown = "\n".join(md_testa) + "\n\n" + (blocco_fonti + "\n\n" if blocco_fonti else "") \
        + corpo
    html = documento_html(doc, testa + [("Fonti", "; ".join(sorted(fonti)))])
    return Generato(
        tipo=doc.tipo, titolo=doc.titolo, nome_file=NOMI_FILE.get(doc.tipo, doc.tipo.lower()),
        markdown=markdown, html=html, impronta_contenuto=impronta,
        impronta_sha256=sha256(markdown), impronta_html=sha256(html), sezioni=sezioni,
        differenze=diff, da_compilare=doc.riepilogo_da_compilare(),
        da_verificare=[{"requisito": v.requisito, "motivo": v.motivo} for v in verificare],
        fonti=fonti, versione=ed.versione, generata_il=ed.generata_il)


def contenuto_da_firmare(tipo: str, versione: int, html: bytes) -> dict[str, Any]:
    """Punto di aggancio per la firma del responsabile (R16): che cosa si firma."""
    return {"nome": f"{NOMI_FILE.get(tipo, tipo.lower())}_v{versione}.html",
            "media_type": "text/html; charset=utf-8", "impronta_sha256": sha256(html),
            "contenuto": html}


__all__ = ["NOMI_FILE", "Edizione", "Generato", "componi", "contenuto_da_firmare",
           "differenze", "impronte_sezioni", "intestazione", "sha256"]
