#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Verificatore indipendente delle ricevute VerumDoc (R04, PIANO §5).

Controlla, SENZA contattare VerumDoc, che un documento conservato sia nella
catena delle impronte e che il registro non sia stato riscritto:

  1. impronta del file originale = impronta scritta nella foglia del PdA;
  2. inclusione della foglia nell'albero del PdA (RFC 6962) → radice del PdA;
  3. inclusione del PdA nel registro globale (RFC 6962) → radice del checkpoint;
  4. firme del checkpoint: chiave del registro e chiave del testimone
     (signed note Ed25519, contratto K2), ORIGINE attesa del checkpoint
     (``--origine``, predefinita quella di produzione ``verumdoc.it/registro/v1``:
     un checkpoint di sviluppo o di prova non vale come uno di produzione) e
     chiavi distinte nei due ruoli (la chiave del registro non vale come
     «testimone»; i nomi sono quelli del contratto K2);
  5. (facoltativo) consistenza fra due checkpoint conservati dal cliente:
     il più recente estende il più vecchio senza riscriverlo (RFC 6962 §2.1.2);
  6. (R05, contratto Q3) STATO DEL DOCUMENTO: le foglie del ciclo di vita del
     PdA (formato 2: scarto, annullamento, rettifica) riportate nella prova
     (``ciclo_vita``) si verificano nello stesso checkpoint. Uno scarto o un
     annullamento verificati rendono l'esito «DOCUMENTO SCARTATO» o «DOCUMENTO
     ANNULLATO» (codice d'uscita 2): la ricevuta è autentica, ma il contenuto
     non è più conservato e non risulta MAI «verificato». Una foglia di formato
     2 usata come foglia del documento, o un formato/tipo sconosciuto, è un
     controllo fallito;
  7. (revisione 2 di R05, S8) ATTESTAZIONE del ciclo di vita: nota firmata
     con la chiave del registro che impegna numero e impronta dell'elenco delle
     foglie del ciclo di vita del PdA fino al checkpoint della prova
     (``libs/catena/attestazione.py``). Una foglia di scarto tolta dalla prova
     non torna con l'attestazione; senza attestazione l'assenza di scarti e
     annullamenti NON è dimostrabile e lo stato del documento non si dichiara
     «conservato» (controllo non eseguito, codice 1): chiedere la prova
     direttamente all'endpoint pubblico del conservatore;
  8. (revisione 3 di R05, T2/T3) l'attestazione v2 porta l'ISTANTE del rilascio
     e lo STATO del PdA, firmati: il verificatore scrive sempre «stato al
     checkpoint N del <data>»; con ``--checkpoint-corrente`` (l'ultimo
     checkpoint pubblicato, firmato dal registro) avverte se lo stato della
     prova è più vecchio e non conclude «conservato» (codice 1: chiedere una
     prova aggiornata). Uno stato SCARTO_PARZIALE (cancellazione iniziata e non
     completata) o SCARTO (scarto avviato) dà «DOCUMENTO SCARTATO IN PARTE» o
     «DOCUMENTO SCARTATO» (codice 2), anche senza foglia di scarto;
  9. (revisione 4 di R05, U3) il checkpoint corrente si CONFRONTA con quello
     della prova: più vecchio → controllo fallito («checkpoint fornito più
     vecchio della prova»); stessa dimensione e radice diversa → «REGISTRO
     BIFORCATO» (fallito); più recente → con ``--consistenza-corrente`` (prova
     di consistenza dal checkpoint della prova al corrente) si verifica che il
     registro non sia stato riscritto, ma lo stato attuale resta non dimostrato.
     Mai «VERIFICATA» con un checkpoint corrente incoerente.

 10. (R14, VD-C-123) PROVA AGGIORNATA: con ``--prova-aggiornata`` (una prova d'inclusione
     dello stesso documento scaricata OGGI dall'endpoint del conservatore) e
     ``--checkpoint-corrente`` lo stato ATTUALE si dimostra offline: la prova aggiornata
     deve riguardare la STESSA foglia del registro e lo stesso documento della ricevuta
     (stessi dati, stessa posizione nel registro: il registro è append-only) e il suo
     checkpoint deve essere il corrente; la ricevuta resta verificata al SUO checkpoint
     e, con ``--consistenza-corrente``, si verifica che il corrente la estenda. Lo stato
     del documento lo dà la prova aggiornata (conservato, rettificato, scartato,
     annullato). La pagina web resta offline: la prova aggiornata la scarica il cliente
     con le sue credenziali (endpoint autenticato).

Ingressi: la prova d'inclusione JSON (contratto K1, ``GET
/api/v1/hashchain/prova/inclusione``) e/o il rapporto di versamento definitivo
v2 (XML, contratto K5), le chiavi PUBBLICHE di registro e testimone (verifier
key), il file originale (facoltativo).

Requisiti: Python 3.12, il pacchetto ``cryptography`` e la cartella
``libs/catena`` di VerumDoc (pubblica, solo libreria standard + cryptography).
Nessun'altra parte di VerumDoc. Esempi::

    python verifica_ricevuta.py --prova prova.json --documento fattura.pdf \\
        --chiave-registro chiave_registro.txt --chiave-testimone chiave_testimone.txt

    python verifica_ricevuta.py --checkpoint-vecchio cp_100.txt \\
        --checkpoint-nuovo cp_250.txt --prova-consistenza consistenza.json \\
        --chiave-registro chiave_registro.txt --chiave-testimone chiave_testimone.txt

Codice d'uscita: 0 se TUTTI i controlli richiesti sono riusciti; 1 se almeno
uno è fallito, non è stato possibile eseguirlo o gli ingressi non sono validi;
2 se i controlli sono riusciti ma il documento risulta scartato o annullato.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET  # noqa: S405 — si leggono solo file del cliente; vedi _leggi_xml
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _trova_libs() -> None:
    """Rende importabile ``libs.catena`` dalla copia del repository o da una
    cartella accanto a questo file (distribuzione: verificatore + libs/catena)."""
    qui = Path(__file__).resolve().parent
    for cartella in (qui, *qui.parents):
        if (cartella / "libs" / "catena" / "rfc6962.py").is_file():
            if str(cartella) not in sys.path:
                sys.path.insert(0, str(cartella))
            return


_trova_libs()

from libs.catena import attestazione as attestazione_lib  # noqa: E402
from libs.catena import foglie as foglie_lib  # noqa: E402
from libs.catena import nota as nota_lib  # noqa: E402
from libs.catena import rfc6962  # noqa: E402
from libs.catena.canonico import json_canonico  # noqa: E402

NOME_REGISTRO = "verumdoc-registro"
NOME_TESTIMONE = "verumdoc-testimone"
#: Origine dei checkpoint di produzione (contratto K2); negli altri ambienti
#: «verumdoc.it/registro/v1/<ambiente>», da indicare con --origine.
ORIGINE_PRODUZIONE = "verumdoc.it/registro/v1"
BLOCCO_LETTURA = 1 << 20  # 1 MiB: il file non si carica mai intero in memoria

OK = "OK"
FALLITO = "FALLITO"
NON_ESEGUITO = "NON ESEGUITO"      # doveva essere eseguito e non si è potuto: fallisce
NON_RICHIESTO = "NON RICHIESTO"    # facoltativo e non chiesto (es. file originale assente)
NON_APPLICABILE = "NON APPLICABILE"  # non ha senso per questa prova (riversamento)
SCARTATO = "SCARTATO"              # ricevuta autentica, documento scartato (R05)
ANNULLATO = "ANNULLATO"            # ricevuta autentica, versamento annullato (R05)
#: Revisione 3 di R05 (T3): cancellazione dello scarto iniziata e non completata
SCARTATO_IN_PARTE = "SCARTATO IN PARTE"
STATI_INVALIDANTI = (SCARTATO, ANNULLATO, SCARTATO_IN_PARTE)
NOME_STATO = "Stato del documento"


class IngressoNonValido(ValueError):
    """File, chiave o prova illeggibili o malformati."""


@dataclass(frozen=True)
class Controllo:
    nome: str
    esito: str
    dettaglio: str


@dataclass
class Rapporto:
    controlli: list[Controllo] = field(default_factory=list)

    def _metti(self, controllo: Controllo) -> None:
        # Lo stesso controllo con lo stesso esito (es. le firme dello stesso
        # checkpoint per più documenti di un RdV) si mostra una volta sola.
        if controllo not in self.controlli:
            self.controlli.append(controllo)

    def aggiungi(self, nome: str, riuscito: bool, dettaglio: str) -> bool:
        self._metti(Controllo(nome, OK if riuscito else FALLITO, dettaglio))
        return riuscito

    def non_eseguito(self, nome: str, dettaglio: str, esito: str = NON_ESEGUITO) -> None:
        self._metti(Controllo(nome, esito, dettaglio))

    @property
    def riuscito(self) -> bool:
        """Almeno un controllo riuscito e nessuno fallito o mancato (uno scarto o un
        annullamento NON sono un esito riuscito)."""
        return any(c.esito == OK for c in self.controlli) and all(
            c.esito in (OK, NON_RICHIESTO, NON_APPLICABILE) for c in self.controlli)

    @property
    def invalidante(self) -> Controllo | None:
        """Lo stato «scartato» o «annullato», se i controlli sono tutti riusciti."""
        stati = [c for c in self.controlli if c.esito in STATI_INVALIDANTI]
        altri = [c for c in self.controlli if c.esito not in STATI_INVALIDANTI]
        if stati and any(c.esito == OK for c in altri) and all(
                c.esito in (OK, NON_RICHIESTO, NON_APPLICABILE) for c in altri):
            return stati[0]
        return None

    @property
    def codice_uscita(self) -> int:
        if self.riuscito:
            return 0
        return 2 if self.invalidante is not None else 1

    def testo(self) -> str:
        larghezza = max(len(c.esito) for c in self.controlli) if self.controlli else 2
        righe = ["Verifica indipendente della catena delle impronte VerumDoc", ""]
        for c in self.controlli:
            righe.append(f"  {c.esito:<{larghezza}}  {c.nome}")
            righe.append(f"  {'':<{larghezza}}  {c.dettaglio}")
        righe.append("")
        stato = self.invalidante
        if self.riuscito:
            righe.append("ESITO: VERIFICATA — tutti i controlli sono riusciti")
        elif stato is not None:
            righe.append(f"ESITO: DOCUMENTO {stato.esito} — la ricevuta è autentica, ma "
                         f"{stato.dettaglio}: il contenuto non è più conservato e non vale "
                         "come verificato")
        else:
            righe.append("ESITO: NON VERIFICATA — almeno un controllo è fallito o non eseguito")
        return "\n".join(righe)


# ── Lettura degli ingressi ─────────────────────────────────────────────

def leggi_testo(percorso: str) -> str:
    try:
        return Path(percorso).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IngressoNonValido(f"{percorso}: file non leggibile ({exc})") from exc


def leggi_chiave(valore: str) -> str:
    """Verifier key scritta direttamente o in un file (prima riga non vuota)."""
    testo = valore
    if "+" not in valore or Path(valore).is_file():
        testo = leggi_testo(valore)
    righe = [r.strip() for r in testo.splitlines() if r.strip()]
    if not righe:
        raise IngressoNonValido(f"{valore}: chiave vuota")
    try:
        nota_lib.analizza_verificatore(righe[0])
    except ValueError as exc:
        raise IngressoNonValido(f"Chiave pubblica non valida: {exc}") from exc
    return righe[0]


def impronta_file(percorso: str) -> str:
    """SHA-256 esadecimale del file, letto a blocchi."""
    h = hashlib.sha256()
    try:
        with open(percorso, "rb") as f:
            while blocco := f.read(BLOCCO_LETTURA):
                h.update(blocco)
    except OSError as exc:
        raise IngressoNonValido(f"{percorso}: file non leggibile ({exc})") from exc
    return h.hexdigest()


def _dati_foglia(valore: Any, nome: str) -> tuple[bytes, dict[str, Any]]:
    """Byte della foglia (JSON canonico) e oggetto letto; errore se non canonico."""
    if isinstance(valore, str):
        dati = valore.encode("utf-8")
        try:
            oggetto = json.loads(valore)
        except json.JSONDecodeError as exc:
            raise IngressoNonValido(f"{nome}: JSON non valido") from exc
    elif isinstance(valore, dict):
        oggetto = valore
        dati = json_canonico(valore)
    else:
        raise IngressoNonValido(f"{nome}: atteso il JSON canonico della foglia")
    if not isinstance(oggetto, dict) or json_canonico(oggetto) != dati:
        raise IngressoNonValido(f"{nome}: la foglia non è in forma canonica")
    return dati, oggetto


def _hash_hex(valori: Any, nome: str) -> list[bytes]:
    if not isinstance(valori, list):
        raise IngressoNonValido(f"{nome}: atteso un elenco di hash")
    try:
        return rfc6962.da_esadecimale(valori)
    except ValueError as exc:
        raise IngressoNonValido(f"{nome}: {exc}") from exc


def _intero(valore: Any, nome: str) -> int:
    if isinstance(valore, bool) or not isinstance(valore, int) or valore < 0:
        raise IngressoNonValido(f"{nome}: atteso un intero non negativo")
    return valore


# ── Controlli ──────────────────────────────────────────────────────────

def controlla_chiavi(chiave_registro: str, chiave_testimone: str | None) -> None:
    """Le chiavi hanno i nomi dei loro ruoli (contratto K2) e sono DIVERSE: la
    firma del registro non può valere come controfirma del testimone."""
    try:
        reg = nota_lib.analizza_verificatore(chiave_registro)
        tst = nota_lib.analizza_verificatore(chiave_testimone) if chiave_testimone else None
    except ValueError as exc:
        raise IngressoNonValido(f"Chiave pubblica non valida: {exc}") from exc
    if reg.nome != NOME_REGISTRO:
        raise IngressoNonValido(f"La chiave del registro si chiama {reg.nome!r}: attesa "
                                f"{NOME_REGISTRO!r}")
    if tst is None:
        return
    if tst.chiave_pubblica == reg.chiave_pubblica:
        raise IngressoNonValido("La chiave del testimone coincide con quella del registro: la "
                                "controfirma deve venire da una chiave indipendente")
    if tst.nome != NOME_TESTIMONE:
        raise IngressoNonValido(f"La chiave del testimone si chiama {tst.nome!r}: attesa "
                                f"{NOME_TESTIMONE!r}")


def verifica_firme(
    rapporto: Rapporto, testo_checkpoint: str, *, chiave_registro: str,
    chiave_testimone: str | None, etichetta: str = "Checkpoint",
    origine: str = ORIGINE_PRODUZIONE,
) -> nota_lib.Checkpoint | None:
    """Origine attesa, firma del registro (obbligatoria) e del testimone (se la
    chiave è data)."""
    try:
        cp = nota_lib.analizza(testo_checkpoint)
        firme = nota_lib.verifica(testo_checkpoint, [chiave_registro]
                                  + ([chiave_testimone] if chiave_testimone else []))
    except ValueError as exc:
        rapporto.aggiungi(f"{etichetta}: formato", False, f"Nota firmata non valida: {exc}")
        return None
    rapporto.aggiungi(
        f"{etichetta} {cp.dimensione}: origine", cp.origine == origine,
        f"origine {cp.origine}" + ("" if cp.origine == origine else
                                   f", attesa {origine}: checkpoint di un altro registro o "
                                   "ambiente (--origine per un ambiente diverso)"))
    nomi = {(f.nome, f.key_hash) for f in firme}
    reg = nota_lib.analizza_verificatore(chiave_registro)
    rapporto.aggiungi(
        f"{etichetta} {cp.dimensione}: firma del registro",
        (reg.nome, reg.key_hash) in nomi,
        f"origine {cp.origine}, chiave {reg.nome}+{reg.key_hash:08x}")
    if chiave_testimone:
        tst = nota_lib.analizza_verificatore(chiave_testimone)
        rapporto.aggiungi(
            f"{etichetta} {cp.dimensione}: controfirma del testimone",
            (tst.nome, tst.key_hash) in nomi,
            f"chiave {tst.nome}+{tst.key_hash:08x}"
            + ("" if (tst.nome, tst.key_hash) in nomi else
               " — controfirma assente o non valida: il checkpoint non è stato "
               "confermato dal testimone indipendente"))
    else:
        rapporto.non_eseguito(f"{etichetta} {cp.dimensione}: controfirma del testimone",
                              "Non richiesta (--senza-testimone): il checkpoint è garantito "
                              "solo dal registro", NON_RICHIESTO)
    return cp


def verifica_prova(
    rapporto: Rapporto, prova: dict[str, Any], *, chiave_registro: str,
    chiave_testimone: str | None, impronta_documento: str | None = None,
    origine: str = ORIGINE_PRODUZIONE, checkpoint_corrente: nota_lib.Checkpoint | None = None,
    consistenza_corrente: dict[str, Any] | None = None,
) -> None:
    """Controlli 1–4 su una prova d'inclusione K1. ``impronta_documento`` è lo
    SHA-256 del file originale, se il cliente l'ha indicato e se questa è la
    prova del suo documento (``esegui`` sceglie la prova giusta)."""
    if not isinstance(prova, dict):
        raise IngressoNonValido("La prova d'inclusione deve essere un oggetto JSON")
    pda = prova.get("pda")
    dati_reg, foglia_reg = _dati_foglia(prova.get("foglia_registro_dati"), "foglia_registro_dati")
    try:
        interpretata = foglie_lib.analizza_foglia_registro(dati_reg)
    except foglie_lib.FogliaNonValida as exc:
        rapporto.aggiungi("Formato della foglia del registro", False, str(exc))
        return
    if not interpretata.prova_conservazione:
        # R05: una foglia di formato 2 (scarto, annullamento, rettifica, audit) non
        # prova MAI la conservazione di un contenuto
        rapporto.aggiungi("Formato della foglia del registro", False,
                          f"foglia di tipo «{interpretata.tipo}» (formato "
                          f"{interpretata.versione}): registra un fatto successivo, non "
                          "prova la conservazione di un contenuto")
        return
    riversamento = interpretata.tipo == foglie_lib.TIPO_RIVERSAMENTO
    rapporto.aggiungi("Foglia del registro riferita al PdA", foglia_reg.get("pda") == pda,
                      f"PdA {pda}")

    if riversamento:
        rapporto.non_eseguito(
            "Inclusione nel PdA",
            "PdA conservato prima del registro a due livelli (foglia di riversamento): "
            "la foglia cita l'impronta del PdA, non un albero dei documenti", NON_APPLICABILE)
        if impronta_documento is not None:
            rapporto.non_eseguito("Impronta del file",
                                  "Non verificabile con una foglia di riversamento: "
                                  "usare il pacchetto di distribuzione")
    else:
        dati_doc, foglia_doc = _dati_foglia(prova.get("foglia_dati"), "foglia_dati")
        # Con un RdV di più documenti i controlli del documento portano il nome del file
        pref = f"{prova['nome_file']}: " if prova.get("nome_file") else ""
        prova_pda = prova.get("prova_pda") or {}
        radice_pda_hex = str(prova.get("radice_pda", ""))
        coerente = (foglia_doc.get("pda") == pda
                    and str(foglia_doc.get("doc")) == str(prova.get("documento")))
        rapporto.aggiungi(f"{pref}Foglia del documento riferita al PdA e al documento", coerente,
                          f"documento {foglia_doc.get('doc')}, file {foglia_doc.get('file')}")
        dichiarata = prova.get("impronta_dichiarata")
        if dichiarata is not None:
            rapporto.aggiungi(f"{pref}Impronta dichiarata nel rapporto = impronta della foglia",
                              str(dichiarata).lower() == str(foglia_doc.get("sha256")).lower()
                              and prova.get("nome_file") == foglia_doc.get("file"),
                              f"file {prova.get('nome_file')}")
        if impronta_documento is not None:
            calcolata = impronta_documento
            attesa = str(foglia_doc.get("sha256", "")).lower()
            rapporto.aggiungi(
                f"{pref}Impronta del file", calcolata == attesa,
                f"SHA-256 calcolata {calcolata}"
                + ("" if calcolata == attesa else f", attesa {attesa}"))
        else:
            rapporto.non_eseguito(f"{pref}Impronta del file",
                                  "File originale non indicato (--documento)", NON_RICHIESTO)
        indice = _intero(prova_pda.get("foglia_indice"), "prova_pda.foglia_indice")
        dimensione = _intero(prova_pda.get("dimensione"), "prova_pda.dimensione")
        percorso = _hash_hex(prova_pda.get("percorso"), "prova_pda.percorso")
        radice_pda = _hash_hex([radice_pda_hex], "radice_pda")[0]
        incluso = rfc6962.verifica_inclusione(rfc6962.hash_foglia(dati_doc), indice, dimensione,
                                              percorso, radice_pda)
        rapporto.aggiungi(f"{pref}Inclusione nel PdA", incluso,
                          f"foglia {indice} di {dimensione}, radice del PdA {radice_pda_hex}")
        rapporto.aggiungi("Radice del PdA nella foglia del registro",
                          str(foglia_reg.get("radice_pda", "")).lower() == radice_pda_hex.lower(),
                          f"radice_pda {foglia_reg.get('radice_pda')}")

    testo_cp = prova.get("checkpoint")
    if not isinstance(testo_cp, str):
        raise IngressoNonValido("checkpoint: atteso il testo della nota firmata")
    cp = verifica_firme(rapporto, testo_cp, chiave_registro=chiave_registro,
                        chiave_testimone=chiave_testimone, origine=origine)
    prova_reg = prova.get("prova_registro") or {}
    indice = _intero(prova_reg.get("foglia_indice"), "prova_registro.foglia_indice")
    dimensione = _intero(prova_reg.get("dimensione_albero"), "prova_registro.dimensione_albero")
    percorso = _hash_hex(prova_reg.get("percorso"), "prova_registro.percorso")
    if cp is None:
        rapporto.non_eseguito("Inclusione nel registro", "Checkpoint non leggibile")
        return
    incluso = (dimensione == cp.dimensione and rfc6962.verifica_inclusione(
        rfc6962.hash_foglia(dati_reg), indice, dimensione, percorso, cp.radice))
    rapporto.aggiungi(
        "Inclusione nel registro", incluso,
        f"foglia {indice} del registro di {dimensione} foglie, checkpoint {cp.dimensione}")
    verifica_ciclo_vita(rapporto, prova, str(pda), cp, chiave_registro=chiave_registro,
                        checkpoint_corrente=checkpoint_corrente,
                        consistenza_corrente=consistenza_corrente)


NOME_ATTESTAZIONE = "Attestazione del ciclo di vita"


def _verifica_attestazione(rapporto: Rapporto, prova: dict[str, Any], pda: str,
                           cp: nota_lib.Checkpoint, chiave_registro: str | None,
                           presentate: list[tuple[int, bytes]]
                           ) -> attestazione_lib.Attestazione | None:
    """Revisione 2 di R05 (S8): attestazione del registro (numero di foglie,
    impronta dell'elenco; revisione 3: istante e stato del PdA), o None se manca
    o non è valida (controllo fallito)."""
    testo = prova.get("attestazione_ciclo_vita")
    if testo is None:
        motivo = prova.get("attestazione_assente")
        rapporto.non_eseguito(NOME_ATTESTAZIONE, "assente"
                              + (f" ({motivo})" if isinstance(motivo, str) and motivo else "")
                              + ": l'assenza di scarti o annullamenti NON è dimostrabile da "
                              "questa prova (una foglia tolta non si vedrebbe; il conservatore "
                              "la rilascia solo per l'ultimo checkpoint); chiedere la prova "
                              "direttamente all'endpoint pubblico del conservatore (GET "
                              "/api/v1/hashchain/prova/inclusione, senza «dimensione»)")
        return None
    if not isinstance(testo, str):
        raise IngressoNonValido("attestazione_ciclo_vita: atteso il testo della nota firmata")
    try:
        att = attestazione_lib.analizza(testo)
    except attestazione_lib.AttestazioneNonValida as exc:
        rapporto.aggiungi(NOME_ATTESTAZIONE, False, f"non valida: {exc}")
        return None
    if not chiave_registro or not attestazione_lib.firmata_da(testo, chiave_registro):
        rapporto.aggiungi(NOME_ATTESTAZIONE, False, "firma del registro assente o non valida")
        return None
    if (att.origine, att.dimensione, att.pda) != (cp.origine, cp.dimensione, pda):
        rapporto.aggiungi(NOME_ATTESTAZIONE, False,
                          f"riferita a {att.pda} al checkpoint {att.dimensione} di "
                          f"{att.origine}, non al PdA e al checkpoint di questa prova")
        return None
    if att.foglie < len(presentate):
        rapporto.aggiungi(NOME_ATTESTAZIONE, False,
                          f"la prova riporta {len(presentate)} foglie, il registro ne attesta "
                          f"{att.foglie}")
        return None
    if att.foglie == len(presentate) and \
            attestazione_lib.impronta_elenco(presentate) != att.elenco:
        rapporto.aggiungi(NOME_ATTESTAZIONE, False,
                          "le foglie della prova non sono quelle attestate dal registro "
                          "(foglia tolta, aggiunta o sostituita)")
        return None
    rapporto.aggiungi(NOME_ATTESTAZIONE, True,
                      f"{att.foglie} foglie del ciclo di vita fino al checkpoint "
                      f"{att.dimensione}, stato del PdA {att.stato}, rilasciata il "
                      f"{att.rilasciata} (ultimo checkpoint del registro in quell'istante), "
                      "firmata dal registro")
    return att


NOME_CORRENTE = "Checkpoint corrente rispetto alla prova"
NOME_CONSISTENZA_CORRENTE = "Consistenza con il checkpoint corrente"


def confronta_checkpoint_corrente(rapporto: Rapporto, cp: nota_lib.Checkpoint,
                                  corrente: nota_lib.Checkpoint,
                                  consistenza: dict[str, Any] | None = None) -> bool:
    """Revisione 4 di R05 (U3): confronto del checkpoint corrente indicato dal
    cliente con il checkpoint della prova. Restituisce True solo se il corrente
    È il checkpoint della prova (stessa dimensione, stessa radice): solo allora
    lo stato della prova è lo stato attuale.

    - più vecchio → FALLITO: non può essere il checkpoint corrente;
    - stessa dimensione, radice diversa → FALLITO «REGISTRO BIFORCATO» (due
      registri firmati con la stessa dimensione: la prova o il corrente vengono
      da una storia diversa);
    - più recente → con la prova di consistenza (``consistenza``: da = prova,
      a = corrente) si verifica che il corrente ESTENDA il checkpoint della
      prova (nessuna riscrittura); senza, il controllo è «non richiesto». Lo
      stato attuale resta comunque non dimostrato (chi decide è il chiamante)."""
    if corrente.origine != cp.origine:
        rapporto.aggiungi(NOME_CORRENTE, False, f"origine diversa ({corrente.origine} invece "
                          f"di {cp.origine}): non è un checkpoint dello stesso registro")
        return False
    if corrente.dimensione < cp.dimensione:
        rapporto.aggiungi(NOME_CORRENTE, False,
                          f"checkpoint fornito più vecchio della prova ({corrente.dimensione} "
                          f"foglie contro {cp.dimensione}): non è il checkpoint corrente; "
                          "indicare l'ultimo checkpoint pubblicato dal registro")
        return False
    if corrente.dimensione == cp.dimensione:
        if corrente.radice != cp.radice:
            rapporto.aggiungi(NOME_CORRENTE, False,
                              f"REGISTRO BIFORCATO: il checkpoint corrente e quello della prova "
                              f"hanno la stessa dimensione ({cp.dimensione} foglie) e radici "
                              f"diverse ({corrente.radice.hex()} e {cp.radice.hex()}), entrambi "
                              "firmati: conservare i due checkpoint e segnalare al conservatore "
                              "e all'ispettore")
            return False
        rapporto.aggiungi(NOME_CORRENTE, True, f"coincide con il checkpoint della prova "
                          f"({cp.dimensione} foglie, stessa radice)")
        return True
    rapporto.aggiungi(NOME_CORRENTE, True, f"più recente della prova ({corrente.dimensione} "
                      f"foglie contro {cp.dimensione})")
    if consistenza is None:
        rapporto.non_eseguito(NOME_CONSISTENZA_CORRENTE,
                              "prova di consistenza non indicata (--consistenza-corrente): "
                              "non si esclude che il registro sia stato riscritto dopo il "
                              "checkpoint della prova", NON_RICHIESTO)
        return False
    if not isinstance(consistenza, dict):
        raise IngressoNonValido("La prova di consistenza deve essere un oggetto JSON")
    da = _intero(consistenza.get("da"), "da")
    a = _intero(consistenza.get("a"), "a")
    percorso = _hash_hex(consistenza.get("percorso"), "percorso")
    valida = (da == cp.dimensione and a == corrente.dimensione
              and rfc6962.verifica_consistenza(da, a, cp.radice, corrente.radice, percorso))
    rapporto.aggiungi(NOME_CONSISTENZA_CORRENTE, valida,
                      f"il registro corrente di {corrente.dimensione} foglie estende quello "
                      f"della prova ({cp.dimensione}): nessuna riscrittura" if valida else
                      f"REGISTRO BIFORCATO o riscritto: la prova NON dimostra che il checkpoint "
                      f"corrente ({corrente.dimensione}) estende quello della prova "
                      f"({cp.dimensione}); conservare i checkpoint e segnalare al conservatore "
                      "e all'ispettore")
    return False


def verifica_ciclo_vita(rapporto: Rapporto, prova: dict[str, Any], pda: str,
                        cp: nota_lib.Checkpoint, *, chiave_registro: str | None = None,
                        checkpoint_corrente: nota_lib.Checkpoint | None = None,
                        consistenza_corrente: dict[str, Any] | None = None) -> None:
    """R05 (Q3): foglie del ciclo di vita del PdA nello STESSO checkpoint e stato
    del documento. Una prova senza ``ciclo_vita`` (RdV, prove precedenti a R05)
    non dice nulla sullo stato successivo: lo si dichiara.

    Revisione 3 di R05: lo stato è sempre «al checkpoint N del <data>» (T2);
    con ``checkpoint_corrente`` più recente lo stato attuale non è dimostrato;
    lo stato del PdA firmato (T3) SCARTO/SCARTO_PARZIALE non è mai «conservato».
    Revisione 4 di R05 (U3): il checkpoint corrente si confronta con quello
    della prova (``confronta_checkpoint_corrente``); «conservato» solo se
    coincide (o se il corrente non è indicato)."""
    corrente_coincide = None
    if checkpoint_corrente is not None:
        corrente_coincide = confronta_checkpoint_corrente(rapporto, cp, checkpoint_corrente,
                                                          consistenza_corrente)
    if "ciclo_vita" not in prova:
        rapporto.non_eseguito(NOME_STATO, "La ricevuta non riporta il ciclo di vita del "
                              "documento: per lo stato attuale (scarto, annullamento, "
                              "rettifica) chiedere una prova d'inclusione aggiornata",
                              NON_APPLICABILE)
        return
    elenco = prova.get("ciclo_vita")
    if not isinstance(elenco, list):
        raise IngressoNonValido("ciclo_vita: atteso un elenco")
    valide: list[tuple[foglie_lib.FogliaRegistro, int]] = []
    presentate: list[tuple[int, bytes]] = []
    for i, voce in enumerate(elenco):
        if not isinstance(voce, dict):
            raise IngressoNonValido(f"ciclo_vita[{i}]: atteso un oggetto")
        dati, _ = _dati_foglia(voce.get("foglia_dati"), f"ciclo_vita[{i}].foglia_dati")
        pr = voce.get("prova_registro") or {}
        indice = _intero(pr.get("foglia_indice"), f"ciclo_vita[{i}].foglia_indice")
        dimensione = _intero(pr.get("dimensione_albero"), f"ciclo_vita[{i}].dimensione_albero")
        percorso = _hash_hex(pr.get("percorso"), f"ciclo_vita[{i}].percorso")
        try:
            foglia = foglie_lib.analizza_foglia_registro(dati)
        except foglie_lib.FogliaNonValida as exc:
            rapporto.aggiungi(f"Ciclo di vita: foglia {indice}", False, str(exc))
            continue
        nome = f"Ciclo di vita: {foglia.tipo} (foglia {indice})"
        if not foglia.ciclo_vita or foglia.pda != pda:
            rapporto.aggiungi(nome, False, "foglia non del ciclo di vita di questo PdA")
            continue
        inclusa = dimensione == cp.dimensione and rfc6962.verifica_inclusione(
            rfc6962.hash_foglia(dati), indice, dimensione, percorso, cp.radice)
        rapporto.aggiungi(nome, inclusa,
                          f"pratica {foglia.dati['riferimento']['pratica']}, inclusa nel "
                          f"registro di {dimensione} foglie, checkpoint {cp.dimensione}")
        if inclusa:
            valide.append((foglia, indice))
            presentate.append((indice, rfc6962.hash_foglia(dati)))
    stato = foglie_lib.stato_documento(pda, valide)
    evento = stato.invalidante
    completo, totale = _completezza_ciclo_vita(prova, len(elenco))
    att = _verifica_attestazione(rapporto, prova, pda, cp, chiave_registro, presentate)
    if att is not None and att.foglie > len(presentate):
        # S8: il numero firmato prevale sui campi non firmati della prova
        completo, totale = False, att.foglie
    quando = (f"stato al checkpoint {att.dimensione} del {att.rilasciata}"
              if att is not None else "")
    if evento is not None:
        # Lo scarto e l'annullamento hanno sempre la precedenza (anche su un
        # elenco incompleto: il server li include sempre tutti)
        rapporto.non_eseguito(NOME_STATO, f"il documento è stato {evento.descrizione()}",
                              SCARTATO if stato.codice == "SCARTATO" else ANNULLATO)
    elif att is not None and att.stato == attestazione_lib.STATO_SCARTO_PARZIALE:
        # T3: cancellazione iniziata e non completata (nessun tombstone): il
        # contenuto non è più integralmente conservato
        rapporto.non_eseguito(NOME_STATO, "il PdA è stato scartato in parte: la cancellazione "
                              f"è iniziata e non è stata completata ({quando}, attestato dal "
                              "registro)", SCARTATO_IN_PARTE)
    elif att is not None and att.stato == attestazione_lib.STATO_SCARTO:
        rapporto.non_eseguito(NOME_STATO, "il PdA è in scarto: la cancellazione è stata avviata "
                              f"({quando}, attestato dal registro)", SCARTATO)
    elif not completo:
        # Revisione R05 (R4): un elenco troncato non prova l'assenza di scarti
        rapporto.non_eseguito(NOME_STATO, f"elenco del ciclo di vita incompleto ({len(elenco)} "
                              f"di {totale if totale is not None else '?'} foglie): lo stato "
                              "del documento non è determinabile da questa prova (mai "
                              "«conservato»); chiedere al conservatore le foglie mancanti")
    elif att is None:
        # S8: senza attestazione valida l'assenza di scarti non è dimostrabile
        rapporto.non_eseguito(NOME_STATO, "assenza di scarto non dimostrabile: la prova non "
                              "ha un'attestazione valida del ciclo di vita firmata dal "
                              "registro (mai «conservato»); chiedere la prova direttamente "
                              "all'endpoint pubblico del conservatore")
    elif checkpoint_corrente is not None and checkpoint_corrente.dimensione > att.dimensione:
        # T2: la prova descrive uno stato superato: uno scarto successivo non si vedrebbe
        rapporto.non_eseguito(NOME_STATO, f"conservato secondo questa prova, ma {quando}: il "
                              f"registro ha ora {checkpoint_corrente.dimensione} foglie "
                              "(checkpoint corrente indicato), quindi lo stato ATTUALE non è "
                              "dimostrato (uno scarto successivo non si vedrebbe); chiedere al "
                              "conservatore una prova aggiornata")
    elif checkpoint_corrente is not None and not corrente_coincide:
        # U3: corrente più vecchio, biforcato o di un altro registro: lo stato
        # attuale non si dichiara (il controllo del corrente è già fallito)
        rapporto.non_eseguito(NOME_STATO, f"conservato secondo questa prova ({quando}), ma il "
                              "checkpoint corrente indicato non è coerente con quello della "
                              "prova (vedi «" + NOME_CORRENTE + "»): lo stato attuale non è "
                              "dimostrato")
    else:
        attuale = (" (è il checkpoint corrente indicato)" if checkpoint_corrente is not None
                   else "; per lo stato attuale indicare l'ultimo checkpoint pubblicato "
                        "(--checkpoint-corrente)")
        if stato.rettifiche:
            rapporto.aggiungi(NOME_STATO, True, "conservato; "
                              + "; ".join(e.descrizione() for e in stato.rettifiche)
                              + f" (il contenuto originale resta immutato) — {quando}{attuale}")
        else:
            rapporto.aggiungi(NOME_STATO, True, "conservato: nessuno scarto, annullamento o "
                              f"rettifica fino al checkpoint {cp.dimensione} (elenco attestato "
                              f"dal registro) — {quando}{attuale}")


def _completezza_ciclo_vita(prova: dict[str, Any], presenti: int) -> tuple[bool, int | None]:
    """(completo, totale) dell'elenco ``ciclo_vita`` (revisione R05, R4). Le prove
    precedenti alla correzione non hanno i due campi: complete. Un totale
    dichiarato maggiore delle foglie presenti vale come incompleto anche se il
    campo ``ciclo_vita_completo`` dicesse il contrario."""
    completo = prova.get("ciclo_vita_completo", True)
    if not isinstance(completo, bool):
        raise IngressoNonValido("ciclo_vita_completo: atteso true o false")
    totale = prova.get("ciclo_vita_totale")
    if totale is not None:
        totale = _intero(totale, "ciclo_vita_totale")
        if totale > presenti:
            completo = False
    return completo, totale


def verifica_consistenza(
    rapporto: Rapporto, vecchio: str, nuovo: str, prova: dict[str, Any], *,
    chiave_registro: str, chiave_testimone: str | None, origine: str = ORIGINE_PRODUZIONE,
) -> None:
    """Il checkpoint ``nuovo`` estende ``vecchio`` (entrambi firmati)."""
    cp1 = verifica_firme(rapporto, vecchio, chiave_registro=chiave_registro,
                         chiave_testimone=chiave_testimone, etichetta="Checkpoint conservato",
                         origine=origine)
    cp2 = verifica_firme(rapporto, nuovo, chiave_registro=chiave_registro,
                         chiave_testimone=chiave_testimone, etichetta="Checkpoint recente",
                         origine=origine)
    if cp1 is None or cp2 is None:
        rapporto.non_eseguito("Consistenza fra i checkpoint", "Checkpoint non leggibili")
        return
    if not isinstance(prova, dict):
        raise IngressoNonValido("La prova di consistenza deve essere un oggetto JSON")
    da = _intero(prova.get("da"), "da")
    a = _intero(prova.get("a"), "a")
    percorso = _hash_hex(prova.get("percorso"), "percorso")
    stessa_origine = cp1.origine == cp2.origine
    valida = (stessa_origine and da == cp1.dimensione and a == cp2.dimensione
              and rfc6962.verifica_consistenza(da, a, cp1.radice, cp2.radice, percorso))
    rapporto.aggiungi(
        "Consistenza fra i checkpoint", valida,
        f"il registro di {cp2.dimensione} foglie estende quello di {cp1.dimensione}"
        if valida else
        f"la prova NON dimostra che il checkpoint {cp2.dimensione} estende il "
        f"{cp1.dimensione}: conservare entrambi e segnalare al conservatore e all'ispettore")


# ── Rapporto di versamento definitivo v2 (K5) ──────────────────────────

NS_RDV_2 = "https://schemi.verumdoc.it/rdv/definitivo/2"


NOME_AGGIORNATA = "Prova aggiornata dello stesso documento"


def verifica_legame_aggiornata(rapporto: Rapporto, ricevuta: dict[str, Any],
                               aggiornata: dict[str, Any]) -> bool:
    """R14 (VD-C-123): la prova aggiornata riguarda la stessa foglia del registro e lo
    stesso documento della ricevuta (dati identici, stessa posizione: il registro è
    append-only, una foglia non cambia posto) e ha un checkpoint non più vecchio."""
    if not isinstance(aggiornata, dict):
        raise IngressoNonValido("La prova aggiornata deve essere un oggetto JSON")
    stessa_foglia = (str(aggiornata.get("foglia_registro_dati")) ==
                     str(ricevuta.get("foglia_registro_dati")))
    stesso_doc = (str(aggiornata.get("foglia_dati")) == str(ricevuta.get("foglia_dati"))
                  and aggiornata.get("pda") == ricevuta.get("pda"))
    pos_r = (ricevuta.get("prova_registro") or {}).get("foglia_indice")
    pos_a = (aggiornata.get("prova_registro") or {}).get("foglia_indice")
    dim_r = (ricevuta.get("prova_registro") or {}).get("dimensione_albero")
    dim_a = (aggiornata.get("prova_registro") or {}).get("dimensione_albero")
    stessa_posizione = isinstance(pos_r, int) and pos_r == pos_a
    piu_recente = isinstance(dim_r, int) and isinstance(dim_a, int) and dim_a >= dim_r
    esito = stessa_foglia and stesso_doc and stessa_posizione and piu_recente
    if esito:
        dettaglio = (f"stessa foglia del registro (posizione {pos_r}) e stesso documento; "
                     f"checkpoint {dim_a} contro {dim_r} della ricevuta")
    else:
        difetti = [n for n, ok in (("foglia del registro diversa", stessa_foglia),
                                   ("documento diverso", stesso_doc),
                                   ("posizione nel registro diversa", stessa_posizione),
                                   ("checkpoint più vecchio della ricevuta", piu_recente))
                   if not ok]
        dettaglio = "NON è la prova dello stesso documento: " + ", ".join(difetti)
    rapporto.aggiungi(NOME_AGGIORNATA, esito, dettaglio)
    return esito


def _leggi_xml(percorso: str) -> ET.Element:
    """XML del cliente: si rifiutano DTD ed entità (nessuna espansione)."""
    dati = Path(percorso).read_bytes() if Path(percorso).is_file() else None
    if dati is None:
        raise IngressoNonValido(f"{percorso}: file non trovato")
    if b"<!DOCTYPE" in dati or b"<!ENTITY" in dati:
        raise IngressoNonValido(f"{percorso}: DTD ed entità non ammesse nel rapporto")
    try:
        return ET.fromstring(dati)  # noqa: S314 — DTD/entità rifiutate sopra
    except ET.ParseError as exc:
        raise IngressoNonValido(f"{percorso}: XML non valido ({exc})") from exc


def prove_da_rdv(percorso: str) -> list[dict[str, Any]]:
    """Prove d'inclusione (forma K1) contenute nel RdV definitivo v2: una per
    documento (per un PdA in riversamento, una sola senza foglia del documento).
    I dati si prendono così come sono scritti: ogni controllo li ricalcola."""
    radice = _leggi_xml(percorso)

    def q(nome: str) -> str:
        return f"{{{NS_RDV_2}}}{nome}"

    def testo(el: ET.Element | None) -> str:
        return (el.text or "") if el is not None else ""

    def intero(valore: str | None, nome: str) -> int:
        try:
            return int(valore or "")
        except ValueError as exc:
            raise IngressoNonValido(f"RdV: {nome} non è un intero") from exc

    if radice.tag != q("RapportoVersamento") or radice.get("versione") != "2":
        raise IngressoNonValido("Il file non è un rapporto di versamento definitivo versione 2")
    pda, reg = radice.find(q("PdA")), radice.find(q("Registro"))
    cp = reg.find(q("Checkpoint")) if reg is not None else None
    prova_reg = reg.find(q("ProvaInclusione")) if reg is not None else None
    if pda is None or reg is None or cp is None or prova_reg is None:
        raise IngressoNonValido("RdV incompleto: mancano PdA, registro o checkpoint")
    comune: dict[str, Any] = {
        "pda": testo(pda.find(q("URN"))),
        "foglia_registro_dati": testo(reg.find(q("Foglia"))),
        "prova_registro": {
            "foglia_indice": intero(prova_reg.get("foglia_indice"), "foglia_indice"),
            "dimensione_albero": intero(prova_reg.get("dimensione_albero"), "dimensione_albero"),
            "percorso": [testo(h) for h in prova_reg.iter(q("Hash"))],
        },
        "checkpoint": testo(cp.find(q("Nota"))),
    }
    if pda.get("riversamento") == "true":
        return [comune]
    prove: list[dict[str, Any]] = []
    for doc in radice.iter(q("Documento")):
        el = doc.find(q("ProvaPdA"))
        if el is None:
            raise IngressoNonValido("RdV: documento senza prova d'inclusione nel PdA")
        foglia = testo(el.find(q("Foglia")))
        try:
            id_doc = json.loads(foglia).get("doc")
        except (json.JSONDecodeError, AttributeError):
            id_doc = None
        dichiarata = next((testo(x) for x in doc.findall(q("Impronta"))
                           if x.get("algoritmo") == "SHA-256"), None)
        prove.append({
            **comune, "documento": id_doc, "foglia_dati": foglia,
            "radice_pda": testo(pda.find(q("RadicePdA"))),
            "prova_pda": {"foglia_indice": intero(el.get("foglia_indice"), "foglia_indice"),
                          "dimensione": intero(el.get("dimensione"), "dimensione"),
                          "percorso": [testo(h) for h in el.iter(q("Hash"))]},
            "impronta_dichiarata": dichiarata, "nome_file": testo(doc.find(q("NomeFile"))),
        })
    if not prove:
        raise IngressoNonValido("RdV senza documenti")
    return prove


def _prova_del_documento(prove: list[dict[str, Any]], impronta: str | None) -> int | None:
    """Indice della prova a cui confrontare il file originale: con una sola prova
    quella (il confronto dirà se l'impronta torna); con più prove (RdV con più
    documenti) quella la cui foglia ha la stessa impronta."""
    if impronta is None or not prove:
        return None
    if len(prove) == 1:
        return 0
    for i, prova in enumerate(prove):
        try:
            _, foglia = _dati_foglia(prova.get("foglia_dati"), "foglia_dati")
        except IngressoNonValido:
            continue
        if str(foglia.get("sha256", "")).lower() == impronta:
            return i
    return None


# ── Riga di comando ────────────────────────────────────────────────────

class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"Errore negli argomenti: {message}\n")
        raise SystemExit(1)


def costruisci_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="verifica_ricevuta.py",
                description="Verifica indipendente di ricevute e checkpoint VerumDoc")
    p.add_argument("--prova", help="prova d'inclusione JSON (GET /hashchain/prova/inclusione)")
    p.add_argument("--rdv", help="rapporto di versamento definitivo v2 (XML)")
    p.add_argument("--documento", help="file originale di cui verificare l'impronta")
    p.add_argument("--chiave-registro", required=True,
                   help="verifier key del registro (testo o file)")
    p.add_argument("--chiave-testimone",
                   help="verifier key del testimone (testo o file); obbligatoria salvo "
                        "--senza-testimone")
    p.add_argument("--senza-testimone", action="store_true",
                   help="accetta di NON verificare la controfirma del testimone")
    p.add_argument("--origine", default=ORIGINE_PRODUZIONE,
                   help=f"origine attesa dei checkpoint (predefinita {ORIGINE_PRODUZIONE}; "
                        "per un ambiente di prova verumdoc.it/registro/v1/<ambiente>)")
    p.add_argument("--checkpoint-corrente",
                   help="ultimo checkpoint pubblicato dal registro (testo): se più recente "
                        "di quello della prova, lo stato attuale non è dimostrato (rev. 3 R05)")
    p.add_argument("--consistenza-corrente",
                   help="prova di consistenza JSON {da, a, percorso} dal checkpoint della "
                        "prova al checkpoint corrente: esclude una riscrittura (rev. 4 R05)")
    p.add_argument("--prova-aggiornata",
                   help="R14: prova d'inclusione dello stesso documento scaricata oggi "
                        "(stato attuale; richiede --checkpoint-corrente)")
    p.add_argument("--checkpoint-vecchio", help="checkpoint conservato in passato (testo)")
    p.add_argument("--checkpoint-nuovo", help="checkpoint più recente (testo)")
    p.add_argument("--prova-consistenza", help="prova di consistenza JSON {da, a, percorso}")
    return p


def esegui(argomenti: Sequence[str] | None = None) -> tuple[int, str]:
    """(codice d'uscita, testo del rapporto)."""
    opzioni = costruisci_parser().parse_args(argomenti)
    rapporto = Rapporto()
    try:
        chiave_registro = leggi_chiave(opzioni.chiave_registro)
        chiave_testimone = (leggi_chiave(opzioni.chiave_testimone)
                            if opzioni.chiave_testimone else None)
        if chiave_testimone is None and not opzioni.senza_testimone:
            raise IngressoNonValido("Indicare --chiave-testimone (o --senza-testimone)")
        controlla_chiavi(chiave_registro, chiave_testimone)
        origine = (opzioni.origine or "").strip()
        if not origine:
            raise IngressoNonValido("--origine vuota")
        consistenza = (opzioni.checkpoint_vecchio, opzioni.checkpoint_nuovo,
                       opzioni.prova_consistenza)
        if any(consistenza) and not all(consistenza):
            raise IngressoNonValido("Per la consistenza servono --checkpoint-vecchio, "
                                    "--checkpoint-nuovo e --prova-consistenza")
        if not (opzioni.prova or opzioni.rdv or all(consistenza)):
            raise IngressoNonValido("Indicare --prova, --rdv oppure i due checkpoint "
                                    "con la prova di consistenza")
        prove: list[dict[str, Any]] = []
        if opzioni.prova:
            try:
                prove.append(json.loads(leggi_testo(opzioni.prova)))
            except json.JSONDecodeError as exc:
                raise IngressoNonValido(f"{opzioni.prova}: JSON non valido") from exc
        if opzioni.rdv:
            prove.extend(prove_da_rdv(opzioni.rdv))
        corrente = None
        if opzioni.checkpoint_corrente:
            corrente = verifica_firme(rapporto, leggi_testo(opzioni.checkpoint_corrente),
                                      chiave_registro=chiave_registro,
                                      chiave_testimone=chiave_testimone,
                                      etichetta="Checkpoint corrente", origine=origine)
        consistenza_corrente = None
        if opzioni.consistenza_corrente:
            if corrente is None and not opzioni.checkpoint_corrente:
                raise IngressoNonValido("--consistenza-corrente richiede --checkpoint-corrente")
            try:
                consistenza_corrente = json.loads(leggi_testo(opzioni.consistenza_corrente))
            except json.JSONDecodeError as exc:
                raise IngressoNonValido(f"{opzioni.consistenza_corrente}: JSON non valido"
                                        ) from exc
        impronta = impronta_file(opzioni.documento) if opzioni.documento else None
        destinataria = _prova_del_documento(prove, impronta)
        if impronta is not None and prove and destinataria is None:
            rapporto.aggiungi("Impronta del file", False,
                              f"SHA-256 calcolata {impronta}: nessun documento della ricevuta "
                              "ha questa impronta")
        aggiornata = None
        if opzioni.prova_aggiornata:
            # R14 (VD-C-123): stato attuale dalla prova aggiornata
            if not opzioni.checkpoint_corrente:
                raise IngressoNonValido("--prova-aggiornata richiede --checkpoint-corrente")
            if not prove:
                raise IngressoNonValido("--prova-aggiornata richiede la ricevuta (--prova o "
                                        "--rdv)")
            try:
                aggiornata = json.loads(leggi_testo(opzioni.prova_aggiornata))
            except json.JSONDecodeError as exc:
                raise IngressoNonValido(f"{opzioni.prova_aggiornata}: JSON non valido") from exc
        for i, prova in enumerate(prove):
            con_corrente = aggiornata is None
            verifica_prova(rapporto, prova, chiave_registro=chiave_registro,
                           chiave_testimone=chiave_testimone,
                           impronta_documento=impronta if i == destinataria else None,
                           origine=origine,
                           checkpoint_corrente=corrente if con_corrente else None,
                           consistenza_corrente=consistenza_corrente if con_corrente else None)
        if aggiornata is not None:
            ricevuta = prove[destinataria if destinataria is not None else 0]
            verifica_legame_aggiornata(rapporto, ricevuta, aggiornata)
            if corrente is not None and isinstance(ricevuta.get("checkpoint"), str):
                # revisione R14 (1): SEMPRE, anche a pari dimensione (registro biforcato:
                # stessa dimensione e radice diversa) o con un corrente più vecchio
                cp_ricevuta = nota_lib.analizza(ricevuta["checkpoint"])
                confronta_checkpoint_corrente(rapporto, cp_ricevuta, corrente,
                                              consistenza_corrente)
            verifica_prova(rapporto, aggiornata, chiave_registro=chiave_registro,
                           chiave_testimone=chiave_testimone, impronta_documento=impronta,
                           origine=origine, checkpoint_corrente=corrente)
        if all(consistenza):
            try:
                prova_c = json.loads(leggi_testo(opzioni.prova_consistenza))
            except json.JSONDecodeError as exc:
                raise IngressoNonValido(f"{opzioni.prova_consistenza}: JSON non valido") from exc
            verifica_consistenza(rapporto, leggi_testo(opzioni.checkpoint_vecchio),
                                 leggi_testo(opzioni.checkpoint_nuovo), prova_c,
                                 chiave_registro=chiave_registro,
                                 chiave_testimone=chiave_testimone, origine=origine)
    except IngressoNonValido as exc:
        rapporto.aggiungi("Ingressi", False, str(exc))
    return rapporto.codice_uscita, rapporto.testo()


def main(argomenti: Sequence[str] | None = None) -> int:
    codice, testo = esegui(argomenti)
    sys.stdout.write(testo + "\n")
    return codice


if __name__ == "__main__":
    raise SystemExit(main())
