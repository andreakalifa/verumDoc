# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/verumdoc_rules/campione.py — campione dei PdV recenti e configurazioni
«prima» e «dopo» per il confronto (R05 Q1; REGOLE_SVILUPPO §8.11, §13.4).

Usato dall'anteprima delle bozze (onboarding) e da ``tools/confronta_regole.py``
con LO STESSO codice: il chiamante fornisce solo l'esecutore delle query
(``Esecutore``: testo SQL + parametri → righe), così il pacchetto resta privo di
dipendenze dal database.

Scala (obiettivo 1 milione di PdV al giorno, enti con milioni di PdV):
  - il campione è dei soli ultimi ``limite`` PdV della finestra (``giorni``)
    nell'ambito, letti con gli indici ``idx_pdv_data_ric_desc``,
    ``idx_pdv_tenant_ricezione`` e ``idx_pdv_classe_ricezione`` (migrazione 036)
    in ordine di ricezione decrescente: mai ``COUNT(*)`` né scansioni complete;
  - la configurazione di TUTTI i contesti del campione si legge con cinque
    query in blocco (``= ANY``), non una per PdV;
  - il tempo massimo lo applica ``confronta`` (``tempo_scaduto``) e il chiamante
    imposta ``statement_timeout`` sulla transazione.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from libs.verumdoc_rules.accordi import AccordoVersamento
from libs.verumdoc_rules.confronta import PdvCampione, dati_da_pdv
from libs.verumdoc_rules.impostazioni import Livello, RegistroImpostazioni
from libs.verumdoc_rules.lettura import (
    accordi_da_righe,
    json_o_none,
    livelli_da_righe,
    prepara_ambiente,
    profili_classe_da_righe,
    profili_tipologia_da_righe,
    valori_colonne_classe,
)
from libs.verumdoc_rules.override import valida_override
from libs.verumdoc_rules.profili import ProfiloMetadati
from libs.verumdoc_rules.regole import AmbienteRegole

#: Esecutore asincrono: (testo SQL, parametri) → righe come mapping.
Esecutore = Callable[[str, Mapping[str, Any]], Awaitable[Sequence[Mapping[str, Any]]]]

_COLONNE_PDV = (
    "SELECT id::text AS id, tenant_id::text AS tenant_id, classe_doc_id::text AS classe_id, "
    "data_ricezione, (metadata_json->'indice')::text AS indice, "
    "(metadata_json->'docs_s3_keys')::text AS documenti FROM public.pdv "
)
#: Campioni per ambito (ultimi PdV per data di ricezione, indici dichiarati sopra).
SQL_CAMPIONE_TUTTI = (_COLONNE_PDV + "WHERE data_ricezione >= :da "
                      "ORDER BY data_ricezione DESC, id DESC LIMIT :n")
SQL_CAMPIONE_ENTE = (_COLONNE_PDV + "WHERE tenant_id = :t AND data_ricezione >= :da "
                     "ORDER BY data_ricezione DESC, id DESC LIMIT :n")
SQL_CAMPIONE_CLASSE = (_COLONNE_PDV + "WHERE classe_doc_id = :c AND data_ricezione >= :da "
                       "ORDER BY data_ricezione DESC, id DESC LIMIT :n")
SQL_CAMPIONE_ENTE_CLASSE = (_COLONNE_PDV + "WHERE classe_doc_id = :c AND tenant_id = :t "
                            "AND data_ricezione >= :da "
                            "ORDER BY data_ricezione DESC, id DESC LIMIT :n")

#: Configurazione dei contesti del campione, in blocco.
SQL_VALORI_BLOCCO = (
    "SELECT livello, tenant_id::text AS tenant_id, classe_id::text AS classe_id, codice, "
    "valore::text AS valore FROM public.impostazioni_valori "
    "WHERE livello = 'CONSERVATORE' "
    "   OR (livello = 'ENTE' AND tenant_id = ANY(CAST(:ts AS uuid[]))) "
    "   OR (livello = 'CLASSE' AND classe_id = ANY(CAST(:cs AS uuid[])))"
)
SQL_CLASSI_BLOCCO = (
    "SELECT id::text AS id, codice, formati_ammessi::text AS formati_ammessi, "
    "metadati_obbligatori::text AS metadati_obbligatori, max_dimensione_mb "
    "FROM public.classi_documentali WHERE id = ANY(CAST(:cs AS uuid[]))"
)
SQL_PROFILI_CLASSE_BLOCCO = (
    "SELECT id::text AS id, classe_id::text AS classe_id, versione, valido_dal, valido_al, "
    "campi::text AS campi FROM public.profili_metadati "
    "WHERE classe_id = ANY(CAST(:cs AS uuid[]))"
)
SQL_ACCORDI_BLOCCO = (
    "SELECT id::text AS id, tenant_id::text AS tenant_id, codice, versione, "
    "classe_id::text AS classe_id, valido_dal, cessato, valori::text AS valori, descrizione "
    "FROM public.accordi_versamento WHERE tenant_id = ANY(CAST(:ts AS uuid[]))"
)
SQL_PROFILI_TIPOLOGIA_TUTTI = (
    "SELECT id::text AS id, codice, versione, valido_dal, valido_al, campi::text AS campi, "
    "norm_ref, adozione FROM public.profili_tipologia"
)


@dataclass(frozen=True)
class Variante:
    """Cosa cambia la bozza («dopo»), applicato solo ai PdV del suo ambito.

    ``livello``/``ente``/``classe`` e ``valori``: valori da aggiungere (o, con
    ``ASSENTE``, da togliere: chiavi in ``togli``) a quel livello;
    ``accordo``: nuova versione di un accordo; ``profilo_tipologia`` e
    ``profilo_classe``: nuova versione di un profilo."""

    livello: Livello | None = None
    ente: str | None = None
    classe: str | None = None
    valori: Mapping[str, Any] = field(default_factory=dict)
    togli: frozenset[str] = frozenset()
    accordo: AccordoVersamento | None = None
    profilo_tipologia: ProfiloMetadati | None = None
    profilo_classe: ProfiloMetadati | None = None


@dataclass(frozen=True)
class PdvLetto(PdvCampione):
    """PdV del campione con l'indice normalizzato (accordo e override richiesti)."""

    dati_indice: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ContestiCampione:
    """Configurazione letta in blocco per i contesti del campione."""

    registro: RegistroImpostazioni
    adesso: datetime
    conservatore: dict[str, Any] = field(default_factory=dict)
    enti: dict[str, dict[str, Any]] = field(default_factory=dict)
    classi_valori: dict[str, dict[str, Any]] = field(default_factory=dict)
    classi_codice: dict[str, str] = field(default_factory=dict)
    profili_classe: dict[str, list[ProfiloMetadati]] = field(default_factory=dict)
    accordi: dict[str, list[AccordoVersamento]] = field(default_factory=dict)
    profili_tipologia: dict[str, list[ProfiloMetadati]] = field(default_factory=dict)
    conservatore_ambiente: dict[str, Any] = field(default_factory=dict)

    def _livelli(self, tenant: str, classe: str) -> dict[Livello, dict[str, Any]]:
        livelli = livelli_da_righe((), conservatore_ambiente=self.conservatore_ambiente)
        livelli[Livello.CONSERVATORE].update(self.conservatore)
        livelli[Livello.ENTE].update(self.enti.get(tenant, {}))
        livelli[Livello.CLASSE].update(self.classi_valori.get(classe, {}))
        return livelli

    def ambiente(self, pdv: PdvLetto, variante: Variante | None = None) -> AmbienteRegole:
        """Ambiente del motore per il PdV (configurazione di oggi, più la variante
        se il PdV è nel suo ambito). Solleva ValueError se non si risolve."""
        livelli = self._livelli(pdv.tenant_id, pdv.classe_id)
        accordi = list(self.accordi.get(pdv.tenant_id, ()))
        tipologie = {k: list(v) for k, v in self.profili_tipologia.items()}
        profili = list(self.profili_classe.get(pdv.classe_id, ()))
        if variante is not None:
            nel_ambito = (
                (variante.ente is None or variante.ente == pdv.tenant_id)
                and (variante.classe is None or variante.classe == pdv.classe_id))
            if nel_ambito and variante.livello is not None:
                livelli.setdefault(variante.livello, {}).update(variante.valori)
                for codice in variante.togli:
                    livelli[variante.livello].pop(codice, None)
            if nel_ambito and variante.accordo is not None:
                accordi.append(variante.accordo)
            if variante.profilo_tipologia is not None:
                tipologie.setdefault(variante.profilo_tipologia.codice, []).append(
                    variante.profilo_tipologia)
            if nel_ambito and variante.profilo_classe is not None:
                profili.append(variante.profilo_classe)
        indice = pdv.dati_indice
        override = valida_override(indice.get("override"), self.registro) \
            if isinstance(indice.get("override"), Mapping) else None
        richiesto = indice.get("accordo") if isinstance(indice.get("accordo"), str) else None
        return prepara_ambiente(
            registro=self.registro, livelli=livelli, adesso=self.adesso,
            tenant_id=pdv.tenant_id, classe_id=pdv.classe_id, profili_classe=profili,
            profili_tipologia_db=tipologie, accordi=accordi, accordo_richiesto=richiesto,
            override=override)


@dataclass
class Campione:
    pdv: list[PdvLetto]
    contesti: ContestiCampione
    limite: int
    giorni: int
    metodo: str

    def dichiarazione(self) -> dict[str, Any]:
        """Come è stato scelto il campione (va nella risposta dell'anteprima)."""
        return {"metodo": self.metodo, "finestra_giorni": self.giorni, "limite": self.limite,
                "letti": len(self.pdv), "raggiunto_limite": len(self.pdv) >= self.limite}


async def carica_campione(esegui: Esecutore, *, registro: RegistroImpostazioni,
                          adesso: datetime, giorni: int, limite: int,
                          ente: str | None = None, classe: str | None = None,
                          campi_noti: frozenset[str],
                          normalizza: Callable[[str], str] | None = None,
                          conservatore_ambiente: Mapping[str, Any] | None = None) -> Campione:
    """Legge il campione dei PdV recenti dell'ambito e la configurazione dei loro
    contesti (cinque query in blocco)."""
    da = adesso - timedelta(days=giorni)
    parametri: dict[str, Any] = {"da": da, "n": limite, "t": ente, "c": classe}
    if classe and ente:
        sql, metodo = SQL_CAMPIONE_ENTE_CLASSE, "ultimi PdV della classe per l'ente"
    elif classe:
        sql, metodo = SQL_CAMPIONE_CLASSE, "ultimi PdV della classe"
    elif ente:
        sql, metodo = SQL_CAMPIONE_ENTE, "ultimi PdV dell'ente"
    else:
        sql, metodo = SQL_CAMPIONE_TUTTI, "ultimi PdV di tutti gli enti"
    righe = await esegui(sql, parametri)
    pdv: list[PdvLetto] = []
    for r in righe:
        indice = json_o_none(r["indice"]) or {}
        documenti = json_o_none(r["documenti"]) or []
        pdv.append(PdvLetto(
            id=str(r["id"]), tenant_id=str(r["tenant_id"]), classe_id=str(r["classe_id"]),
            dati=dati_da_pdv(indice, documenti, campi_noti, normalizza),
            accordo=indice.get("accordo") if isinstance(indice, dict) else None,
            ricevuto_il=r["data_ricezione"].isoformat() if r["data_ricezione"] else None,
            dati_indice=indice if isinstance(indice, dict) else {}))
    contesti = ContestiCampione(registro=registro, adesso=adesso,
                                conservatore_ambiente=dict(conservatore_ambiente or {}))
    enti = sorted({p.tenant_id for p in pdv} | ({ente} if ente else set()))
    classi = sorted({p.classe_id for p in pdv} | ({classe} if classe else set()))
    blocco = {"ts": enti, "cs": classi}
    for r in await esegui(SQL_VALORI_BLOCCO, blocco):
        valore = json_o_none(r["valore"])
        if r["livello"] == "CONSERVATORE":
            contesti.conservatore[r["codice"]] = valore
        elif r["livello"] == "ENTE":
            contesti.enti.setdefault(str(r["tenant_id"]), {})[r["codice"]] = valore
        else:
            contesti.classi_valori.setdefault(str(r["classe_id"]), {})[r["codice"]] = valore
    for r in await esegui(SQL_CLASSI_BLOCCO, blocco):
        cid = str(r["id"])
        contesti.classi_codice[cid] = str(r["codice"])
        contesti.classi_valori.setdefault(cid, {}).update(valori_colonne_classe(
            json_o_none(r["formati_ammessi"]) or [], json_o_none(r["metadati_obbligatori"]) or [],
            r["max_dimensione_mb"]))
    per_classe: dict[str, list[Mapping[str, Any]]] = {}
    for r in await esegui(SQL_PROFILI_CLASSE_BLOCCO, blocco):
        per_classe.setdefault(str(r["classe_id"]), []).append(r)
    for cid, profili in per_classe.items():
        contesti.profili_classe[cid] = profili_classe_da_righe(
            profili, contesti.classi_codice.get(cid, cid))
    for accordo in accordi_da_righe(await esegui(SQL_ACCORDI_BLOCCO, blocco)):
        contesti.accordi.setdefault(accordo.tenant_id, []).append(accordo)
    contesti.profili_tipologia = profili_tipologia_da_righe(
        await esegui(SQL_PROFILI_TIPOLOGIA_TUTTI, {}))
    return Campione(pdv=pdv, contesti=contesti, limite=limite, giorni=giorni, metodo=metodo)


__all__ = [
    "Campione",
    "ContestiCampione",
    "Esecutore",
    "PdvLetto",
    "Variante",
    "carica_campione",
]
