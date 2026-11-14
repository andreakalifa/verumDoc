# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/domain/documenti.py — documenti prodotti dalle pratiche: verbale di scarto,
atti di annullamento e di rettifica, manifesto del riversamento.

Riferimenti: contratto Q2 di R05 (verbale in WORM, RdV della rettifica che cita
l'originale); REGOLE_SVILUPPO §8.9 (formati di scambio versionati: namespace con
numero di versione), §12.8 (ordine totale: stesso contenuto, stessi byte).

Modulo PURO. Il verbale si scrive a pezzi su un ``scrivi(str)`` fornito dal
chiamante (file temporaneo con impronta calcolata durante la scrittura): un
verbale di 100.000 PdA non sta mai tutto in memoria. A parità di dati i byte
sono identici (nessun orologio, ordine dato dal chiamante), così la scrittura
nel WORM ripetuta dopo un crash riconosce il documento già conservato.

Formati propri di VerumDoc (nessuno schema AgID li prescrive):
  - ``https://schemi.verumdoc.it/ciclo-vita/verbale-scarto/1``
  - ``https://schemi.verumdoc.it/ciclo-vita/atto/1``
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from xml.sax.saxutils import escape, quoteattr

NS_VERBALE = "https://schemi.verumdoc.it/ciclo-vita/verbale-scarto/1"
NS_ATTO = "https://schemi.verumdoc.it/ciclo-vita/atto/1"
FORMATO_MANIFESTO = "verumdoc-manifesto-riversamento-1"

Scrivi = Callable[[str], Any]


def _t(valore: Any) -> str:
    return escape("" if valore is None else str(valore))


def _el(nome: str, valore: Any, rientro: str = "  ") -> str:
    return f"{rientro}<{nome}>{_t(valore)}</{nome}>\n"


def scrivi_verbale_scarto(
    scrivi: Scrivi, *, pratica: Mapping[str, Any], ente: Mapping[str, Any],
    conservatore: str, cronologia: Iterable[Mapping[str, Any]],
    allegati: Iterable[Mapping[str, Any]], elementi: Iterable[Mapping[str, Any]],
    conteggi: Mapping[str, int],
) -> None:
    """Verbale di scarto (XML). ``elementi`` in ordine totale (id della riga):
    ``{aip_id, urn, esito, motivo}`` (per NON_COMPLETATO anche
    ``versioni_eliminate``, ``versioni_rimaste``, ``chiavi_eliminate``,
    ``chiavi_rimaste``: campioni). ``pratica``: id, motivo, criteri,
    eseguita_il (istante fissato prima della scrittura), giro."""
    scrivi('<?xml version="1.0" encoding="UTF-8"?>\n')
    scrivi(f"<VerbaleScarto xmlns={quoteattr(NS_VERBALE)} versione=\"1\">\n")
    scrivi(_el("IdentificativoPratica", pratica["id"]))
    scrivi(_el("Conservatore", conservatore or "non indicato"))
    scrivi("  <Ente>\n")
    scrivi(_el("Identificativo", ente.get("id"), "    "))
    scrivi(_el("Denominazione", ente.get("denominazione"), "    "))
    if ente.get("codice_ipa"):
        scrivi(_el("CodiceIPA", ente["codice_ipa"], "    "))
    scrivi("  </Ente>\n")
    scrivi(_el("Motivo", pratica.get("motivo")))
    scrivi(_el("Criteri", json.dumps(pratica.get("criteri") or {}, sort_keys=True,
                                      ensure_ascii=False)))
    scrivi(_el("ImprontaPiano", pratica.get("impronta_piano")))
    scrivi(_el("EseguitoIl", pratica.get("eseguita_il")))
    # Revisione 2 di R05 (S3): giro dell'esecuzione (>1: verbale integrativo di una
    # ripresa) ed esito complessivo
    scrivi(_el("Giro", pratica.get("giro") or 1))
    incompleti = int(conteggi.get("NON_COMPLETATO") or 0)
    scrivi(_el("Esito", "ESEGUITO_CON_ECCEZIONI" if incompleti else "ESEGUITO"))
    scrivi("  <Iter>\n")
    for passo in cronologia:
        scrivi("    <Passo>\n")
        for nome, chiave in (("Da", "da_stato"), ("A", "a_stato"), ("Il", "created_at"),
                             ("Utente", "utente_id"), ("Ruolo", "ruolo"), ("Motivo", "motivo")):
            if passo.get(chiave) is not None:
                scrivi(_el(nome, passo[chiave], "      "))
        scrivi("    </Passo>\n")
    scrivi("  </Iter>\n")
    scrivi("  <Allegati>\n")
    for a in allegati:
        scrivi(f"    <Allegato tipo={quoteattr(str(a['tipo']))} "
               f"sha256={quoteattr(str(a['sha256']))}>{_t(a.get('nome_file'))}</Allegato>\n")
    scrivi("  </Allegati>\n")
    scrivi("  <Conteggi>\n")
    for chiave in sorted(conteggi):
        scrivi(f"    <Conteggio esito={quoteattr(chiave)}>{int(conteggi[chiave])}</Conteggio>\n")
    scrivi("  </Conteggi>\n")
    scrivi("  <Elementi>\n")
    for e in elementi:
        motivo = f" motivo={quoteattr(str(e['motivo']))}" if e.get("motivo") else ""
        apertura = (f"    <PdA urn={quoteattr(str(e.get('urn') or ''))} "
                    f"id={quoteattr(str(e['aip_id']))} esito={quoteattr(str(e['esito']))}{motivo}")
        if e.get("esito") != "NON_COMPLETATO":
            scrivi(apertura + "/>\n")
            continue
        # S3: cancellazione iniziata e non completata: cosa è stato cancellato e cosa no
        # Revisione 4 di R05 (U1): storage illeggibile alla chiusura → le versioni
        # rimaste NON sono note («non determinabile», mai 0) e quelle eliminate
        # sono un minimo (contate); ``verificaStorage`` lo dichiara
        rimaste = e.get("versioni_rimaste")
        verifica = e.get("verifica_storage")
        scrivi(apertura + f" versioniEliminate=\"{int(e.get('versioni_eliminate') or 0)}\" "
               + "versioniRimaste="
               + quoteattr("non determinabile" if rimaste is None and verifica
                           == "non_determinabile" else str(int(rimaste or 0)))
               + (f" verificaStorage={quoteattr(str(verifica))}"
                  if verifica == "non_determinabile" else "")
               + ">\n")
        for chiave in e.get("chiavi_eliminate") or []:
            scrivi(f"      <Eliminata chiave={quoteattr(str(chiave))}/>\n")
        for chiave in e.get("chiavi_rimaste") or []:
            scrivi(f"      <Rimasta chiave={quoteattr(str(chiave))}/>\n")
        scrivi("    </PdA>\n")
    scrivi("  </Elementi>\n")
    scrivi("</VerbaleScarto>\n")


def atto(tipo: str, campi: Mapping[str, Any]) -> bytes:
    """Atto di una pratica puntuale (annullamento, rettifica): XML piccolo, in
    memoria. ``campi`` in ordine di chiave; dizionari e liste come JSON
    canonico (metadati precedenti e nuovi della rettifica)."""
    righe = ['<?xml version="1.0" encoding="UTF-8"?>\n',
             f"<Atto xmlns={quoteattr(NS_ATTO)} versione=\"1\" tipo={quoteattr(tipo)}>\n"]
    for chiave in sorted(campi):
        valore = campi[chiave]
        if isinstance(valore, Mapping | list):
            valore = json.dumps(valore, sort_keys=True, ensure_ascii=False, default=str)
        righe.append(f"  <Campo nome={quoteattr(chiave)}>{_t(valore)}</Campo>\n")
    righe.append("</Atto>\n")
    return "".join(righe).encode("utf-8")


def manifesto(pratica: Mapping[str, Any], pacchetti: Iterable[Mapping[str, Any]],
              allegati: Iterable[Mapping[str, Any]] = ()) -> bytes:
    """Manifesto JSON di un riversamento in uscita o di una restituzione: un
    pacchetto di distribuzione MIGRAZIONE per PdA (con impronta dello ZIP) più
    gli allegati prodotti (export dei registri)."""
    corpo = {
        "formato": FORMATO_MANIFESTO,
        "pratica": str(pratica["id"]), "tipo": pratica["tipo"], "ente": str(pratica["ente"]),
        "conservatore_destinatario": pratica.get("conservatore_destinatario"),
        "concluso_il": pratica.get("concluso_il"),
        "pacchetti": [dict(p) for p in pacchetti],
        "allegati": [dict(a) for a in allegati],
    }
    return json.dumps(corpo, sort_keys=True, ensure_ascii=False, indent=1,
                      default=str).encode("utf-8")


__all__ = ["FORMATO_MANIFESTO", "NS_ATTO", "NS_VERBALE", "atto", "manifesto",
           "scrivi_verbale_scarto"]
