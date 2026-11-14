# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/conformita/rendering.py — Markdown e HTML stampabile dei documenti generati (R15).

Il Markdown è il formato di riferimento (impronta, differenze, archivio nel WORM); l'HTML è
la versione stampabile, autonoma (nessuna risorsa esterna, nessuno script) con stile per la
stampa A4: è anche il contenuto che la firma del responsabile trasformerà in PDF/A e
firmerà (punto di aggancio: ``contenuto_da_firmare`` in ``versioni``).

Forma dei blocchi dichiarati nel Markdown (la riconosce ``tools/docs/verifica_documentazione.py``):

    > **DA COMPILARE** — <che cosa>. Fornisce: <chi>. Dove: <dove>. Campo: `<campo>`.
    > *da verificare sul testo ufficiale* — <requisito>: <motivo>.

In una tabella un valore mancante è «DA COMPILARE (vedi sotto)» e il blocco dichiarato segue
la tabella. Modulo puro.
"""
from __future__ import annotations

import html as _html

from libs.conformita.modello import (
    DA_COMPILARE,
    DA_VERIFICARE,
    Blocco,
    DaCompilare,
    DaVerificare,
    Documento,
    Elenco,
    Nota,
    Paragrafo,
    Sezione,
    Tabella,
)

CELLA_DA_COMPILARE = f"{DA_COMPILARE} (vedi sotto)"


def _cella(testo: str) -> str:
    t = CELLA_DA_COMPILARE if testo == DA_COMPILARE else testo
    return t.replace("|", "\\|").replace("\n", " ").strip() or "—"


def riga_da_compilare(b: DaCompilare) -> str:
    dove = f" Dove: {b.dove}." if b.dove else ""
    return (f"> **{DA_COMPILARE}** — {b.descrizione}. Fornisce: {b.chi}.{dove} "
            f"Campo: `{b.campo}`.")


def riga_da_verificare(b: DaVerificare) -> str:
    return f"> *{DA_VERIFICARE}* — {b.requisito}: {b.motivo}."


def blocco_markdown(b: Blocco) -> str:
    if isinstance(b, Paragrafo):
        return b.testo
    if isinstance(b, Nota):
        return f"*{b.testo}*"
    if isinstance(b, Elenco):
        return "\n".join(f"- {v}" for v in b.voci) if b.voci else "*(elenco vuoto)*"
    if isinstance(b, Tabella):
        righe = []
        if b.didascalia:
            righe.append(f"*{b.didascalia}*\n")
        righe.append("| " + " | ".join(_cella(h) for h in b.intestazioni) + " |")
        righe.append("|" + "---|" * len(b.intestazioni))
        for r in b.righe:
            righe.append("| " + " | ".join(_cella(c) for c in r) + " |")
        if not b.righe:
            righe.append("| " + " | ".join("—" for _ in b.intestazioni) + " |")
        return "\n".join(righe)
    if isinstance(b, DaCompilare):
        return riga_da_compilare(b)
    if isinstance(b, DaVerificare):
        return riga_da_verificare(b)
    raise TypeError(f"blocco sconosciuto: {type(b).__name__}")


def sezione_markdown(s: Sezione, livello: int = 2) -> str:
    parti = [f"{'#' * livello} {s.numero}. {s.titolo}" if s.numero[0].isdigit()
             else f"{'#' * livello} {s.titolo}"]
    parti += [blocco_markdown(b) for b in s.blocchi]
    parti += [sezione_markdown(x, livello + 1) for x in s.sottosezioni]
    return "\n\n".join(parti)


def corpo_markdown(doc: Documento) -> str:
    """Contenuto del documento senza intestazione di versione (base dell'impronta)."""
    parti = []
    for a in doc.avvertenze:
        parti.append(f"> **Avvertenza.** {a}")
    parti += [sezione_markdown(s) for s in doc.sezioni]
    return "\n\n".join(parti) + "\n"


# ── HTML ───────────────────────────────────────────────────────────────

_STILE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #ffffff; color: #16181b;
  font: 15px/1.5 "Helvetica Neue", Arial, sans-serif; }
main { max-width: 820px; margin: 0 auto; padding: 32px 16px 48px; }
h1 { font-size: 28px; line-height: 1.2; margin: 0 0 4px; }
h2 { font-size: 20px; margin: 32px 0 8px; border-bottom: 2px solid #16181b; padding-bottom: 4px; }
h3 { font-size: 17px; margin: 24px 0 8px; }
p.sottotitolo { color: #575c63; margin: 0 0 16px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 16px; font-size: 13px; }
th, td { border-bottom: 1px solid #e1e3e6; text-align: left; padding: 6px 8px;
  vertical-align: top; overflow-wrap: anywhere; }
th { border-bottom: 2px solid #16181b; font-weight: 700; }
caption { caption-side: top; text-align: left; font-style: italic; color: #575c63;
  padding-bottom: 4px; }
code { font: 12px ui-monospace, Menlo, Consolas, monospace; overflow-wrap: anywhere; }
.da-compilare { border-left: 4px solid #b42318; background: #fdf1ef; padding: 8px 12px;
  margin: 8px 0; }
.da-verificare { border-left: 4px solid #a63d00; background: #fff4ea; padding: 8px 12px;
  margin: 8px 0; }
.avvertenza { border: 2px solid #16181b; padding: 8px 12px; margin: 12px 0; }
.nota { color: #575c63; font-style: italic; }
dl.edizione { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px;
  font-size: 13px; margin: 0 0 16px; }
dl.edizione dt { font-weight: 700; }
dl.edizione dd { margin: 0; overflow-wrap: anywhere; }
@page { size: A4; margin: 18mm 16mm; }
@media print {
  main { max-width: none; padding: 0; }
  h2 { break-after: avoid; }
  table, .da-compilare, .da-verificare { break-inside: avoid; }
}
"""


def _e(testo: str) -> str:
    """Testo in HTML con i `codici` resi come <code>."""
    parti = testo.split("`")
    return "".join(f"<code>{_html.escape(p)}</code>" if i % 2 else _html.escape(p)
                   for i, p in enumerate(parti))


def blocco_html(b: Blocco) -> str:
    if isinstance(b, Paragrafo):
        return f"<p>{_e(b.testo)}</p>"
    if isinstance(b, Nota):
        return f'<p class="nota">{_e(b.testo)}</p>'
    if isinstance(b, Elenco):
        return "<ul>" + "".join(f"<li>{_e(v)}</li>" for v in b.voci) + "</ul>"
    if isinstance(b, Tabella):
        testa = "".join(f'<th scope="col">{_e(h)}</th>' for h in b.intestazioni)
        corpo = "".join("<tr>" + "".join(
            f"<td>{_e(CELLA_DA_COMPILARE if c == DA_COMPILARE else c)}</td>" for c in r)
            + "</tr>" for r in b.righe)
        didascalia = f"<caption>{_e(b.didascalia)}</caption>" if b.didascalia else ""
        return f"<table>{didascalia}<thead><tr>{testa}</tr></thead><tbody>{corpo}</tbody></table>"
    if isinstance(b, DaCompilare):
        dove = f" Dove: {_e(b.dove)}." if b.dove else ""
        return (f'<aside class="da-compilare"><strong>{DA_COMPILARE}</strong> — '
                f"{_e(b.descrizione)}. Fornisce: {_e(b.chi)}.{dove} "
                f"Campo: <code>{_html.escape(b.campo)}</code>.</aside>")
    if isinstance(b, DaVerificare):
        return (f'<aside class="da-verificare"><em>{DA_VERIFICARE}</em> — '
                f"{_e(b.requisito)}: {_e(b.motivo)}.</aside>")
    raise TypeError(f"blocco sconosciuto: {type(b).__name__}")


def sezione_html(s: Sezione, livello: int = 2) -> str:
    titolo = f"{s.numero}. {s.titolo}" if s.numero[0].isdigit() else s.titolo
    parti = [f'<section id="{_html.escape(s.id)}"><h{livello}>{_e(titolo)}</h{livello}>']
    parti += [blocco_html(b) for b in s.blocchi]
    parti += [sezione_html(x, min(livello + 1, 6)) for x in s.sottosezioni]
    parti.append("</section>")
    return "\n".join(parti)


def documento_html(doc: Documento, intestazione: list[tuple[str, str]]) -> str:
    voci = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in intestazione)
    avvertenze = "".join(f'<p class="avvertenza"><strong>Avvertenza.</strong> {_e(a)}</p>'
                         for a in doc.avvertenze)
    corpo = "\n".join(sezione_html(s) for s in doc.sezioni)
    return ("<!doctype html>\n<html lang=\"it\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{_html.escape(doc.titolo)}</title><style>{_STILE}</style></head>"
            f"<body><main><h1>{_e(doc.titolo)}</h1><p class=\"sottotitolo\">"
            f"{_e(doc.sottotitolo)}</p><dl class=\"edizione\">{voci}</dl>{avvertenze}"
            f"{corpo}</main></body></html>\n")


__all__ = ["CELLA_DA_COMPILARE", "blocco_markdown", "corpo_markdown", "documento_html",
           "riga_da_compilare", "riga_da_verificare", "sezione_markdown"]
