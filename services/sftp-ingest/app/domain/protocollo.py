# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Protocollo delle cartelle SFTP: regole pure (nessun I/O, nessun orologio).

Servizio: sftp-ingest. Riferimenti: piano R03 §3.2; REGOLE §7.4 (dominio puro),
§12.1 (idempotenza), §12.4 (ritardi come lista), §17.6 (il tempo è un argomento).
Documentazione per i clienti: docs/integrazione/sftp.md.

Struttura dell'area di un account (radice del chroot):
    in/<cartella>/            pacchetto da versare: file + indice.json
    in/<cartella>.tmp/        pacchetto in caricamento (ignorato)
    out/<cartella>/esito.json esito della lavorazione
    lavorazione/<cartella>/   pacchetto preso in carico (sola lettura)
    fatti/<cartella>/         pacchetto versato
    scarti/<cartella>/        pacchetto scartato
Una cartella è pronta quando: il nome è valido e non termina con «.tmp»,
contiene indice.json, nessun file con prefisso «.tmp-» e nulla è cambiato da
``stabilita_secondi``. Una cartella che non diventa mai pronta (indice assente,
file temporanei rimasti) è lavorata dopo ``abbandono_secondi`` e scartata con
il motivo, così il cliente riceve sempre un esito.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

#: Nome del file indice (stesso schema IndiceUD dell'API REST).
NOME_INDICE = "indice.json"
#: Nome del file d'esito scritto in out/<cartella>/.
NOME_ESITO = "esito.json"
#: Prefisso dei file ancora in caricamento.
PREFISSO_TEMPORANEO = ".tmp-"
#: Suffisso delle cartelle ancora in caricamento.
SUFFISSO_TEMPORANEO = ".tmp"
#: Versione del formato di esito.json (REGOLE §8.9).
VERSIONE_ESITO = "1"

#: Sottocartelle dell'area di un account.
IN, OUT, LAVORAZIONE, FATTI, SCARTI = "in", "out", "lavorazione", "fatti", "scarti"
SOTTOCARTELLE = (IN, OUT, LAVORAZIONE, FATTI, SCARTI)

#: Nome della cartella di un pacchetto (= identificativo del PdV del produttore).
_PATTERN_CARTELLA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LUNGHEZZA_MAX_NOME_FILE = 255


class StatoCartella(StrEnum):
    PRONTA = "PRONTA"
    IN_CARICAMENTO = "IN_CARICAMENTO"
    ABBANDONATA = "ABBANDONATA"


class TipoVoce(StrEnum):
    FILE = "file"
    CARTELLA = "cartella"
    ALTRO = "altro"  # collegamenti, dispositivi, socket


@dataclass(frozen=True)
class VoceCartella:
    """Un elemento di una cartella di pacchetto (letto con lstat, senza seguire link)."""

    nome: str
    tipo: TipoVoce
    dimensione: int
    modificato_il: float  # secondi epoch (mtime)


class ErroreCartella(Exception):
    """Pacchetto non lavorabile per un motivo del canale (codice SFTP-1nn), oppure
    per un esito che il gateway darebbe uguale (VRS-127, R03 i3)."""

    def __init__(self, codice: str, dettaglio: str,
                 dettagli: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"{codice}: {dettaglio}")
        self.codice = codice
        self.dettaglio = dettaglio
        self.dettagli: dict[str, Any] = dict(dettagli or {})


def nome_cartella_valido(nome: str) -> bool:
    """Nome di cartella di pacchetto ammesso (senza il suffisso «.tmp»)."""
    return (bool(_PATTERN_CARTELLA.fullmatch(nome))
            and not nome.lower().endswith(SUFFISSO_TEMPORANEO))


def nome_cartella_temporanea_valido(nome: str) -> bool:
    """Nome ``<cartella>.tmp`` con ``<cartella>`` valido."""
    if not nome.lower().endswith(SUFFISSO_TEMPORANEO):
        return False
    return nome_cartella_valido(nome[: -len(SUFFISSO_TEMPORANEO)])


def nome_file_valido(nome: str) -> bool:
    """Semplice nome di file: niente separatori, «.»/«..», caratteri di controllo,
    al massimo 255 byte UTF-8 (REGOLE §17.5: path traversal)."""
    if not nome or nome in (".", "..") or "/" in nome or "\\" in nome:
        return False
    try:
        if len(nome.encode("utf-8")) > _LUNGHEZZA_MAX_NOME_FILE:
            return False
    except UnicodeEncodeError:
        return False
    return not any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in nome)


def valuta_cartella(
    voci: Sequence[VoceCartella], *, modificata_il: float, adesso: float,
    stabilita_secondi: float, abbandono_secondi: float,
) -> StatoCartella:
    """Stato di una cartella di in/ dai suoi elementi.

    ``modificata_il`` è l'mtime della cartella stessa (cambia a ogni file
    creato, rinominato o eliminato); ``adesso`` è l'istante corrente (epoch)."""
    ultima = max([modificata_il, *(v.modificato_il for v in voci)])
    ferma_da = adesso - ultima
    completa = (any(v.nome == NOME_INDICE and v.tipo is TipoVoce.FILE for v in voci)
                and not any(v.nome.startswith(PREFISSO_TEMPORANEO) for v in voci))
    if completa and ferma_da >= stabilita_secondi:
        return StatoCartella.PRONTA
    if ferma_da >= abbandono_secondi:
        return StatoCartella.ABBANDONATA
    return StatoCartella.IN_CARICAMENTO


def prossima_valutazione(
    voci: Sequence[VoceCartella], *, modificata_il: float, adesso: float,
    stabilita_secondi: float, abbandono_secondi: float,
) -> float:
    """Secondi dopo cui una cartella oggi IN_CARICAMENTO potrebbe cambiare stato
    senza altre scritture del cliente (R03 f2, REV-15): fine della stabilità se
    è completa, altrimenti l'abbandono. Almeno 1 s."""
    ultima = max([modificata_il, *(v.modificato_il for v in voci)])
    completa = (any(v.nome == NOME_INDICE and v.tipo is TipoVoce.FILE for v in voci)
                and not any(v.nome.startswith(PREFISSO_TEMPORANEO) for v in voci))
    attesa = stabilita_secondi if completa else abbandono_secondi
    return max(1.0, ultima + attesa - adesso)


def problema_di_quota(problema: Mapping[str, Any]) -> bool:
    """Errore di quota del piano (QTA-001 al minuto, QTA-002 al giorno): non è un
    difetto del pacchetto, quindi non consuma i tentativi (c2, difetto 1)."""
    return str(problema.get("codice") or "").startswith("QTA-")


def nomi_file_dichiarati(indice: Mapping[str, Any]) -> list[str] | None:
    """Nomi dei file dichiarati nell'indice (documenti e allegati, in ordine,
    senza ripetizioni). ``None`` se l'indice non ha la forma attesa
    (``documenti`` lista non vuota di oggetti con ``nome_file`` testuale): in quel
    caso la validazione dello schema la fa il gateway (una sola fonte, §9.1)."""
    documenti = indice.get("documenti")
    if not isinstance(documenti, list) or not documenti:
        return None
    nomi: list[str] = []
    da_visitare: list[Any] = list(documenti)
    while da_visitare:
        documento = da_visitare.pop(0)
        if not isinstance(documento, Mapping):
            return None
        nome = documento.get("nome_file")
        if not isinstance(nome, str):
            return None
        if nome not in nomi:
            nomi.append(nome)
        allegati = documento.get("allegati")
        if allegati is not None:
            if not isinstance(allegati, list):
                return None
            da_visitare.extend(allegati)
    return nomi


def impronte_dichiarate(indice: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """``nome_file → {"algoritmo", "valore"}`` dichiarati nell'indice (solo le
    voci ben formate): passati alla sessione di caricamento, che li confronta."""
    risultato: dict[str, dict[str, str]] = {}
    da_visitare: list[Any] = list(indice.get("documenti") or [])
    while da_visitare:
        documento = da_visitare.pop(0)
        if not isinstance(documento, Mapping):
            continue
        hash_ = documento.get("hash")
        nome = documento.get("nome_file")
        if (isinstance(nome, str) and isinstance(hash_, Mapping)
                and isinstance(hash_.get("impronta"), str)):
            risultato.setdefault(nome, {
                "algoritmo": str(hash_.get("algoritmo") or "SHA-256"),
                "valore": hash_["impronta"],
            })
        if isinstance(documento.get("allegati"), list):
            da_visitare.extend(documento["allegati"])
    return risultato


def controlla_contenuto(voci: Iterable[VoceCartella], dichiarati: Sequence[str]) -> list[str]:
    """Controlli del canale sul contenuto di una cartella pronta.

    Restituisce i file da caricare (nell'ordine dell'indice) o solleva
    ``ErroreCartella`` con il primo motivo (SFTP-105, SFTP-103, SFTP-104)."""
    elenco = list(voci)
    non_ammessi = sorted(v.nome for v in elenco
                         if v.tipo is not TipoVoce.FILE or v.nome.startswith(PREFISSO_TEMPORANEO))
    if non_ammessi:
        raise ErroreCartella("SFTP-105", "La cartella contiene sottocartelle, collegamenti o "
                             "file temporanei", {"elementi": non_ammessi[:50]})
    nomi_non_validi = [n for n in dichiarati if not nome_file_valido(n) or n == NOME_INDICE]
    if nomi_non_validi:
        raise ErroreCartella("SFTP-105", "nome_file dell'indice non è un semplice nome di file",
                             {"nomi": nomi_non_validi[:50]})
    presenti = {v.nome for v in elenco if v.nome != NOME_INDICE}
    mancanti = [n for n in dichiarati if n not in presenti]
    if mancanti:
        raise ErroreCartella("SFTP-103", f"{len(mancanti)} file dichiarati nell'indice non sono "
                             "nella cartella", {"mancanti": mancanti[:50]})
    in_piu = sorted(presenti - set(dichiarati))
    if in_piu:
        raise ErroreCartella("SFTP-104", f"{len(in_piu)} file della cartella non sono "
                             "dichiarati nell'indice", {"non_dichiarati": in_piu[:50]})
    return list(dichiarati)


def _sha256(*parti: str) -> str:
    return hashlib.sha256("\n".join(parti).encode("utf-8")).hexdigest()


def chiave_idempotenza(tenant_id: str, cartella: str, impronta_indice: str) -> str:
    """``Idempotency-Key`` del versamento: impronta di (ente, nome cartella,
    impronta dell'indice). La stessa cartella reinviata non crea un PdV nuovo."""
    return "sftp-" + _sha256("versamento", str(tenant_id), cartella, impronta_indice)


def chiave_caricamento(lavorazione_id: str, nome_file: str, impronta_indice: str,
                       generazione: int) -> str:
    """``Idempotency-Key`` della sessione di caricamento di un file: dopo un crash
    la stessa richiesta restituisce la stessa sessione (ripresa delle parti).

    R03 i3 (richiesta 2 di f1): la chiave comprende l'impronta dell'indice, che
    dichiara l'impronta di ogni file (``hash`` obbligatorio). Se il cliente
    corregge la cartella mentre la lavorazione è ancora aperta (stesso nome,
    file diverso) le sessioni ripartono da capo con una chiave nuova, invece di
    ricevere 409 UPL-007 (stessa chiave, richiesta diversa)."""
    return "sftp-" + _sha256("caricamento", str(lavorazione_id), nome_file, impronta_indice,
                             str(generazione))


#: Stati di un PdV concluso con esito negativo: rifiutato dalla verifica (ERRORE) o
#: annullato. Stesso elenco del gateway (``versamento_service.STATI_PDV_NEGATIVI``,
#: contratto dello stato del PdV): un esito «VERSATA» locale che punta a un PdV in
#: questi stati non si riusa (R03 i3, richiesta 1 di f1).
STATI_PDV_NEGATIVI = frozenset({"ERRORE", "ANNULLATO"})


def ritardo_tentativo(tentativi_falliti: int, ritardi: Sequence[int]) -> int | None:
    """Attesa (secondi) prima del prossimo tentativo dopo ``tentativi_falliti``
    errori temporanei; ``None`` se i tentativi sono esauriti (§12.4)."""
    if tentativi_falliti < 1 or tentativi_falliti > len(ritardi):
        return None
    return int(ritardi[tentativi_falliti - 1])


def problema_ritentabile(problema: Mapping[str, Any]) -> bool:
    """Un problem+json del catalogo è ritentabile solo se lo dichiara."""
    return problema.get("ritentabile") is True


class StatoEsito(StrEnum):
    VERSATO = "VERSATO"
    SCARTATO = "SCARTATO"
    IN_RIPROVA = "IN_RIPROVA"


def costruisci_esito(
    *,
    cartella: str,
    stato: StatoEsito,
    lavorazione_id: str,
    adesso: datetime,
    tentativi: int,
    pdv_id: str | None = None,
    stato_pdv: str | None = None,
    avvisi: Sequence[Mapping[str, Any]] = (),
    errori: Sequence[Mapping[str, Any]] = (),
    link_stato: str | None = None,
    chiave: str | None = None,
    reinvio: bool = False,
    prossimo_tentativo: datetime | None = None,
    url_pubblico_api: str = "",
) -> dict[str, Any]:
    """Contenuto di out/<cartella>/esito.json (formato versione 1, documentato)."""
    base = url_pubblico_api.rstrip("/")
    rapporto = f"{base}/api/v1/versamento/{pdv_id}/rdv" if pdv_id else None
    stato_url = None
    if link_stato:
        stato_url = link_stato if link_stato.startswith("http") else f"{base}{link_stato}"
    elif pdv_id:
        stato_url = f"{base}/api/v1/versamento/{pdv_id}"
    return {
        "versione": VERSIONE_ESITO,
        "cartella": cartella,
        "stato": stato.value,
        "pdv_id": pdv_id,
        "stato_pdv": stato_pdv,
        "reinvio": reinvio,
        "avvisi": [dict(a) for a in avvisi],
        "errori": [dict(e) for e in errori],
        "link": {"stato": stato_url, "rapporto": rapporto},
        "chiave_idempotenza": chiave,
        "lavorazione_id": lavorazione_id,
        "tentativi": tentativi,
        "prossimo_tentativo": prossimo_tentativo.isoformat() if prossimo_tentativo else None,
        "elaborato_il": adesso.isoformat(),
    }


def prossimo_istante(adesso: datetime, secondi: int) -> datetime:
    return adesso + timedelta(seconds=secondi)
