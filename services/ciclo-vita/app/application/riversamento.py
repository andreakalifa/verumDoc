# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/riversamento.py — riversamento in uscita, restituzione di fine
contratto e riversamento in ingresso.

Riferimenti: contratto Q2 di R05; Linee guida AgID §4.9-§4.10 (interoperabilità
fra conservatori, cessazione); REGOLE_SVILUPPO §9.1 (nessuna logica duplicata:
i PdD li fa distribution, i versamenti il gateway), §13.1 (anteprima/applica),
§12.9, §12.11, §15.6 (export registrati), §19.5 (file ricevuti: dato non fidato).

USCITA / RESTITUZIONE (lavoratore, passo ``ESEGUI``):
  1. PdA SELEZIONATI a lotti (≤ 100) → ``POST /api/v2/migration/export-batch``
     di distribution → un PdD MIGRAZIONE per PdA (IN_CORSO con ``pdd_id``);
  2. stato di ogni PdD finché PRONTO (ESPORTATO, impronta dello ZIP) o in errore;
  3. solo RESTITUZIONE: export dei registri dell'ente (audit, pratiche, foglie
     del registro delle impronte dei suoi PdA) in JSON Lines nel WORM;
  4. manifesto (JSON) nel WORM, transizione → ESEGUITA, evento ``riversamento.pronto``.

INGRESSO: il PdD SInCRO di un altro conservatore arriva con le sessioni di
caricamento del gateway (``caricamento_id``):
  - ``ACQUISISCI``: copia nel WORM (prova di ciò che si è ricevuto), lettura
    dello ZIP senza scaricarlo (richieste ``Range``), indice letto con il parser
    sicuro, impronta di OGNI file ricalcolata sui byte e confrontata; esito per
    file; con un rilievo bloccante la pratica è RESPINTA dal sistema;
  - ``ESEGUI`` (dopo ESEGUIBILE dal conservatore): ogni file con una sessione di
    caricamento del gateway (impronta ricalcolata durante l'invio), poi UN
    versamento per riferimento con i metadati che citano l'IdC d'origine
    (``riversamento_*``: entrano nell'impronta dei metadati della foglia del
    documento nel registro).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import uuid
import zipfile
from collections.abc import AsyncIterator, Mapping
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import lease
from app.application import pratiche as pr
from app.application.puntuali import NAMESPACE_DOCUMENTI, per_conto_di
from app.application.scarto import FUSO, LOTTO_INSERIMENTO
from app.config import settings
from app.domain import scarto as dom
from app.domain import stati as st
from app.domain.documenti import manifesto
from app.domain.indice_pdd import IndiceNonValido, leggi_indice_pdd, nome_sicuro
from app.domain.permessi import Identita, motivo_blocco_avvio
from app.infrastructure import repository as repo
from app.infrastructure import storage
from app.infrastructure.distribution import Esportatore
from app.infrastructure.gateway import ErroreGateway, Versatore
from libs.shared.errori import ErroreApi
from libs.shared.security.audit import registra_audit
from libs.shared.storage.worm import aggiungi_anni
from libs.shared.zip_limiti import MembroZipNonAmmesso, controlla_membro, leggi_membro

logger = structlog.get_logger(__name__)

LAVORO_ACQUISISCI = "ACQUISISCI"
ESPORTATO = "ESPORTATO"
STATI_PDD_FINALI_KO = ("ERRORE", "SCADUTO")

_SELEZIONE = """
SELECT a.id::text AS aip_id, a.urn, a.stato::text AS stato
FROM public.aip a JOIN public.pdv v ON v.id = a.pdv_id
JOIN public.classi_documentali c ON c.id = v.classe_doc_id
WHERE a.tenant_id = :ente {filtri}
ORDER BY a.id
"""


def _criteri(criteri: Mapping[str, Any] | None) -> dict[str, Any]:
    criteri = criteri or {}
    entro = criteri.get("scadenza_entro")
    return {"classi": sorted({str(c) for c in (criteri.get("classi") or [])}),
            "anni": sorted({int(a) for a in (criteri.get("anni") or [])}),
            "scadenza_entro": date.fromisoformat(str(entro)).isoformat() if entro else None}


def _sql(criteri: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    filtri, parametri = [], {}
    if criteri.get("classi"):
        filtri.append("AND c.codice = ANY(:classi)")
        parametri["classi"] = criteri["classi"]
    if criteri.get("anni"):
        filtri.append("AND CAST(EXTRACT(YEAR FROM a.data_versamento AT TIME ZONE "
                      "'Europe/Rome') AS integer) = ANY(:anni)")
        parametri["anni"] = criteri["anni"]
    if criteri.get("scadenza_entro"):
        filtri.append("AND a.scadenza_conservazione <= :entro")
        giorno = date.fromisoformat(criteri["scadenza_entro"])
        parametri["entro"] = datetime.combine(giorno + timedelta(days=1), datetime.min.time(),
                                              tzinfo=FUSO)
    return _SELEZIONE.format(filtri=" ".join(filtri)), parametri


def _motivo_uscita(stato: str) -> str | None:
    """Si esporta solo un PdA conservato (gli altri: bloccati con motivo)."""
    return None if stato == "CONSERVATO" else dom.STATO_NON_CONSERVATO


async def _piano_uscita(session: AsyncSession, tipo: str, ente: uuid.UUID,
                        criteri: Mapping[str, Any], *, pratica_id: uuid.UUID | None = None
                        ) -> dict[str, Any]:
    sql, parametri = _sql(criteri)
    impronta = dom.ImprontaPiano(tipo, str(ente), criteri)
    conteggi: dict[str, int] = {}
    esempi: list[dict[str, Any]] = []
    lotto: list[dict[str, Any]] = []
    risultato = await session.stream(text(sql), {"ente": ente, **parametri})
    async for riga in risultato.mappings():
        if impronta.elementi >= settings.riversamento_max_pda_per_pratica:
            raise ErroreApi("LC-422-CRITERI", f"oltre {settings.riversamento_max_pda_per_pratica} "
                                              "PdA per pratica: restringere i criteri")
        motivo = _motivo_uscita(riga["stato"])
        esito = dom.BLOCCATO if motivo else dom.SELEZIONATO
        impronta.aggiungi(riga["aip_id"], esito, motivo)
        conteggi[esito] = conteggi.get(esito, 0) + 1
        if len(esempi) < settings.anteprima_esempi:
            esempi.append({"aip_id": riga["aip_id"], "urn": riga["urn"], "esito": esito,
                           "motivo": motivo})
        if pratica_id is not None:
            lotto.append({"p": pratica_id, "t": ente, "aip": riga["aip_id"], "urn": riga["urn"],
                          "esito": esito, "motivo": motivo})
            if len(lotto) >= LOTTO_INSERIMENTO:
                await _inserisci(session, lotto)
                lotto = []
    if lotto:
        await _inserisci(session, lotto)
    return {"impronta_piano": impronta.hex(), "totale": impronta.elementi, "conteggi": conteggi,
            "esempi": esempi, "limite_esempi": settings.anteprima_esempi}


async def _inserisci(session: AsyncSession, righe: list[dict[str, Any]]) -> None:
    await session.execute(text(
        "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, aip_id, urn, esito, motivo) "
        "VALUES (:p, :t, CAST(:aip AS uuid), :urn, :esito, :motivo)"), righe)


async def autorizzazione_produttore_richiesta(tenant: uuid.UUID, *, session: Any = None) -> bool:
    """R13b (VD-C-162): parametro RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE dell'ente
    (predefinito NON_RICHIESTA, comportamento di prima)."""
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import (
        AUTORIZZAZIONE_RICHIESTA,
    )
    from libs.verumdoc_rules.parametri_servizio import (
        RIVERSAMENTO_USCITA_AUTORIZZAZIONE_PRODUTTORE as CODICE,
    )

    valori = await parametri([CODICE], str(tenant), session=session)
    return valori.get(CODICE) == AUTORIZZAZIONE_RICHIESTA


async def uscita(identita: Identita, *, tipo: str, ente: str, criteri: Mapping[str, Any] | None,
                 destinatario: str | None, motivo: str, impronta_piano: str | None,
                 idempotency_key: str | None) -> tuple[dict[str, Any], int]:
    """Riversamento in uscita o restituzione. Senza ``impronta_piano``:
    anteprima (200, nessuna scrittura). Con: pratica creata (201) se il piano
    ricalcolato nel lock coincide, altrimenti 409 LC-409-PIANO-CAMBIATO."""
    blocco = motivo_blocco_avvio(tipo, identita, ente)
    if blocco is not None:
        raise ErroreApi(*blocco)
    tenant = pr.uuid_o_404(ente, "LC-404-ENTE", "Ente non trovato")
    try:
        canonici = _criteri(criteri if tipo == st.RIVERSAMENTO_USCITA else None)
    except (ValueError, TypeError) as exc:
        raise ErroreApi("LC-422-CRITERI", str(exc)) from None
    adesso = pr.adesso_utc()
    from libs.shared.db import get_async_session, get_async_session_lettura

    try:
        if impronta_piano is None:
            async with get_async_session_lettura() as session:
                if await repo.ente(session, tenant) is None:
                    raise ErroreApi("LC-404-ENTE", "Ente non trovato")
                piano = await _piano_uscita(session, tipo, tenant, canonici)
            return {**piano, "ente": str(tenant), "criteri": canonici,
                    "conservatore_destinatario": destinatario, "calcolata_il": adesso}, 200
        async with get_async_session() as session:
            if await repo.ente(session, tenant) is None:
                raise ErroreApi("LC-404-ENTE", "Ente non trovato")
            if idempotency_key:
                esistente = await repo.pratica_per_idempotenza(session, tenant, tipo,
                                                               idempotency_key)
                if esistente is not None:
                    if esistente["impronta_piano"] != impronta_piano:
                        raise ErroreApi("LC-409-IDEMPOTENZA",
                                        "Stessa Idempotency-Key con una richiesta diversa")
                    return pr.rappresenta(esistente), 200
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                                  {"k": f"ciclo-vita:{tipo}:{tenant}"})
            stato_iniziale = st.macchina(tipo).iniziale
            # R13b (VD-C-162): fotografia alla creazione dell'opzione dell'ente (§8.3)
            autorizzazione = tipo == st.RIVERSAMENTO_USCITA and \
                await autorizzazione_produttore_richiesta(tenant, session=session)
            pratica_id = await repo.inserisci_pratica(
                session, tenant_id=tenant, tipo=tipo, stato=stato_iniziale, motivo=motivo,
                dati={"criteri": canonici, "conservatore_destinatario": destinatario},
                creata_da=identita.user_id, creata_ruolo=identita.ruolo,
                impronta_piano=impronta_piano, idempotency_key=idempotency_key, adesso=adesso,
                richiede_autorizzazione_produttore=autorizzazione)
            piano = await _piano_uscita(session, tipo, tenant, canonici, pratica_id=pratica_id)
            if piano["impronta_piano"] != impronta_piano:
                raise ErroreApi("LC-409-PIANO-CAMBIATO",
                                "Il piano è cambiato dall'anteprima: rivedere e confermare",
                                dettagli={"errore": "PIANO_CAMBIATO",
                                          "piano": json.loads(json.dumps(piano, default=str))})
            await repo.aggiorna_pratica(session, pratica_id, adesso, conteggi=piano["conteggi"])
            await repo.inserisci_stato(session, pratica_id=pratica_id, tenant_id=tenant,
                                       da_stato=None, a_stato=stato_iniziale,
                                       utente_id=identita.user_id, ruolo=identita.ruolo,
                                       metodo=identita.metodo, motivo=motivo, allegati=(),
                                       adesso=adesso)
            await pr.evento(session, pr.TOPIC_CAMBIO_STATO, str(pratica_id), {
                "pratica_id": str(pratica_id), "tipo": tipo, "da_stato": None,
                "a_stato": stato_iniziale, "versione": 1, "ente": str(tenant),
                "il": adesso.isoformat()}, tenant)
            await registra_audit(session, evento=f"ciclo_vita.{tipo.lower()}.richiesta",
                                 tenant_id=str(tenant), user_id=identita.user_id,
                                 dettagli={"pratica_id": str(pratica_id),
                                           "impronta_piano": impronta_piano,
                                           "conteggi": piano["conteggi"], "motivo": motivo})
            pratica = await repo.leggi_pratica(session, pratica_id)
            assert pratica is not None
    except SQLAlchemyError as exc:
        raise pr.errore_db(f"pratica_{tipo.lower()}", exc) from None
    return pr.rappresenta(pratica), 201


async def _oggetti(pratica_id: uuid.UUID, esito: str, limite: int) -> list[dict[str, Any]]:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        righe = (await session.execute(text(
            "SELECT id, aip_id::text AS aip_id, urn, esito, dettagli FROM public.pratiche_oggetti "
            "WHERE pratica_id = :p AND esito = :e ORDER BY id LIMIT :lim"),
            {"p": pratica_id, "e": esito, "lim": limite})).mappings().all()
    return [dict(r) for r in righe]


async def _segna(oggetto_id: int, esito: str, motivo: str | None, dettagli: Mapping[str, Any],
                 adesso: datetime) -> None:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.pratiche_oggetti SET esito = :e, motivo = :m, "
            "dettagli = dettagli || CAST(:d AS jsonb), aggiornato_il = :adesso, "
            "tentativi = tentativi + 1 WHERE id = :id"),
            {"id": oggetto_id, "e": esito, "m": motivo, "adesso": adesso,
             "d": json.dumps(dict(dettagli), default=str)})


async def passo_uscita(pratica: Mapping[str, Any], esportatore: Esportatore, *,
                       adesso: datetime) -> bool:
    """Un passo dell'esecuzione di un riversamento in uscita o di una restituzione."""
    dati = dict(pratica["dati"] or {})
    destinatario = dati.get("conservatore_destinatario") or "restituzione all'ente"
    ente = str(pratica["tenant_id"])
    # 1. richieste dei PdD MIGRAZIONE a lotti
    selezionati = await _oggetti(pratica["id"], dom.SELEZIONATO,
                                 settings.riversamento_lotto_distribution)
    if selezionati:
        risposta = await esportatore.esporta(
            ente=ente, aip_ids=[o["aip_id"] for o in selezionati], destinatario=destinatario,
            motivo=f"{pratica['tipo'].lower()} {pratica['id']}: {pratica['motivo']}"[:500])
        per_aip = {str(r["aip_id"]): r for r in risposta.get("results") or []}
        errori = {str(e["aip_id"]): e for e in risposta.get("errori") or []}
        for o in selezionati:
            if o["aip_id"] in per_aip:
                await _segna(o["id"], "IN_CORSO", None,
                             {"pdd_id": per_aip[o["aip_id"]].get("pdd_id")}, adesso)
            else:
                errore = errori.get(o["aip_id"], {"errore": "assente dalla risposta"})
                await _segna(o["id"], "ERRORE", "PDD_NON_RICHIESTO", errore, adesso)
        return True
    # 2. stato dei PdD
    in_corso = await _oggetti(pratica["id"], "IN_CORSO", settings.lavoratore_lotto)
    attesa = False
    for o in in_corso:
        pdd_id = str((o["dettagli"] or {}).get("pdd_id"))
        stato = await esportatore.stato(ente=ente, pdd_id=pdd_id)
        if stato.get("stato") == "PRONTO":
            zip_info = stato.get("zip") or {}
            await _segna(o["id"], ESPORTATO, None, {
                "pdd_id": pdd_id, "hash_pdd": stato.get("hash_pdd"),
                "zip_sha256": zip_info.get("hash_sha256"),
                "zip_dimensione": zip_info.get("dimensione_bytes")}, adesso)
        elif stato.get("stato") in STATI_PDD_FINALI_KO:
            await _segna(o["id"], "ERRORE", f"PDD_{stato.get('stato')}",
                         {"pdd_id": pdd_id, "errore": stato.get("errore")}, adesso)
        else:
            attesa = True
    if attesa:
        from libs.shared.db import get_async_session

        async with get_async_session() as session:
            await repo.aggiorna_pratica(
                session, pratica["id"], adesso, prossimo_tentativo=adesso + timedelta(
                    seconds=settings.lavoratore_attesa_esterna_secondi))
        return False
    if in_corso:
        return True
    # 3-4. export dei registri (restituzione), manifesto, conclusione
    await concludi_uscita(pratica, adesso=adesso)
    return False


_SEZIONI_REGISTRI = (
    ("audit", "SELECT id::text, created_at, evento, user_id::text, aip_id::text, dettagli "
              "FROM public.audit_global WHERE tenant_id = :t ORDER BY created_at, id"),
    ("pratiche", "SELECT id::text, tipo, stato, motivo, created_at, conclusa_il "
                 "FROM public.pratiche WHERE tenant_id = :t ORDER BY created_at, id"),
    ("registro", "SELECT r.indice, r.tipo, a.urn, r.aip_id::text AS aip_id "
                 "FROM public.catena_registro r JOIN public.aip a ON a.id = r.aip_id "
                 "WHERE a.tenant_id = :t ORDER BY r.indice"),
)


async def _export_registri(pratica: Mapping[str, Any],
                           adesso: datetime) -> tuple[pr.DocumentoCaricato, dict[str, int]]:
    """JSON Lines dei registri dell'ente (audit, pratiche, foglie del registro
    dei suoi PdA) su file temporaneo, poi nel WORM. Ogni sezione in ordine totale.

    Revisione 3 di R05 (T7): la lettura avviene in una transazione PROPRIA,
    chiusa prima della scrittura nel WORM; la scrittura (l'audit di un ente
    grande) non tiene aperta alcuna transazione. La registrazione dell'allegato
    la fa il chiamante, nella transazione breve che conclude la pratica."""
    from libs.shared.db import get_async_session

    ente = pratica["tenant_id"]
    righe_per_sezione: dict[str, int] = {}
    with tempfile.TemporaryFile() as f:
        async with get_async_session() as session:
            for nome, sql in _SEZIONI_REGISTRI:
                n = 0
                risultato = await session.stream(text(sql), {"t": ente})
                async for riga in risultato.mappings():
                    f.write(json.dumps({"sezione": nome, **dict(riga)}, default=str,
                                       sort_keys=True).encode() + b"\n")
                    n += 1
                righe_per_sezione[nome] = n
        f.seek(0)
        caricato = await pr.carica_documento(
            pratica, tipo=st.ALLEGATO_EXPORT_REGISTRI,
            nome_file=f"registri_{ente}_{pratica['id']}.jsonl",
            content_type="application/x-ndjson", f=f, adesso=adesso)
    return caricato, righe_per_sezione


async def concludi_uscita(pratica: Mapping[str, Any], *, adesso: datetime) -> None:
    """Export dei registri (restituzione), manifesto, conclusione.

    Revisione 3 di R05 (T7): letture e scritture nel WORM FUORI da transazioni
    aperte; poi una transazione breve con la pratica bloccata che ricontrolla lo
    stato, registra gli allegati e applica la transizione (tutto o niente: una
    ripresa rifà export e manifesto)."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"])
    assert attuale is not None
    if attuale["stato"] != st.ESEGUIBILE:
        return
    dati = dict(attuale["dati"] or {})
    prodotti = []
    export: tuple[pr.DocumentoCaricato, dict[str, int]] | None = None
    if attuale["tipo"] == st.RESTITUZIONE:
        export = await _export_registri(attuale, adesso)
        prodotti.append({"tipo": export[0].tipo, "sha256": export[0].scritto.sha256,
                         "righe": export[1]})
    with tempfile.TemporaryFile() as elenco:
        async with get_async_session() as session:
            righe = await session.stream(text(
                "SELECT aip_id::text AS aip_id, urn, esito, motivo, dettagli "
                "FROM public.pratiche_oggetti WHERE pratica_id = :p ORDER BY id"),
                {"p": attuale["id"]})
            async for r in righe.mappings():
                d = r["dettagli"] or {}
                elenco.write(json.dumps({
                    "aip_id": r["aip_id"], "urn": r["urn"], "esito": r["esito"],
                    "motivo": r["motivo"], "pdd_id": d.get("pdd_id"),
                    "zip_sha256": d.get("zip_sha256"), "hash_pdd": d.get("hash_pdd")}
                ).encode() + b"\n")
        elenco.seek(0)
        corpo = manifesto(
            {"id": attuale["id"], "tipo": attuale["tipo"], "ente": attuale["tenant_id"],
             "conservatore_destinatario": dati.get("conservatore_destinatario"),
             "concluso_il": adesso.isoformat()},
            (json.loads(x) for x in elenco), prodotti)
    manifesto_caricato = await pr.carica_documento(
        attuale, tipo=st.ALLEGATO_MANIFESTO, nome_file=f"manifesto_{attuale['id']}.json",
        content_type="application/json", f=io.BytesIO(corpo), adesso=adesso)
    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert attuale is not None
        if attuale["stato"] != st.ESEGUIBILE:
            return
        if export is not None:
            registrato = await pr.registra_documento(session, attuale, export[0])
            prodotti[0]["allegato_id"] = registrato["id"]
        doc = await pr.registra_documento(session, attuale, manifesto_caricato)
        conteggi = await repo.conteggi_oggetti(session, attuale["id"])
        await pr.applica_transizione(session, attuale, st.ESEGUITA, pr.SISTEMA,
                                     motivo="Pacchetti di migrazione pronti", adesso=adesso,
                                     lavoro=None, conteggi=conteggi)
        await pr.evento(session, pr.TOPIC_RIVERSAMENTO_PRONTO, str(attuale["id"]), {
            "pratica_id": str(attuale["id"]), "tipo": attuale["tipo"],
            "ente": str(attuale["tenant_id"]), "conteggi": conteggi,
            "manifesto_sha256": doc["sha256"],
            "manifesto": f"/api/v1/ciclo-vita/pratiche/{attuale['id']}"}, attuale["tenant_id"])
        await registra_audit(session, evento=f"ciclo_vita.{attuale['tipo'].lower()}.eseguita",
                             tenant_id=str(attuale["tenant_id"]),
                             dettagli={"pratica_id": str(attuale["id"]), "conteggi": conteggi,
                                       "manifesto_sha256": doc["sha256"],
                                       "export": prodotti})


# ── Riversamento in ingresso ─────────────────────────────────────────────

async def ingresso(identita: Identita, *, ente: str, caricamento_id: str, classe: str,
                   conservatore_origine: str, metadati: Mapping[str, Any] | None, motivo: str,
                   idempotency_key: str | None) -> tuple[dict[str, Any], bool]:
    blocco = motivo_blocco_avvio(st.RIVERSAMENTO_INGRESSO, identita, ente)
    if blocco is not None:
        raise ErroreApi(*blocco)
    tenant = pr.uuid_o_404(ente, "LC-404-ENTE", "Ente non trovato")
    cid = pr.uuid_o_404(caricamento_id, "LC-422-CARICAMENTO", "Sessione di caricamento non valida")
    adesso = pr.adesso_utc()
    from libs.shared.db import get_async_session

    try:
        async with get_async_session() as session:
            if idempotency_key:
                esistente = await repo.pratica_per_idempotenza(
                    session, tenant, st.RIVERSAMENTO_INGRESSO, idempotency_key)
                if esistente is not None:
                    if (esistente["dati"] or {}).get("caricamento_id") != str(cid):
                        raise ErroreApi("LC-409-IDEMPOTENZA",
                                        "Stessa Idempotency-Key con una richiesta diversa")
                    return pr.rappresenta(esistente), False
            car = (await session.execute(text(
                "SELECT id, stato, bucket, chiave_oggetto, dimensione, impronte, nome_file, "
                "consumato_il FROM public.caricamenti WHERE id = :c AND tenant_id = :t"),
                {"c": cid, "t": tenant})).mappings().first()
            if car is None:
                raise ErroreApi("LC-422-CARICAMENTO", "Sessione di caricamento non trovata "
                                                      "per questo ente")
            if car["stato"] != "COMPLETO" or car["consumato_il"] is not None:
                raise ErroreApi("LC-422-CARICAMENTO",
                                f"La sessione di caricamento deve essere COMPLETA e non usata "
                                f"(stato: {car['stato']})")
            if int(car["dimensione"]) > settings.riversamento_ingresso_max_mb * 1024 * 1024:
                raise ErroreApi("LC-422-CARICAMENTO",
                                f"PdD oltre {settings.riversamento_ingresso_max_mb} MB")
            classe_ok = (await session.execute(text(
                "SELECT 1 FROM public.classi_documentali WHERE codice = :c AND attiva "
                "AND (tenant_id IS NULL OR tenant_id = :t)"), {"c": classe, "t": tenant})).scalar()
            if classe_ok is None:
                raise ErroreApi("LC-422-CRITERI", f"Classe documentale non disponibile: {classe}")
            dati = {"caricamento_id": str(cid), "nome_file": car["nome_file"],
                    "impronta_pdd": (car["impronte"] or {}).get("SHA-256"),
                    "dimensione": int(car["dimensione"]), "classe": classe,
                    "conservatore_origine": conservatore_origine,
                    "metadati": dict(metadati or {}),
                    "origine": {"bucket": car["bucket"], "chiave": car["chiave_oggetto"]}}
            pratica_id = await repo.inserisci_pratica(
                session, tenant_id=tenant, tipo=st.RIVERSAMENTO_INGRESSO, stato=st.PROPOSTA,
                motivo=motivo, dati=dati, creata_da=identita.user_id, creata_ruolo=identita.ruolo,
                idempotency_key=idempotency_key, lavoro=LAVORO_ACQUISISCI, adesso=adesso)
            await repo.inserisci_stato(session, pratica_id=pratica_id, tenant_id=tenant,
                                       da_stato=None, a_stato=st.PROPOSTA,
                                       utente_id=identita.user_id, ruolo=identita.ruolo,
                                       metodo=identita.metodo, motivo=motivo, allegati=(),
                                       adesso=adesso)
            await pr.evento(session, pr.TOPIC_CAMBIO_STATO, str(pratica_id), {
                "pratica_id": str(pratica_id), "tipo": st.RIVERSAMENTO_INGRESSO,
                "da_stato": None, "a_stato": st.PROPOSTA, "versione": 1, "ente": str(tenant),
                "il": adesso.isoformat()}, tenant)
            await registra_audit(session, evento="ciclo_vita.riversamento_ingresso.richiesta",
                                 tenant_id=str(tenant), user_id=identita.user_id,
                                 dettagli={"pratica_id": str(pratica_id),
                                           "caricamento_id": str(cid),
                                           "conservatore_origine": conservatore_origine})
            pratica = await repo.leggi_pratica(session, pratica_id)
            assert pratica is not None
    except SQLAlchemyError as exc:
        raise pr.errore_db("riversamento_ingresso", exc) from None
    return pr.rappresenta(pratica), True


def _membri(archivio: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    return {i.filename: i for i in archivio.infolist() if not i.is_dir()}


def _trova(membri: Mapping[str, zipfile.ZipInfo], nome: str) -> zipfile.ZipInfo | None:
    if nome in membri:
        return membri[nome]
    base = nome.rsplit("/", 1)[-1]
    candidati = [i for n, i in membri.items() if n.rsplit("/", 1)[-1] == base]
    return candidati[0] if len(candidati) == 1 else None


def _sha256_membro(archivio: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    h = hashlib.sha256()
    with archivio.open(info) as f:
        while blocco := f.read(storage.BLOCCO):
            h.update(blocco)
    return h.hexdigest()


def verifica_pdd(lettore: Any, *, dimensione_indice_max: int) -> dict[str, Any]:
    """Verifica sincrona dello ZIP ricevuto: membri sicuri, indice trovato e
    leggibile, impronta di ogni file ricalcolata. Nessun file intero in memoria
    (solo l'indice, entro ``dimensione_indice_max``)."""
    rapporto: dict[str, Any] = {"schema_validato": False, "rilievi": [], "file": [],
                                "firma": {"presente": False, "verificata": False}}
    try:
        archivio = zipfile.ZipFile(lettore)
    except zipfile.BadZipFile:
        rapporto["rilievi"].append("Il PdD ricevuto non è un archivio ZIP leggibile")
        return rapporto
    with archivio:
        membri = _membri(archivio)
        non_sicuri = [n for n in membri if not nome_sicuro(n)]
        if non_sicuri:
            rapporto["rilievi"].append(f"Nomi non sicuri nell'archivio: {non_sicuri[:5]}")
            return rapporto
        # Revisione R08 (X4): membri con rapporto di compressione anomalo (zip bomb)
        anomali = []
        for info in membri.values():
            try:
                controlla_membro(info)
            except MembroZipNonAmmesso as exc:
                anomali.append(str(exc))
        if anomali:
            rapporto["rilievi"].append(f"Membri oltre i limiti di decompressione: {anomali[:5]}")
            return rapporto
        firme = [n for n in membri if n.lower().endswith((".p7m", ".p7s", ".tsr", ".tsd",
                                                          ".p7x"))]
        if firme:
            rapporto["firma"] = {"presente": True, "verificata": False, "file": firme[:20],
                                 "nota": "verifica di firma e marca non disponibile (VD-C-073)"}
        indice = None
        for nome in sorted(n for n in membri if n.lower().endswith(".xml")):
            info = membri[nome]
            if info.file_size > dimensione_indice_max:
                continue
            try:
                dati_indice = leggi_membro(archivio, info, massimo_byte=dimensione_indice_max)
                indice = leggi_indice_pdd(dati_indice, dimensione_massima=dimensione_indice_max)
            except (IndiceNonValido, MembroZipNonAmmesso):
                continue
            rapporto["indice"] = {"nome": nome, "sha256": hashlib.sha256(
                dati_indice).hexdigest(), "radice": indice.radice,
                "identificativo": indice.identificativo, "conservatore": indice.conservatore}
            break
        if indice is None:
            rapporto["rilievi"].append("Nessun indice SInCRO leggibile con file e impronte")
            return rapporto
        rapporto["rilievi"].extend(indice.rilievi)
        rapporto["metadati"] = indice.metadati
        for voce in indice.file:
            info = _trova(membri, voce.nome)
            if info is None:
                rapporto["file"].append({"nome": voce.nome, "esito": "ASSENTE"})
                continue
            if voce.algoritmo == "SHA-256":
                calcolata = _sha256_membro(archivio, info)
            else:
                h = hashlib.sha512()
                with archivio.open(info) as f:
                    while blocco := f.read(storage.BLOCCO):
                        h.update(blocco)
                calcolata = h.hexdigest()
            esito = "OK" if calcolata == voce.impronta else "IMPRONTA_DIVERSA"
            sha256 = calcolata if voce.algoritmo == "SHA-256" else _sha256_membro(archivio, info)
            rapporto["file"].append({"nome": voce.nome, "membro": info.filename,
                                     "esito": esito, "algoritmo": voce.algoritmo,
                                     "sha256": sha256, "dimensione": info.file_size,
                                     "tipo": voce.tipo, "formato": voce.formato})
    return rapporto


async def passo_acquisizione(pratica: Mapping[str, Any], *, adesso: datetime) -> bool:
    """Copia nel WORM del PdD ricevuto e verifica dell'indice e delle impronte."""
    from libs.shared.db import get_async_session

    dati = dict(pratica["dati"] or {})
    s3 = storage.client()
    bucket = storage.bucket_worm()
    sha = dati.get("impronta_pdd") or "0" * 64
    chiave = pr.chiave_documento(pratica["tenant_id"], pratica["id"], st.ALLEGATO_PDD_RICEVUTO,
                                 dati.get("nome_file") or "pdd.zip", sha)
    from app.application.parametri import anni_retention

    fino = aggiungi_anni(adesso, await anni_retention(pratica["tenant_id"]))
    versione = await asyncio.to_thread(
        storage.copia_in_worm, s3, dati["origine"]["bucket"], dati["origine"]["chiave"], bucket,
        chiave, conserva_fino_a=fino, content_type="application/zip")
    lettore = storage.LettoreS3(s3, bucket, chiave, version_id=versione)
    rapporto = await asyncio.to_thread(
        verifica_pdd, lettore,
        dimensione_indice_max=settings.riversamento_ingresso_indice_max_mb * 1024 * 1024)
    ok = [f for f in rapporto["file"] if f["esito"] == "OK"]
    bloccante = bool(rapporto["rilievi"]) or not ok or len(ok) != len(rapporto["file"])
    dati["verifica"] = rapporto
    dati["pdd_worm"] = {"chiave": chiave, "version_id": versione}
    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert attuale is not None
        await repo.inserisci_allegato(
            session, pratica_id=attuale["id"], tenant_id=attuale["tenant_id"],
            tipo=st.ALLEGATO_PDD_RICEVUTO, nome_file=(dati.get("nome_file") or "pdd.zip")[:255],
            content_type="application/zip", dimensione=int(dati.get("dimensione") or 0),
            sha256=sha, bucket=bucket, chiave=chiave, version_id=versione,
            conserva_fino_a=fino, firma=rapporto["firma"], caricato_da=None)
        await session.execute(text(
            "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, urn, esito, motivo, "
            "dettagli) VALUES (:p, :t, :urn, :esito, :motivo, CAST(:d AS jsonb))"),
            [{"p": attuale["id"], "t": attuale["tenant_id"], "urn": f["nome"][:500],
              "esito": dom.SELEZIONATO if f["esito"] == "OK" else dom.BLOCCATO,
              "motivo": None if f["esito"] == "OK" else f["esito"],
              "d": json.dumps(f, default=str)} for f in rapporto["file"]] or
            [{"p": attuale["id"], "t": attuale["tenant_id"], "urn": "indice", "esito": "BLOCCATO",
              "motivo": "INDICE_NON_VALIDO", "d": json.dumps({"rilievi": rapporto["rilievi"]})}])
        if bloccante:
            await pr.applica_transizione(
                session, attuale, st.RESPINTA, pr.SISTEMA,
                motivo="Verifica del PdD ricevuto non superata: "
                       + "; ".join(rapporto["rilievi"][:3] or ["impronte non corrispondenti"]),
                adesso=adesso, lavoro=None, dati=dati)
        else:
            await repo.aggiorna_pratica(session, attuale["id"], adesso, dati=dati, lavoro=None,
                                        ultimo_errore=None)
        await registra_audit(session, evento="ciclo_vita.riversamento_ingresso.verifica",
                             tenant_id=str(attuale["tenant_id"]),
                             dettagli={"pratica_id": str(attuale["id"]),
                                       "file_ok": len(ok), "file": len(rapporto["file"]),
                                       "rilievi": rapporto["rilievi"][:10]})
    return False


async def _blocchi_membro(lettore_factory: Any, membro: str, sha_atteso: str
                          ) -> AsyncIterator[bytes]:
    """Blocchi di un membro dello ZIP nel WORM, con impronta ricontrollata alla fine."""
    def apri() -> tuple[zipfile.ZipFile, Any]:
        archivio = zipfile.ZipFile(lettore_factory())
        controlla_membro(archivio.getinfo(membro))  # Y1: solo STORED/DEFLATED
        return archivio, archivio.open(membro)

    archivio, f = await asyncio.to_thread(apri)
    h = hashlib.sha256()
    try:
        while blocco := await asyncio.to_thread(f.read, storage.BLOCCO):
            h.update(blocco)
            yield blocco
    finally:
        await asyncio.to_thread(f.close)
        await asyncio.to_thread(archivio.close)
    if h.hexdigest() != sha_atteso:
        raise ErroreGateway(f"impronta del file {membro} cambiata durante l'invio",
                            status=None, problema={"codice": "LC-422-IMPRONTA"},
                            ritentabile=False)


async def passo_ingresso(pratica: Mapping[str, Any], versatore: Versatore, *,
                         adesso: datetime) -> bool:
    from libs.shared.db import get_async_session

    dati = dict(pratica["dati"] or {})
    conto = await per_conto_di(pratica)
    if conto is None:
        # Revisione R05 (R8): senza un utente che può versare nulla si versa e la
        # pratica NON si dichiara eseguita
        await _concludi_ingresso(pratica, None, "Utente richiedente non più attivo nell'ente: "
                                 "nessun documento versato", adesso, stato=st.RESPINTA)
        return False
    s3 = storage.client()
    bucket = storage.bucket_worm()
    worm = dati["pdd_worm"]

    def lettore() -> storage.LettoreS3:
        return storage.LettoreS3(s3, bucket, worm["chiave"], version_id=worm.get("version_id"))

    da_caricare = await _oggetti(pratica["id"], dom.SELEZIONATO, settings.lavoratore_lotto)
    for o in da_caricare:
        await lease.rinnova_nel_passo()  # R11: caricamenti lunghi
        f = o["dettagli"]
        caricamento = await versatore.carica(
            conto, nome_file=f["nome"].rsplit("/", 1)[-1][:255], dimensione=int(f["dimensione"]),
            sha256=f["sha256"], blocchi=lambda f=f: _blocchi_membro(lettore, f["membro"],
                                                                     f["sha256"]),
            chiave=f"cv-{pratica['id']}-{o['id']}")
        await _segna(o["id"], "IN_CORSO", None, {"caricamento_id": caricamento}, adesso)
    if da_caricare:
        return True
    caricati = await _oggetti(pratica["id"], "IN_CORSO", 10_000)
    if not caricati:
        await _concludi_ingresso(pratica, None, "Nessun file da versare", adesso,
                                 stato=st.RESPINTA)
        return False
    verifica = dati.get("verifica") or {}
    origine = {
        "riversamento_conservatore_origine": dati.get("conservatore_origine"),
        "riversamento_idc_origine": (verifica.get("indice") or {}).get("identificativo")
        or (verifica.get("indice") or {}).get("nome"),
        "riversamento_impronta_idc": (verifica.get("indice") or {}).get("sha256"),
        "riversamento_impronta_pdd": dati.get("impronta_pdd"),
        "riversamento_pratica": str(pratica["id"]),
    }
    documenti = []
    for i, o in enumerate(caricati):
        f = o["dettagli"]
        principale = i == 0 if not any((x["dettagli"] or {}).get("tipo") == "PRINCIPALE"
                                       for x in caricati) else f.get("tipo") == "PRINCIPALE"
        doc: dict[str, Any] = {
            "id": str(uuid.uuid5(NAMESPACE_DOCUMENTI, f"{pratica['id']}:{f['nome']}")),
            "tipo": "PRINCIPALE" if principale else "ALLEGATO",
            "nome_file": f["nome"].rsplit("/", 1)[-1][:255], "formato": f.get("formato"),
            "hash": {"impronta": f["sha256"], "algoritmo": "SHA-256"},
        }
        if principale:
            doc["parametri"] = {**(verifica.get("metadati") or {}), **(dati.get("metadati") or {}),
                                **origine}
        documenti.append(doc)
    indice = {"classe_documentale": dati["classe"],
              "chiave": {"registro": "RIVERSAMENTO", "anno": str(pratica["created_at"].year),
                         "numero": str(pratica["id"])},
              "documenti": documenti}
    file = {d["nome_file"]: (o["dettagli"] or {}).get("caricamento_id")
            for d, o in zip(documenti, caricati, strict=True)}
    try:
        esito = await versatore.versa(conto, indice=indice, file=file,
                                      chiave=f"cv-{pratica['id']}-versamento")
    except ErroreGateway as exc:
        if exc.ritentabile:
            raise
        await _concludi_ingresso(pratica, None, f"Versamento rifiutato dal gateway: "
                                                f"{exc.problema.get('codice')}", adesso,
                                 errore=exc.problema, stato=st.RESPINTA)
        return False
    pdv_id = uuid.UUID(str(esito["pdv_id"]))
    async with get_async_session() as session:
        await session.execute(text(
            "UPDATE public.pratiche_oggetti SET esito = 'VERSATO', pdv_id = :pdv, "
            "aggiornato_il = :adesso WHERE pratica_id = :p AND esito = 'IN_CORSO'"),
            {"pdv": pdv_id, "p": pratica["id"], "adesso": adesso})
    await _concludi_ingresso(pratica, pdv_id, f"Documenti versati nel PdV {pdv_id}", adesso)
    return False


async def _concludi_ingresso(pratica: Mapping[str, Any], pdv_id: uuid.UUID | None, motivo: str,
                             adesso: datetime, *, errore: Mapping[str, Any] | None = None,
                             stato: str = st.ESEGUITA) -> None:
    """Chiude il riversamento in ingresso. Revisione R05 (R8): ESEGUITA SOLO con
    il versamento avvenuto (``pdv_id``); senza, RESPINTA con il motivo e i file
    non versati in ERRORE (mai una pratica eseguita che non ha versato nulla)."""
    from libs.shared.db import get_async_session

    if stato == st.ESEGUITA and pdv_id is None:
        raise RuntimeError("riversamento in ingresso ESEGUITO senza versamento")

    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert attuale is not None
        if attuale["stato"] != st.ESEGUIBILE:
            return
        dati = dict(attuale["dati"] or {})
        if errore:
            dati["errore"] = dict(errore)
        if pdv_id is not None:
            await repo.aggiorna_pratica(session, attuale["id"], adesso, pdv_risultato_id=pdv_id)
        else:
            await session.execute(text(
                "UPDATE public.pratiche_oggetti SET esito = 'ERRORE', motivo = 'NON_VERSATO', "
                "aggiornato_il = :adesso WHERE pratica_id = :p "
                "AND esito IN ('SELEZIONATO', 'IN_CORSO')"), {"p": attuale["id"], "adesso": adesso})
        conteggi = await repo.conteggi_oggetti(session, attuale["id"])
        await pr.applica_transizione(session, attuale, stato, pr.SISTEMA, motivo=motivo,
                                     adesso=adesso, lavoro=None, dati=dati, conteggi=conteggi)
        evento_audit = ("ciclo_vita.riversamento_ingresso.eseguito" if stato == st.ESEGUITA
                        else "ciclo_vita.riversamento_ingresso.respinto")
        await registra_audit(session, evento=evento_audit,
                             tenant_id=str(attuale["tenant_id"]),
                             dettagli={"pratica_id": str(attuale["id"]),
                                       "pdv_id": str(pdv_id) if pdv_id else None,
                                       "conteggi": conteggi})


__all__ = ["ESPORTATO", "LAVORO_ACQUISISCI", "concludi_uscita", "ingresso", "passo_acquisizione",
           "passo_ingresso", "passo_uscita", "uscita", "verifica_pdd"]
