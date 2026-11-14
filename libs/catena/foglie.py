# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/catena/foglie.py — dati delle foglie dei due livelli della catena (K1).

Riferimenti: contratto K1 di R04; REGOLE_SVILUPPO §9.4 (un calcolo, una sola
implementazione: aip-builder, hashchain e verificatore usano queste funzioni),
§12.8 (ordine totale e documentato).

Livello 1 — albero del PdA. Una foglia per ogni componente (file) del PdA, in
ordine (``ordine`` del documento nell'IPdA, poi identificativo del documento,
poi nome del file), più UNA foglia finale per l'IPdA::

    {"v":1,"pda":<urn>,"doc":<id documento>,"file":<nome>,"sha256":<hex>,
     "metadati":<SHA-256 hex del JSON canonico dei metadati del documento>}
    {"v":1,"pda":<urn>,"ipda":<SHA-256 hex dell'IPdA>}

Livello 2 — registro globale. Una foglia per PdA::

    {"v":1,"pda":<urn>,"radice_pda":<hex>,"ente":<tenant_id>,"conservato_il":<ISO 8601 UTC>}

più le foglie di RIVERSAMENTO dei PdA conservati prima del registro a due
livelli (decisione R04, vedi ``docs/architettura/catena.md`` §7)::

    {"v":1,"tipo":"riversamento","pda":<urn>,"impronta_pda":<SHA-256 hex del manifest>,
     "ente":<tenant_id>,"conservato_il":<ISO>[,"blocco":{"numero","hash","formato"}]}

Hash di foglia = SHA-256(0x00 || JSON canonico) (``rfc6962.hash_foglia``).
Gli istanti si scrivono sempre in UTC con microsecondi e «Z»
(``2026-10-23T08:15:00.000000Z``).

Formato 2 (R05, contratto Q3): foglie con un campo ``tipo`` obbligatorio, che
NON riguardano la conservazione di un contenuto ma un fatto successivo:

  - ciclo di vita di un PdA (``scarto``, ``annullamento``, ``rettifica``)::

        {"v":2,"tipo":"scarto","id":<uuid della foglia>,"pda":<urn>,"ente":<tenant_id>,
         "riferimento":{"pratica":<id>,...},"impronta_documento_pratica":<hex>,
         "registrato_il":<ISO>}

    lo scarto è un TOMBSTONE: prova che lo scarto è avvenuto (pratica, impronta
    del verbale) senza rivelare il contenuto;
  - export dell'audit nel WORM (``audit``, VD-C-112)::

        {"v":2,"tipo":"audit","id":<uuid>,"segmento":<chiave WORM>,
         "impronta_segmento":<hex>,"impronta_precedente":<hex>|null,
         "radice_righe":<hex>,"righe":<n>,"periodo_da":<ISO>,"periodo_a":<ISO>,
         "registrato_il":<ISO>}

``analizza_foglia_registro`` riconosce ogni formato; un formato o un tipo
sconosciuto è un errore (un verificatore non deve mai «indovinare»), e una
foglia di formato 2 non prova MAI la conservazione di un contenuto.
``stato_documento`` riassume le foglie del ciclo di vita di un PdA.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from libs.catena.canonico import json_canonico
from libs.catena.rfc6962 import hash_foglia, radice

VERSIONE_FOGLIE = 1
TIPO_RIVERSAMENTO = "riversamento"
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _hex(valore: str, nome: str) -> str:
    testo = str(valore).lower()
    if not _SHA256_HEX.fullmatch(testo):
        raise ValueError(f"{nome}: atteso SHA-256 esadecimale (64 cifre)")
    return testo


def iso_utc(istante: datetime) -> str:
    """Istante in UTC con microsecondi e «Z»; errore se senza fuso orario."""
    if istante.tzinfo is None:
        raise ValueError("Istante senza fuso orario")
    return istante.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _senza_decimali(valore: Any) -> Any:
    """Metadati con i numeri decimali scritti come testo (``repr``: forma più breve)."""
    if isinstance(valore, float):
        return repr(valore)
    if isinstance(valore, Mapping):
        return {str(k): _senza_decimali(v) for k, v in valore.items()}
    if isinstance(valore, list | tuple):
        return [_senza_decimali(v) for v in valore]
    return valore


def impronta_metadati(metadati: Mapping[str, Any] | None) -> str:
    """SHA-256 esadecimale del JSON canonico dei metadati del documento (``{}`` se
    assenti). I metadati arrivano dal produttore e possono contenere numeri
    decimali: qui (e solo qui) diventano testo con ``repr`` (es. 1.5 → "1.5"),
    così l'impronta resta definita senza rifiutare il versamento."""
    return hashlib.sha256(json_canonico(_senza_decimali(dict(metadati or {})))).hexdigest()


@dataclass(frozen=True)
class ComponentePda:
    """Un file del PdA come entra nell'albero di livello 1."""

    doc: str
    file: str
    sha256: str
    metadati: Mapping[str, Any] | None
    ordine: int


def ordina_componenti(componenti: Iterable[ComponentePda]) -> list[ComponentePda]:
    """Ordine totale delle foglie: ordine nell'IPdA, poi id del documento, poi nome."""
    return sorted(componenti, key=lambda c: (c.ordine, c.doc, c.file))


def dati_foglia_documento(pda: str, doc: str, file: str, sha256: str,
                          metadati: Mapping[str, Any] | None) -> bytes:
    """JSON canonico della foglia di un componente del PdA."""
    return json_canonico({
        "v": VERSIONE_FOGLIE, "pda": pda, "doc": doc, "file": file,
        "sha256": _hex(sha256, "sha256"), "metadati": impronta_metadati(metadati),
    })


def dati_foglia_ipda(pda: str, ipda_sha256: str) -> bytes:
    """JSON canonico della foglia dell'IPdA."""
    return json_canonico({"v": VERSIONE_FOGLIE, "pda": pda, "ipda": _hex(ipda_sha256, "ipda")})


@dataclass(frozen=True)
class AlberoPda:
    """Albero di livello 1: dati delle foglie (testo JSON canonico) e radice."""

    foglie: tuple[str, ...]
    radice: bytes
    documenti: tuple[str, ...]  # id del documento per foglia ("" per l'IPdA)

    @property
    def radice_hex(self) -> str:
        return self.radice.hex()

    def hash_foglie(self) -> list[bytes]:
        return [hash_foglia(f.encode("utf-8")) for f in self.foglie]

    def indice_documento(self, doc: str) -> int | None:
        """Indice della PRIMA foglia del documento ``doc`` (None se assente)."""
        try:
            return self.documenti.index(doc)
        except ValueError:
            return None


def albero_pda(pda: str, componenti: Iterable[ComponentePda], ipda_sha256: str) -> AlberoPda:
    """Foglie e radice del PdA (ordine totale di ``ordina_componenti``, IPdA per ultima)."""
    foglie: list[str] = []
    documenti: list[str] = []
    for c in ordina_componenti(componenti):
        foglie.append(dati_foglia_documento(pda, c.doc, c.file, c.sha256, c.metadati)
                      .decode("utf-8"))
        documenti.append(c.doc)
    foglie.append(dati_foglia_ipda(pda, ipda_sha256).decode("utf-8"))
    documenti.append("")
    return AlberoPda(
        tuple(foglie), radice([hash_foglia(f.encode("utf-8")) for f in foglie]),
        tuple(documenti),
    )


def albero_pda_da_foglie(foglie: Iterable[str]) -> AlberoPda:
    """Ricostruisce l'albero da dati di foglia salvati (testo JSON canonico)."""
    elenco = tuple(foglie)
    documenti = []
    for testo in elenco:
        dati = json.loads(testo)
        if json_canonico(dati).decode("utf-8") != testo:
            raise ValueError("Dato di foglia non in forma canonica")
        documenti.append(str(dati.get("doc", "")) if "ipda" not in dati else "")
    return AlberoPda(elenco, radice([hash_foglia(f.encode("utf-8")) for f in elenco]),
                     tuple(documenti))


def dati_foglia_registro(pda: str, radice_pda: str, ente: str, conservato_il: datetime) -> bytes:
    """JSON canonico della foglia del registro globale per un PdA."""
    return json_canonico({
        "v": VERSIONE_FOGLIE, "pda": pda, "radice_pda": _hex(radice_pda, "radice_pda"),
        "ente": str(ente), "conservato_il": iso_utc(conservato_il),
    })


def dati_foglia_riversamento(
    pda: str, impronta_pda: str, ente: str, conservato_il: datetime,
    blocco: Mapping[str, Any] | None = None,
) -> bytes:
    """JSON canonico della foglia di riversamento di un PdA conservato prima del
    registro a due livelli (cita il blocco d'origine se c'è)."""
    dati: dict[str, Any] = {
        "v": VERSIONE_FOGLIE, "tipo": TIPO_RIVERSAMENTO, "pda": pda,
        "impronta_pda": _hex(impronta_pda, "impronta_pda"), "ente": str(ente),
        "conservato_il": iso_utc(conservato_il),
    }
    if blocco is not None:
        dati["blocco"] = {"numero": int(blocco["numero"]), "hash": _hex(blocco["hash"], "hash"),
                          "formato": int(blocco["formato"])}
    return json_canonico(dati)


# ── Formato 2 (R05, contratto Q3): ciclo di vita e audit ────────────────────

VERSIONE_FOGLIE_2 = 2
TIPO_PDA = "pda"  # foglia di formato 1 senza «tipo»: conservazione di un PdA
TIPO_SCARTO = "scarto"
TIPO_ANNULLAMENTO = "annullamento"
TIPO_RETTIFICA = "rettifica"
TIPO_AUDIT = "audit"
#: R09: fatti del ciclo di vita che NON invalidano il contenuto del PdA:
#: chiusura con eccezioni di un PdA in eliminazione incompleta (D20),
#: minimizzazione dei metadati di un PdA scartato (D06), rinnovo del blocco WORM
#: di un PdA permanente (D04)
TIPO_CHIUSURA_ECCEZIONI = "chiusura_eccezioni"
TIPO_MINIMIZZAZIONE = "minimizzazione"
TIPO_RINNOVO_RETENTION = "rinnovo_retention"
#: Tipi del ciclo di vita di un PdA (contratto Q3; R09)
TIPI_CICLO_VITA = (TIPO_SCARTO, TIPO_ANNULLAMENTO, TIPO_RETTIFICA, TIPO_CHIUSURA_ECCEZIONI,
                   TIPO_MINIMIZZAZIONE, TIPO_RINNOVO_RETENTION)
#: Tipi di formato 2 riconosciuti
TIPI_V2 = (*TIPI_CICLO_VITA, TIPO_AUDIT)
#: Tipi dopo i quali il CONTENUTO del PdA non vale più come conservato
TIPI_CHE_INVALIDANO = (TIPO_SCARTO, TIPO_ANNULLAMENTO)

_CHIAVE_RIFERIMENTO = re.compile(r"[a-z_][a-z0-9_]{0,40}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
RIFERIMENTO_CHIAVI_MAX = 16
RIFERIMENTO_TESTO_MAX = 500


class FogliaNonValida(ValueError):
    """Foglia del registro malformata o di formato/tipo sconosciuto."""


def _uuid(valore: Any, nome: str) -> str:
    testo = str(valore).lower()
    if not _UUID.fullmatch(testo):
        raise FogliaNonValida(f"{nome}: atteso un UUID")
    return testo


def controlla_riferimento(riferimento: Mapping[str, Any]) -> dict[str, Any]:
    """Riferimento di una foglia del ciclo di vita: oggetto piatto con al più
    ``RIFERIMENTO_CHIAVI_MAX`` chiavi (minuscole, cifre, «_»), valori testo
    (al più ``RIFERIMENTO_TESTO_MAX`` caratteri), interi, booleani o null, e la
    chiave ``pratica`` (testo non vuoto) obbligatoria: è la chiave d'idempotenza."""
    if not isinstance(riferimento, Mapping):
        raise FogliaNonValida("riferimento: atteso un oggetto")
    if len(riferimento) > RIFERIMENTO_CHIAVI_MAX:
        raise FogliaNonValida(f"riferimento: al più {RIFERIMENTO_CHIAVI_MAX} chiavi")
    pulito: dict[str, Any] = {}
    for chiave, valore in riferimento.items():
        if not isinstance(chiave, str) or not _CHIAVE_RIFERIMENTO.fullmatch(chiave):
            raise FogliaNonValida(f"riferimento: chiave non ammessa {chiave!r}")
        if isinstance(valore, str):
            if len(valore) > RIFERIMENTO_TESTO_MAX:
                raise FogliaNonValida(f"riferimento.{chiave}: testo troppo lungo")
        elif not (valore is None or isinstance(valore, bool | int)):
            raise FogliaNonValida(f"riferimento.{chiave}: ammessi testo, interi, booleani, null")
        pulito[chiave] = valore
    pratica = pulito.get("pratica")
    if not isinstance(pratica, str) or not pratica.strip():
        raise FogliaNonValida("riferimento.pratica: obbligatoria (testo non vuoto)")
    return pulito


def dati_foglia_ciclo_vita(
    tipo: str, *, foglia_id: str, pda: str, ente: str, riferimento: Mapping[str, Any],
    impronta_documento_pratica: str, registrato_il: datetime,
) -> bytes:
    """JSON canonico della foglia di formato 2 di un fatto del ciclo di vita di un
    PdA (scarto = tombstone, annullamento, rettifica; R09: chiusura con eccezioni,
    minimizzazione, rinnovo del blocco WORM)."""
    if tipo not in TIPI_CICLO_VITA:
        raise FogliaNonValida(f"tipo del ciclo di vita sconosciuto: {tipo!r}")
    if not pda:
        raise FogliaNonValida("pda obbligatorio")
    return json_canonico({
        "v": VERSIONE_FOGLIE_2, "tipo": tipo, "id": _uuid(foglia_id, "id"), "pda": str(pda),
        "ente": _uuid(ente, "ente"), "riferimento": controlla_riferimento(riferimento),
        "impronta_documento_pratica": _hex(impronta_documento_pratica,
                                           "impronta_documento_pratica"),
        "registrato_il": iso_utc(registrato_il),
    })


def dati_foglia_audit(
    *, foglia_id: str, segmento: str, impronta_segmento: str, impronta_precedente: str | None,
    radice_righe: str, righe: int, periodo_da: datetime, periodo_a: datetime,
    registrato_il: datetime,
) -> bytes:
    """JSON canonico della foglia di formato 2 di un segmento dell'audit nel WORM."""
    if isinstance(righe, bool) or not isinstance(righe, int) or righe < 0:
        raise FogliaNonValida("righe: atteso un intero non negativo")
    return json_canonico({
        "v": VERSIONE_FOGLIE_2, "tipo": TIPO_AUDIT, "id": _uuid(foglia_id, "id"),
        "segmento": str(segmento), "impronta_segmento": _hex(impronta_segmento,
                                                             "impronta_segmento"),
        "impronta_precedente": None if impronta_precedente is None
        else _hex(impronta_precedente, "impronta_precedente"),
        "radice_righe": _hex(radice_righe, "radice_righe"), "righe": righe,
        "periodo_da": iso_utc(periodo_da), "periodo_a": iso_utc(periodo_a),
        "registrato_il": iso_utc(registrato_il),
    })


@dataclass(frozen=True)
class FogliaRegistro:
    """Foglia del registro globale interpretata (qualunque formato)."""

    versione: int
    tipo: str            # pda, riversamento, scarto, annullamento, rettifica, audit
    pda: str | None
    dati: Mapping[str, Any]

    @property
    def prova_conservazione(self) -> bool:
        """True solo per le foglie che provano la conservazione di un PdA (formato 1)."""
        return self.versione == VERSIONE_FOGLIE and self.tipo in (TIPO_PDA, TIPO_RIVERSAMENTO)

    @property
    def ciclo_vita(self) -> bool:
        return self.versione == VERSIONE_FOGLIE_2 and self.tipo in TIPI_CICLO_VITA


_CAMPI_V2: dict[str, frozenset[str]] = {
    **{t: frozenset({"v", "tipo", "id", "pda", "ente", "riferimento",
                     "impronta_documento_pratica", "registrato_il"}) for t in TIPI_CICLO_VITA},
    TIPO_AUDIT: frozenset({"v", "tipo", "id", "segmento", "impronta_segmento",
                           "impronta_precedente", "radice_righe", "righe", "periodo_da",
                           "periodo_a", "registrato_il"}),
}


def analizza_foglia_registro(testo: str | bytes) -> FogliaRegistro:
    """Interpreta il JSON canonico di una foglia del registro globale.

    Formato 1: senza ``tipo`` = foglia di un PdA (``radice_pda`` obbligatoria),
    ``tipo: riversamento``. Formato 2: i tipi di ``TIPI_V2`` con esattamente i
    loro campi. Qualunque altro formato o tipo, una foglia non canonica o
    incompleta → ``FogliaNonValida`` (mai un'interpretazione «di ripiego»)."""
    grezzo = testo.encode("utf-8") if isinstance(testo, str) else bytes(testo)
    try:
        dati = json.loads(grezzo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FogliaNonValida("foglia: JSON non valido") from exc
    if not isinstance(dati, dict) or json_canonico(dati) != grezzo:
        raise FogliaNonValida("foglia: non in forma canonica")
    versione = dati.get("v")
    if versione == VERSIONE_FOGLIE:
        tipo = dati.get("tipo", TIPO_PDA)
        if tipo == TIPO_PDA:
            if "tipo" in dati or not dati.get("pda") or not dati.get("radice_pda"):
                raise FogliaNonValida("foglia di un PdA incompleta (pda, radice_pda)")
            _hex(dati["radice_pda"], "radice_pda")
        elif tipo == TIPO_RIVERSAMENTO:
            if not dati.get("pda") or not dati.get("impronta_pda"):
                raise FogliaNonValida("foglia di riversamento incompleta (pda, impronta_pda)")
        else:
            raise FogliaNonValida(f"foglia di formato 1 con tipo sconosciuto {tipo!r}")
        return FogliaRegistro(VERSIONE_FOGLIE, str(tipo), str(dati["pda"]), dati)
    if versione == VERSIONE_FOGLIE_2:
        tipo = dati.get("tipo")
        if tipo not in _CAMPI_V2:
            raise FogliaNonValida(f"foglia di formato 2 con tipo sconosciuto {tipo!r}: "
                                  "aggiornare il verificatore")
        if set(dati) != _CAMPI_V2[tipo]:
            raise FogliaNonValida(f"foglia {tipo}: campi diversi da quelli del formato 2")
        _uuid(dati["id"], "id")
        if tipo == TIPO_AUDIT:
            for campo in ("impronta_segmento", "radice_righe"):
                _hex(dati[campo], campo)
            return FogliaRegistro(VERSIONE_FOGLIE_2, TIPO_AUDIT, None, dati)
        controlla_riferimento(dati["riferimento"])
        _hex(dati["impronta_documento_pratica"], "impronta_documento_pratica")
        if not dati.get("pda"):
            raise FogliaNonValida(f"foglia {tipo}: pda obbligatorio")
        return FogliaRegistro(VERSIONE_FOGLIE_2, str(tipo), str(dati["pda"]), dati)
    raise FogliaNonValida(f"formato di foglia sconosciuto (v={versione!r}): aggiornare il "
                          "verificatore")


@dataclass(frozen=True)
class EventoCicloVita:
    """Un fatto del ciclo di vita di un PdA, con la sua posizione nel registro."""

    tipo: str
    pratica: str
    registrato_il: str
    impronta_documento_pratica: str
    indice: int | None = None

    def descrizione(self) -> str:
        verbo = {TIPO_SCARTO: "scartato", TIPO_ANNULLAMENTO: "annullato",
                 TIPO_RETTIFICA: "metadati rettificati",
                 TIPO_CHIUSURA_ECCEZIONI: "scarto incompleto chiuso con eccezioni",
                 TIPO_MINIMIZZAZIONE: "metadati ridotti all'elenco di scarto",
                 TIPO_RINNOVO_RETENTION: "blocco WORM rinnovato"}[self.tipo]
        return f"{verbo} il {self.registrato_il} con pratica {self.pratica}"


@dataclass(frozen=True)
class StatoDocumento:
    """Stato di un PdA secondo le foglie del ciclo di vita (in ordine di registro).

    ``invalidato``: un evento di ``TIPI_CHE_INVALIDANO`` (scarto, annullamento)
    è presente: il contenuto NON vale più come conservato, qualunque altra prova."""

    eventi: tuple[EventoCicloVita, ...]

    @property
    def invalidante(self) -> EventoCicloVita | None:
        """Il primo evento che invalida il contenuto (scarto prima di annullamento)."""
        for tipo in TIPI_CHE_INVALIDANO:
            for evento in self.eventi:
                if evento.tipo == tipo:
                    return evento
        return None

    @property
    def invalidato(self) -> bool:
        return self.invalidante is not None

    @property
    def rettifiche(self) -> tuple[EventoCicloVita, ...]:
        return tuple(e for e in self.eventi if e.tipo == TIPO_RETTIFICA)

    @property
    def codice(self) -> str:
        """CONSERVATO, SCARTATO, ANNULLATO o RETTIFICATO."""
        evento = self.invalidante
        if evento is not None:
            return "SCARTATO" if evento.tipo == TIPO_SCARTO else "ANNULLATO"
        return "RETTIFICATO" if self.rettifiche else "CONSERVATO"


def stato_documento(pda: str, foglie: Iterable[tuple[FogliaRegistro, int | None]]
                    ) -> StatoDocumento:
    """Stato del PdA ``pda`` dalle sue foglie del ciclo di vita (foglia, indice).
    Una foglia di un altro PdA o di un altro tipo è un errore del chiamante."""
    eventi: list[EventoCicloVita] = []
    for foglia, indice in sorted(foglie, key=lambda x: -1 if x[1] is None else x[1]):
        if not foglia.ciclo_vita:
            raise FogliaNonValida(f"foglia {foglia.tipo}: non è del ciclo di vita")
        if foglia.pda != pda:
            raise FogliaNonValida("foglia del ciclo di vita di un altro PdA")
        eventi.append(EventoCicloVita(
            foglia.tipo, str(foglia.dati["riferimento"]["pratica"]),
            str(foglia.dati["registrato_il"]), str(foglia.dati["impronta_documento_pratica"]),
            indice))
    return StatoDocumento(tuple(eventi))


__all__ = [
    "AlberoPda", "ComponentePda", "EventoCicloVita", "FogliaNonValida", "FogliaRegistro",
    "StatoDocumento", "TIPI_CHE_INVALIDANO", "TIPI_CICLO_VITA", "TIPI_V2",
    "TIPO_ANNULLAMENTO", "TIPO_AUDIT", "TIPO_PDA", "TIPO_RETTIFICA", "TIPO_RIVERSAMENTO",
    "TIPO_CHIUSURA_ECCEZIONI", "TIPO_MINIMIZZAZIONE", "TIPO_RINNOVO_RETENTION",
    "TIPO_SCARTO", "VERSIONE_FOGLIE", "VERSIONE_FOGLIE_2", "albero_pda", "albero_pda_da_foglie",
    "analizza_foglia_registro", "controlla_riferimento", "dati_foglia_audit",
    "dati_foglia_ciclo_vita", "dati_foglia_documento", "dati_foglia_ipda",
    "dati_foglia_registro", "dati_foglia_riversamento", "impronta_metadati", "iso_utc",
    "ordina_componenti", "stato_documento",
]
