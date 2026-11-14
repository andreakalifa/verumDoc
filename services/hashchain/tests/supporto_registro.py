# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Supporto ai test del registro a due livelli (R04): PdA di prova con albero di
livello 1 come li scrive l'aip-builder, servizi di firma finti, testimone
finto che applica davvero il protocollo K3 con libs/catena.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from app.registro.checkpoint import Identita
from app.registro.testimone import ClientTestimone
from app.services.firma_blocchi import (
    MOTIVO_NON_CONFIGURATO,
    STATO_DA_RITENTARE,
    STATO_FIRMATO,
    STATO_NON_FIRMATO,
    EsitoFirmaBlocco,
)

from libs.catena import nota
from libs.catena.foglie import ComponentePda, albero_pda
from libs.catena.rfc6962 import verifica_consistenza
from tests.infra_reale import sql

ORIGINE = "verumdoc.it/registro/v1/test"


def identita(testimone: bool = False) -> Identita:
    privata, _ = nota.genera_chiave("verumdoc-registro")
    return Identita(nota.analizza_firmatario(privata), ORIGINE, testimone)


@dataclass
class PdaDiProva:
    pdv_id: uuid.UUID
    aip_id: uuid.UUID
    urn: str
    tenant: uuid.UUID
    documenti: list[tuple[str, str, bytes]]
    radice: str | None


async def crea_pda(url: str, tenant: uuid.UUID, classe: uuid.UUID, *, n_doc: int = 2,
                   avvisi: list | None = None, con_albero: bool = True,
                   in_coda: bool = True, stato: str = "SU_STORAGE") -> PdaDiProva:
    """PdV IN_BUILD + PdA SU_STORAGE + documenti + (albero K1) + riga in coda."""
    pdv, aip = uuid.uuid4(), uuid.uuid4()
    urn = f"urn:test:{aip}"
    await sql(url, "INSERT INTO public.pdv (id, oggetto, stato, classe_doc_id, user_id, "
                   "tenant_id, avvisi) VALUES ($1, 'prova', 'IN_BUILD', $2, $3, $4, "
                   "CAST($5 AS jsonb))", pdv, classe, uuid.uuid4(), tenant,
              json.dumps(avvisi or []))
    documenti = [(f"doc-{i}", f"file-{i}.pdf", f"%PDF-1.7 {aip} {i}".encode())
                 for i in range(n_doc)]
    manifest = hashlib.sha256(b"manifest" + aip.bytes).hexdigest()
    await sql(url, "INSERT INTO public.aip (id, pdv_id, tenant_id, urn, stato, s3_key, "
                   "s3_bucket, hash_sha256) VALUES ($1, $2, $3, $4, CAST($5 AS public.aip_stato), "
                   "'k', 'b', $6)", aip, pdv, tenant, urn, stato, manifest)
    for i, (_, nome, contenuto) in enumerate(documenti):
        await sql(url, "INSERT INTO public.documenti (id, aip_id, nome_file, tipo, formato_mime, "
                       "dimensione_bytes, hash_sha256, hash_sha512, s3_key, ordine) VALUES "
                       "($1, $2, $3, 'PRINCIPALE', 'application/pdf', $4, $5, $6, 'k', $7)",
                  uuid.uuid4(), aip, nome, len(contenuto), hashlib.sha256(contenuto).hexdigest(),
                  hashlib.sha512(contenuto).hexdigest(), i)
    radice = None
    if con_albero:
        albero = albero_pda(urn, [
            ComponentePda(doc, nome, hashlib.sha256(c).hexdigest(), {"oggetto": doc}, i)
            for i, (doc, nome, c) in enumerate(documenti)
        ], hashlib.sha256(b"ipda" + aip.bytes).hexdigest())
        radice = albero.radice_hex
        await sql(url, "INSERT INTO public.catena_pda (aip_id, tenant_id, urn, radice, "
                       "num_foglie, foglie) VALUES ($1, $2, $3, $4, $5, CAST($6 AS jsonb))",
                  aip, tenant, urn, radice, len(albero.foglie), json.dumps(list(albero.foglie)))
    if in_coda:
        await sql(url, "INSERT INTO public.catena_coda (aip_id) VALUES ($1)", aip)
    return PdaDiProva(pdv, aip, urn, tenant, documenti, radice)


class FirmaFinta:
    """Servizio signature finto: NON_FIRMATO (nessun fornitore), DA_RITENTARE o FIRMATO."""

    def __init__(self, stato: str = STATO_NON_FIRMATO) -> None:
        self.stato = stato
        self.chiamate: list[bytes] = []

    async def firma(self, contenuto: bytes) -> EsitoFirmaBlocco:
        self.chiamate.append(contenuto)
        if self.stato == STATO_FIRMATO:
            return EsitoFirmaBlocco(STATO_FIRMATO, None,
                                    firma_b64=base64.b64encode(b"busta" + contenuto).decode(),
                                    tsa_timestamp=datetime(2026, 10, 23, 9, 0, tzinfo=UTC))
        if self.stato == STATO_DA_RITENTARE:
            return EsitoFirmaBlocco(STATO_DA_RITENTARE, "servizio di firma non disponibile")
        return EsitoFirmaBlocco(STATO_NON_FIRMATO, MOTIVO_NON_CONFIGURATO)


@dataclass
class FintoTestimone:
    """Testimone K3 in memoria: verifica firma del registro, ``old`` e consistenza."""

    verificatore_registro: nota.Verificatore
    privata: str = field(default_factory=lambda: nota.genera_chiave("verumdoc-testimone")[0])
    dimensione: int = 0
    radice: bytes = hashlib.sha256(b"").digest()
    risposta_forzata: tuple[int, str] | None = None
    richieste: list[str] = field(default_factory=list)
    #: ultima nota controfirmata (GET /api/v1/testimone/checkpoint, R04 terzo giro)
    ultima_nota: str | None = None
    letture: int = 0

    @property
    def firmatario(self) -> nota.Firmatario:
        return nota.analizza_firmatario(self.privata)

    def gestore(self, richiesta: httpx.Request) -> httpx.Response:
        if richiesta.method == "GET":  # ultimo checkpoint controfirmato (non è un invio)
            self.letture += 1
            if self.ultima_nota is None:
                return httpx.Response(404, text="nessun checkpoint controfirmato")
            return httpx.Response(200, text=self.ultima_nota)
        corpo = richiesta.content.decode("utf-8")
        self.richieste.append(corpo)
        if self.risposta_forzata is not None:
            return httpx.Response(self.risposta_forzata[0], text=self.risposta_forzata[1])
        testa, _, nota_firmata = corpo.partition("\n\n")
        righe = testa.split("\n")
        vecchia = int(righe[0].split()[1])
        prova = [base64.b64decode(r) for r in righe[1:] if r]
        if not nota.verifica(nota_firmata, [self.verificatore_registro]):
            return httpx.Response(403, text="firma del registro non valida")
        if vecchia != self.dimensione:
            return httpx.Response(409, text=f"{self.dimensione}\n")
        cp = nota.analizza(nota_firmata)
        if not verifica_consistenza(vecchia, cp.dimensione, self.radice, cp.radice, prova):
            return httpx.Response(422, text="prova non valida")
        self.dimensione, self.radice = cp.dimensione, cp.radice
        corpo_cp, _ = nota.separa(nota_firmata)
        riga = self.firmatario.firma(corpo_cp)
        self.ultima_nota = nota.aggiungi_firme(nota_firmata, [riga])
        return httpx.Response(200, text=riga + "\n")

    def client(self) -> ClientTestimone:
        return ClientTestimone("http://testimone", "token-servizio", self.firmatario.verificatore,
                               transport=httpx.MockTransport(self.gestore))


@asynccontextmanager
async def fatto_ciclo_vita(url: str, pda: PdaDiProva, tipo: str,
                           impronta: str | None = None) -> AsyncIterator[str]:
    """Revisione 2 di R05 (S2): scrive nel database il FATTO che giustifica una
    foglia del ciclo di vita (come lo lascia ``ciclo-vita`` al momento della
    richiesta) e restituisce l'id della pratica da mettere in
    ``riferimento.pratica``:

    - scarto: pratica SCARTO ESEGUIBILE con l'esecuzione avviata, oggetto
      ELIMINATO, PdA in ELIMINAZIONE;
    - annullamento: pratica ANNULLAMENTO ESEGUITA del PdV, PdV ANNULLATO;
    - rettifica: pratica RETTIFICA IN_VERSAMENTO del PdV.

    R06 (VD-C-119): per annullamento e rettifica anche il FATTO del lavoratore
    in ``esiti_esecuzione`` con l'impronta dell'atto (``impronta``, quella della
    foglia); la rettifica ha come PdV di rettifica il PdV stesso del PdA (il
    riferimento della foglia deve portare ``pdv_rettifica = pda.pdv_id``).

    All'uscita gli stati di PdA e PdV tornano quelli di prima (la registrazione
    del PdA nei test prosegue come prima) e la rettifica si chiude (una sola
    aperta per PdV)."""
    pratica = uuid.uuid4()
    prima = (await sql(url, "SELECT a.stato::text AS a, v.stato::text AS v FROM public.aip a "
                            "JOIN public.pdv v ON v.id = a.pdv_id WHERE a.id = $1",
                       pda.aip_id))[0]
    stato, lavoro, dati, pdv = {
        "scarto": ("ESEGUIBILE", "ESEGUI",
                   {"esecuzione": {"avviata_il": "2026-10-23T08:00:00+00:00"}}, None),
        "annullamento": ("ESEGUITA", None, {}, pda.pdv_id),
        "rettifica": ("IN_VERSAMENTO", None, {}, pda.pdv_id),
    }[tipo]
    await sql(url, "INSERT INTO public.pratiche (id, tenant_id, tipo, stato, motivo, dati, "
                   "lavoro, pdv_id, pdv_risultato_id) VALUES ($1, $2, $3, $4, "
                   "'pratica di prova del registro', CAST($5 AS jsonb), $6, $7, $8)",
              pratica, pda.tenant, tipo.upper(), stato, json.dumps(dati), lavoro, pdv,
              pda.pdv_id if tipo == "rettifica" else None)
    if tipo in ("annullamento", "rettifica") and impronta is not None:
        # R06 (VD-C-119): il fatto che in esercizio scrive SOLO il lavoratore
        dettagli = {"atto_sha256": impronta.lower()}
        if tipo == "rettifica":
            dettagli["pdv_rettifica"] = str(pda.pdv_id)
        await sql(url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, "
                       "aip_id, esito, dettagli) VALUES ($1, $2, $3, $4, $5, CAST($6 AS jsonb))",
                  tipo.upper(), pratica, pda.tenant, pda.aip_id,
                  "ATTO_ARCHIVIATO" if tipo == "annullamento" else "PDV_VERSATO",
                  json.dumps(dettagli))
    if tipo == "scarto":
        await sql(url, "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, aip_id, "
                       "urn, esito) VALUES ($1, $2, $3, $4, 'ELIMINATO')",
                  pratica, pda.tenant, pda.aip_id, pda.urn)
        await sql(url, "UPDATE public.aip SET stato = 'ELIMINAZIONE' WHERE id = $1", pda.aip_id)
        # Revisione 3 di R05 (T5): l'esito di esecuzione che in esercizio scrive
        # SOLO il login del lavoratore (qui il superutente dei test)
        await sql(url, "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, "
                       "aip_id, esito) VALUES ('SCARTO', $1, $2, $3, 'ELIMINATO')",
                  pratica, pda.tenant, pda.aip_id)
    elif tipo == "annullamento":
        await sql(url, "UPDATE public.pdv SET stato = 'ANNULLATO' WHERE id = $1", pda.pdv_id)
    try:
        yield str(pratica)
    finally:
        await sql(url, "UPDATE public.aip SET stato = CAST($2 AS public.aip_stato) WHERE id = $1",
                  pda.aip_id, prima["a"])
        await sql(url, "UPDATE public.pdv SET stato = CAST($2 AS public.pdv_stato) WHERE id = $1",
                  pda.pdv_id, prima["v"])
        if tipo == "rettifica":
            await sql(url, "UPDATE public.pratiche SET stato = 'COMPLETATA' WHERE id = $1",
                      pratica)
