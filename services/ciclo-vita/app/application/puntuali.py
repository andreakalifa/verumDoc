# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/puntuali.py — pratiche su un singolo versamento: annullamento e
rettifica dei metadati.

Riferimenti: contratto Q2 di R05; REGOLE_SVILUPPO §10.1 (ciò che è conservato
non si modifica: la rettifica è un PdV NUOVO collegato all'originale), §10.5
(si annulla con un evento, mai con DELETE), §12.11 (effetti esterni dopo il
commit: versamento sul gateway e foglia nel registro li fa il lavoratore),
§14.4, §15.

ANNULLAMENTO: la richiesta (una transazione) crea la pratica RICHIESTA con
l'audit; revisione R09 (Z3) l'annullamento LOGICO (PdV → ANNULLATO, nessun file
toccato, pratica → ESEGUITA, evento ``versamento.annullato``) lo applica SOLO il
lavoratore dopo aver riverificato la catena e la regola dell'ente (lavoro
ANNULLA): un blocco lascia il PdV intatto. Ammesso sui PdV RICEVUTO, ERRORE e —
secondo la regola configurabile — CONSERVATO; un PdV in lavorazione
(IN_VALIDAZIONE, VALIDATO, IN_BUILD) risponde 409 (la pipeline lo sta portando
avanti: riprovare a lavorazione conclusa).

R09 (D23): la regola è per ente (parametri ANNULLAMENTO_*: ruoli prima e dopo la
conservazione, doppia approvazione). Con la doppia approvazione la richiesta
resta RICHIESTA (PdV invariato) finché una SECONDA persona dell'ente con un ruolo
ammesso la approva (``decidi_approvazione``: RICHIESTA → APPROVATA, lavoro
ANNULLA) o la respinge (→ RESPINTA: si può chiedere di nuovo). Il lavoratore
riverifica i quattro occhi prima di applicare. Revisione R09 (Z1, VD-P-90): con
la doppia approvazione la richiesta è di una persona (mai una chiave API) e
approva un ADMIN_TENANT con un account nato prima della richiesta. Se la regola
è diventata più severa prima che il lavoratore applichi, la pratica torna in
attesa della seconda persona (PdV intatto).
Il lavoratore poi archivia l'atto di annullamento nel WORM e, se il PdA è nel
registro, aggiunge la foglia «annullamento» (contratto Q3).

RETTIFICA: pratica RICHIESTA; il lavoratore archivia l'atto di rettifica nel
WORM, lo versa con il gateway come documento principale di un PdV NUOVO della
stessa classe (metadati = quelli del principale originale aggiornati con
``metadati_nuovi`` + riferimenti all'originale) → IN_VERSAMENTO; quando il PdV
di rettifica è CONSERVATO aggiunge la foglia «rettifica» → COMPLETATA ed
evento ``versamento.rettificato``; se il PdV di rettifica è rifiutato → RESPINTA.
"""
from __future__ import annotations

import io
import json
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.application import avvisi, conferme, parametri
from app.application import pratiche as pr
from app.config import settings
from app.domain import autorizzazioni as dom_aut
from app.domain import stati as st
from app.domain.documenti import atto
from app.domain.permessi import (
    Identita,
    motivo_blocco_annullamento,
    motivo_blocco_approvazione,
    motivo_blocco_rettifica,
    motivo_blocco_richiesta_doppia,
)
from app.infrastructure import repository as repo
from app.infrastructure import storage
from app.infrastructure.gateway import ErroreGateway, Versatore
from app.infrastructure.registro import RegistroImpronte
from libs.shared.errori import ErroreApi, errore_registrato
from libs.shared.security.audit import registra_audit

logger = structlog.get_logger(__name__)

STATI_ANNULLABILI = ("RICEVUTO", "ERRORE", "CONSERVATO")
STATI_IN_LAVORAZIONE = ("IN_VALIDAZIONE", "VALIDATO", "IN_BUILD")
LAVORO_REGISTRO = "REGISTRO"
LAVORO_VERSA = "VERSA"
LAVORO_ATTENDI = "ATTENDI"
_CHIAVE_METADATO = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
#: Metadati della rettifica impostati dal servizio (non dal richiedente)
METADATI_RISERVATI = frozenset({"rettifica_di", "pdv_rettificato", "pratica_rettifica",
                                "identificativo_documento"})
NAMESPACE_DOCUMENTI = uuid.UUID("6c1f3c62-6a59-4d2c-9a2b-5e0b8d2f7a11")


async def _pdv_dell_ente(session: Any, identita: Identita, pdv_id: uuid.UUID,
                         per_aggiornare: bool = True) -> dict[str, Any]:
    sql = ("SELECT p.id, p.tenant_id, p.stato::text AS stato, p.metadata_json, "
           "p.classe_doc_id, c.codice AS classe FROM public.pdv p "
           "JOIN public.classi_documentali c ON c.id = p.classe_doc_id "
           "WHERE p.id = :id AND p.tenant_id = CAST(:t AS uuid)")
    if per_aggiornare:
        sql += " FOR UPDATE OF p"
    riga = (await session.execute(text(sql), {"id": pdv_id, "t": identita.tenant_id}
                                  )).mappings().first()
    if riga is None:
        raise ErroreApi("LC-404-PDV", "Versamento non trovato per questo ente")
    return dict(riga)


async def _pda_del_pdv(session: Any, pdv_id: uuid.UUID) -> dict[str, Any] | None:
    riga = (await session.execute(text(
        "SELECT id, urn, stato::text AS stato FROM public.aip WHERE pdv_id = :p "
        "ORDER BY created_at DESC LIMIT 1"), {"p": pdv_id})).mappings().first()
    return dict(riga) if riga else None


def _chiave_idempotenza(valore: str | None) -> str | None:
    if valore is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", valore):
        raise ErroreApi("IDEM-400", "Idempotency-Key: 8-200 caratteri [A-Za-z0-9._:-]")
    return valore


# ── Annullamento ─────────────────────────────────────────────────────────

async def richiesta_firmata(identita: Identita, pdv_id: uuid.UUID,
                            documento: Mapping[str, Any] | None
                            ) -> tuple[bytes, str, dict[str, Any]] | None:
    """R08 (D22): richiesta di annullamento firmata, verificata PRIMA della
    transazione (livello e obbligo dell'ente). Il lavoratore la riverifica dal WORM."""
    import base64
    import binascii

    from app.application import firme
    from libs.shared.firme import ErroreVerificatoreNonDisponibile

    parametri = await firme.parametri_firme(identita.tenant_id)
    if documento is None:
        if parametri.annullamento_obbligatoria:
            raise ErroreApi("LC-422-RICHIESTA-FIRMATA",
                            "L'ente richiede la richiesta di annullamento firmata "
                            "(campo documento_firmato)")
        return None
    try:
        contenuto = base64.b64decode(str(documento["contenuto"]), validate=True)
    except (binascii.Error, ValueError):
        raise ErroreApi("LC-422-RICHIESTA-FIRMATA",
                        "documento_firmato.contenuto non è base64 valido") from None
    if len(contenuto) > 10 * 1024 * 1024:
        raise ErroreApi("LC-413-ALLEGATO", "Documento firmato oltre 10 MB")
    nome = str(documento["nome_file"])
    try:
        esito = await firme.verifica_documento(contenuto, nome, parametri.livello)
    except ErroreVerificatoreNonDisponibile:
        raise ErroreApi("LC-503-FIRME", "Verifica delle firme non disponibile: riprovare") \
            from None
    problemi, _ = firme.problemi_documento(st.ALLEGATO_RICHIESTA_ANNULLAMENTO, esito, parametri)
    if problemi:
        raise ErroreApi("LC-422-FIRMA", "Firma della richiesta non accettabile: "
                        + "; ".join(problemi)[:500],
                        dettagli={"firma": {k: esito.get(k) for k in (
                            "formato", "indicazione", "in_parole", "firme")}})
    if not firme.contiene_riferimento(contenuto, str(pdv_id),
                                      ammessi=parametri.firmatari_autorizzati):
        raise ErroreApi("LC-422-RICHIESTA-FIRMATA",
                        "Il documento firmato non riporta l'identificativo del versamento")
    esito["presente"] = True
    return contenuto, nome, esito


async def annulla(identita: Identita, *, pdv_id: str, motivo: str,
                  idempotency_key: str | None,
                  documento_firmato: Mapping[str, Any] | None = None,
                  conferma: Mapping[str, Any] | None = None
                  ) -> tuple[dict[str, Any], bool]:
    pid = pr.uuid_o_404(pdv_id, "LC-404-PDV", "Versamento non trovato per questo ente")
    chiave = _chiave_idempotenza(idempotency_key)
    adesso = pr.adesso_utc()
    from libs.shared.db import get_async_session

    if identita.conservatore:
        # contratto Q2: l'annullamento è dell'ente (non del conservatore)
        raise ErroreApi("LC-403-RUOLO", "L'annullamento lo chiede l'ente produttore")
    firmata = await richiesta_firmata(identita, pid, documento_firmato)

    try:
        async with get_async_session() as session:
            if identita.conservatore:
                # contratto Q2: l'annullamento è dell'ente (non del conservatore)
                raise ErroreApi("LC-403-RUOLO", "L'annullamento lo chiede l'ente produttore")
            pdv = await _pdv_dell_ente(session, identita, pid)
            # R09 (D23): un annullamento RESPINTO non impedisce una nuova richiesta
            gia = (await session.execute(text(
                f"SELECT {repo.COLONNE_PRATICA} FROM public.pratiche p "  # noqa: S608
                "WHERE p.tipo = 'ANNULLAMENTO' AND p.pdv_id = :p AND p.stato <> 'RESPINTA'"),
                {"p": pid})).mappings().first()
            if gia is not None:
                if chiave and gia["idempotency_key"] == chiave:
                    return pr.rappresenta(dict(gia)), False
                raise ErroreApi("LC-409-ANNULLATO",
                                "Il versamento è già annullato" if gia["stato"] != st.RICHIESTA
                                else "Il versamento ha già un annullamento in attesa di "
                                     "approvazione", dettagli={"pratica_id": str(gia["id"])})
            if pdv["stato"] in STATI_IN_LAVORAZIONE:
                raise ErroreApi("LC-409-PDV-STATO",
                                f"Versamento in lavorazione ({pdv['stato']}): riprovare quando "
                                "la verifica o la conservazione è conclusa",
                                dettagli={"stato": pdv["stato"]})
            if pdv["stato"] not in STATI_ANNULLABILI:
                raise ErroreApi("LC-409-PDV-STATO",
                                f"Un versamento in stato {pdv['stato']} non si annulla",
                                dettagli={"stato": pdv["stato"]})
            conservato = pdv["stato"] == "CONSERVATO"
            # Revisione R09 (Z3): regola letta senza cache (come il lavoratore)
            regola = await parametri.regola_annullamento(identita.tenant_id, session=session,
                                                         rigoroso=True)
            blocco = motivo_blocco_annullamento(
                identita, conservato=conservato, ruoli_prima=regola.ruoli_prima,
                ruoli_dopo=regola.ruoli_dopo)
            if blocco is not None:
                raise ErroreApi(*blocco)
            doppia = regola.doppia_richiesta(conservato=conservato)
            if doppia and (blocco := motivo_blocco_richiesta_doppia(identita)) is not None:
                raise ErroreApi(*blocco)
            pda = await _pda_del_pdv(session, pid) if conservato else None
            dati = {"stato_precedente": pdv["stato"],
                    "aip_id": str(pda["id"]) if pda else None, "urn": pda["urn"] if pda else None,
                    "approvazione": {"richiesta": doppia, "regola": regola.come_dict()}}
            tenant = uuid.UUID(identita.tenant_id)
            pratica_id = await repo.inserisci_pratica(
                session, tenant_id=tenant, tipo=st.ANNULLAMENTO, stato=st.RICHIESTA,
                motivo=motivo, dati=dati, creata_da=identita.user_id,
                creata_ruolo=identita.ruolo, pdv_id=pid, idempotency_key=chiave,
                lavoro=None if doppia else st.LAVORO_ANNULLA, adesso=adesso)
            await repo.inserisci_stato(session, pratica_id=pratica_id, tenant_id=tenant,
                                       da_stato=None, a_stato=st.RICHIESTA,
                                       utente_id=identita.user_id, ruolo=identita.ruolo,
                                       metodo=identita.metodo, motivo=motivo, allegati=(),
                                       adesso=adesso)
            pratica = await repo.leggi_pratica(session, pratica_id)
            assert pratica is not None
            # R17 (VD-P-164): la richiesta è una decisione sensibile (conferma del gateway)
            await conferme.registra(session, identita, azione=conferme.cd.ANNULLAMENTO_RICHIEDI,
                                    pratica=pratica, conferma=conferma,
                                    campi={"pdv_id": str(pid), "motivo": motivo},
                                    versione_attesa=0, adesso=adesso)
            if firmata is not None:
                contenuto, nome, esito_firma = firmata
                await pr.archivia_documento(
                    session, pratica, tipo=st.ALLEGATO_RICHIESTA_ANNULLAMENTO, nome_file=nome,
                    content_type="application/octet-stream", f=io.BytesIO(contenuto),
                    adesso=adesso, caricato_da=identita.user_id, firma=esito_firma)
            # PdV invariato: con la doppia approvazione in attesa della seconda
            # persona, altrimenti del lavoratore (revisione R09, Z3)
            await pr.evento(session, pr.TOPIC_CAMBIO_STATO, str(pratica_id), {
                "pratica_id": str(pratica_id), "tipo": st.ANNULLAMENTO, "da_stato": None,
                "a_stato": st.RICHIESTA, "versione": 1, "ente": str(tenant),
                "il": adesso.isoformat()}, tenant)
            if doppia:
                await avvisi.avvisa(session, tenant, avvisi.ANNULLAMENTO_DA_APPROVARE,
                                    str(pratica_id), {"pratica_id": str(pratica_id),
                                                      "pdv_id": str(pid),
                                                      "stato_versamento": pdv["stato"]})
            await registra_audit(session, evento="ciclo_vita.annullamento",
                                 tenant_id=str(tenant), user_id=identita.user_id,
                                 aip_id=dati["aip_id"],
                                 dettagli={"pratica_id": str(pratica_id), "pdv_id": str(pid),
                                           "stato_precedente": pdv["stato"], "motivo": motivo,
                                           "ruolo": identita.ruolo,
                                           "richiesta_firmata": firmata is not None,
                                           "doppia_approvazione": doppia})
    except IntegrityError:
        raise ErroreApi("LC-409-ANNULLATO", "Il versamento è già annullato") from None
    except SQLAlchemyError as exc:
        raise pr.errore_db("annullamento", exc) from None
    except storage.ErroreStorageConservativo as exc:
        raise errore_registrato("LC-503-STORAGE", exc, evento="richiesta_firmata_worm") \
            from None
    return pr.rappresenta(pratica), True


async def annulla_logicamente(session: Any, pratica: Mapping[str, Any], stato_atteso: str,
                              adesso: datetime) -> dict[str, Any]:
    """Annullamento LOGICO, applicato SOLO dal lavoratore dopo la riverifica
    (revisione R09, Z3): solo lo stato del PdV (nessun file, nessun metadato
    tolto), pratica → ESEGUITA con il lavoro REGISTRO (atto, fatto, foglia),
    evento ``versamento.annullato``. Il PdV deve essere ancora nello stato atteso
    (409 se cambiato)."""
    pid = pratica["pdv_id"]
    r = await session.execute(text(
        "UPDATE public.pdv SET stato = 'ANNULLATO', data_aggiornamento = :adesso, "
        "metadata_json = COALESCE(metadata_json, '{}'::jsonb) || jsonb_build_object("
        "'annullamento', CAST(:info AS jsonb)) WHERE id = :p AND stato = :atteso"),
        {"p": pid, "adesso": adesso, "atteso": stato_atteso,
         "info": _json({"pratica_id": str(pratica["id"]), "il": adesso.isoformat(),
                        "stato_precedente": stato_atteso})})
    if r.rowcount != 1:
        raise ErroreApi("LC-409-PDV-STATO", "Lo stato del versamento è cambiato: riprovare",
                        dettagli={"stato_atteso": stato_atteso})
    aggiornata = await pr.applica_transizione(
        session, pratica, st.ESEGUITA, pr.SISTEMA, motivo="Annullamento logico applicato",
        adesso=adesso, lavoro=LAVORO_REGISTRO)
    dati = dict(pratica.get("dati") or {})
    await pr.evento(session, pr.TOPIC_ANNULLATO, str(pid), {
        "pdv_id": str(pid), "pratica_id": str(pratica["id"]), "ente": str(pratica["tenant_id"]),
        "stato_precedente": stato_atteso, "urn": dati.get("urn"),
        "annullato_il": adesso.isoformat()}, pratica["tenant_id"])
    return aggiornata


def annullamento_bloccato(pratica: Mapping[str, Any]) -> bool:
    """Revisione R09 (W1): annullamento non applicato e BLOCCATO dal lavoratore
    (difetti da manomissione): PdV intatto, si può solo respingere."""
    from app.domain.autorizzazioni import CODICE_BLOCCO

    errore = pratica.get("ultimo_errore") or {}
    if isinstance(errore, str):
        errore = json.loads(errore)
    return (pratica["tipo"] == st.ANNULLAMENTO
            and pratica["stato"] in dom_aut.STATI_APPLICA_ANNULLAMENTO
            and pratica.get("lavoro") is None and errore.get("codice") == CODICE_BLOCCO)


async def _respingi_bloccata(session: Any, identita: Identita, pratica: Mapping[str, Any], *,
                             approva: bool, motivo: str, adesso: datetime) -> dict[str, Any]:
    """L'uscita di un annullamento bloccato: lo respinge una persona
    (ADMIN_TENANT) dell'ente o il conservatore; approvarlo non si può."""
    from app.domain.permessi import persona

    if approva:
        raise ErroreApi("LC-409-APPROVAZIONE",
                        "L'annullamento è fermo perché il ricontrollo del sistema non è riuscito: "
                        "si può solo respingere e chiedere di nuovo",
                        dettagli={"stato": pratica["stato"]})
    if not (identita.conservatore or (
            persona(identita) and identita.ruolo == st.ADMIN_TENANT
            and str(identita.tenant_id) == str(pratica["tenant_id"]))):
        raise ErroreApi("LC-403-RUOLO", "Un annullamento fermo lo respinge un amministratore "
                                        "dell'ente o il conservatore")
    aggiornata = await pr.applica_transizione(
        session, pratica, st.RESPINTA, pr.Attore.da_identita(identita), motivo=motivo,
        adesso=adesso, lavoro=None)
    await registra_audit(session, evento="ciclo_vita.annullamento.respinto",
                         tenant_id=str(pratica["tenant_id"]), user_id=identita.user_id,
                         dettagli={"pratica_id": str(pratica["id"]), "motivo": motivo,
                                   "ruolo": identita.ruolo, "era_bloccata": True})
    return aggiornata


async def decidi_approvazione(identita: Identita, pratica_id: str, *, approva: bool,
                              motivo: str, conferma: Mapping[str, Any] | None = None
                              ) -> dict[str, Any]:
    """R09 (D23): seconda persona che approva o respinge un annullamento in attesa.

    Quattro occhi VERI (``motivo_blocco_approvazione``): persona dell'ente con un
    ruolo ammesso ADESSO dai parametri dell'ente, diversa dal richiedente. In una
    transazione con la pratica bloccata: RICHIESTA → APPROVATA (con l'utente che
    approva) e annullamento logico → ESEGUITA, oppure RICHIESTA → RESPINTA."""
    pid = pr.uuid_o_404(pratica_id)
    adesso = pr.adesso_utc()
    from app.domain.permessi import puo_leggere
    from libs.shared.db import get_async_session

    try:
        async with get_async_session() as session:
            pratica = await repo.leggi_pratica(session, pid, per_aggiornare=True)
            if pratica is None or not puo_leggere(identita, str(pratica["tenant_id"])):
                raise pr.non_trovata()
            dati = dict(pratica["dati"] or {})
            if annullamento_bloccato(pratica):
                return pr.rappresenta(await _respingi_bloccata(session, identita, pratica,
                                                               approva=approva, motivo=motivo,
                                                               adesso=adesso))
            if pratica["tipo"] != st.ANNULLAMENTO or pratica["stato"] != st.RICHIESTA \
                    or not (dati.get("approvazione") or {}).get("richiesta"):
                raise ErroreApi("LC-409-APPROVAZIONE",
                                "La pratica non è un annullamento in attesa di approvazione",
                                dettagli={"stato": pratica["stato"], "tipo": pratica["tipo"]})
            regola = await parametri.regola_annullamento(pratica["tenant_id"], session=session,
                                                         rigoroso=True)
            creato_il = await pr.utente_creato_il(session, identita, pratica["tenant_id"])
            blocco = motivo_blocco_approvazione(
                identita, tenant_pratica=str(pratica["tenant_id"]),
                richiedente=pratica.get("creata_da") and str(pratica["creata_da"]),
                ruoli_approvazione=regola.ruoli_approvazione,
                richiesta_ruolo=pratica.get("creata_ruolo"), approva=approva,
                approvatore_creato_il=creato_il, pratica_creata_il=pratica["created_at"])
            if blocco is not None:
                raise ErroreApi(*blocco)
            attore = pr.Attore.da_identita(identita)
            if not approva:
                aggiornata = await pr.applica_transizione(
                    session, pratica, st.RESPINTA, attore, motivo=motivo, adesso=adesso)
                await registra_audit(session, evento="ciclo_vita.annullamento.respinto",
                                     tenant_id=str(pratica["tenant_id"]),
                                     user_id=identita.user_id,
                                     dettagli={"pratica_id": str(pid), "motivo": motivo,
                                               "ruolo": identita.ruolo})
                return pr.rappresenta(aggiornata)
            pdv = await _pdv_dell_ente(session, identita, pratica["pdv_id"])
            if pdv["stato"] != dati.get("stato_precedente"):
                raise ErroreApi("LC-409-PDV-STATO",
                                f"Il versamento è cambiato dalla richiesta (ora {pdv['stato']}): "
                                "respingere e chiedere di nuovo l'annullamento",
                                dettagli={"stato": pdv["stato"],
                                          "stato_richiesta": dati.get("stato_precedente")})
            # R17 (VD-P-164): l'approvazione è una decisione sensibile (conferma del gateway)
            await conferme.registra(session, identita, azione=conferme.cd.ANNULLAMENTO_APPROVA,
                                    pratica=pratica, conferma=conferma,
                                    campi={"pdv_id": str(pratica["pdv_id"]),
                                           "motivo": pratica["motivo"]},
                                    versione_attesa=int(pratica["versione"]), adesso=adesso)
            # Revisione R09 (Z3): l'annullamento logico lo applica il lavoratore
            aggiornata = await pr.applica_transizione(
                session, pratica, st.APPROVATA, attore, motivo=motivo, adesso=adesso,
                lavoro=st.LAVORO_ANNULLA)
            await registra_audit(session, evento="ciclo_vita.annullamento.approvato",
                                 tenant_id=str(pratica["tenant_id"]), user_id=identita.user_id,
                                 aip_id=dati.get("aip_id"),
                                 dettagli={"pratica_id": str(pid), "motivo": motivo,
                                           "ruolo": identita.ruolo,
                                           "richiesta_da": str(pratica.get("creata_da"))})
    except SQLAlchemyError as exc:
        raise pr.errore_db("approvazione_annullamento", exc) from None
    return pr.rappresenta(aggiornata)


def _json(valore: Any) -> str:
    import json

    return json.dumps(valore, default=str, sort_keys=True)


def campi_atto(pratica: Mapping[str, Any]) -> dict[str, Any]:
    """Campi dell'atto (deterministici: dalla pratica, mai dall'orologio)."""
    dati = dict(pratica.get("dati") or {})
    campi = {"pratica": str(pratica["id"]), "ente": str(pratica["tenant_id"]),
             "pdv": str(pratica["pdv_id"]), "motivo": pratica["motivo"],
             "richiesta_da": str(pratica.get("creata_da") or ""),
             "ruolo": pratica.get("creata_ruolo"),
             "richiesta_il": pratica["created_at"].isoformat(),
             "urn": dati.get("urn")}
    if pratica["tipo"] == st.ANNULLAMENTO:
        campi["stato_precedente"] = dati.get("stato_precedente")
    else:
        campi["metadati_precedenti"] = dati.get("metadati_precedenti") or {}
        campi["metadati_nuovi"] = dati.get("metadati_nuovi") or {}
    return campi


async def _atto_nel_worm(pratica: Mapping[str, Any], adesso: datetime) -> dict[str, Any]:
    from libs.shared.db import get_async_session

    contenuto = atto(pratica["tipo"].lower(), campi_atto(pratica))
    async with get_async_session() as session:
        bloccata = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert bloccata is not None
        return await pr.archivia_documento(
            session, bloccata, tipo=st.ALLEGATO_ATTO,
            nome_file=f"atto_{pratica['tipo'].lower()}_{pratica['id']}.xml",
            content_type="application/xml", f=io.BytesIO(contenuto),
            adesso=pratica["created_at"] if pratica["created_at"] < adesso else adesso)


async def _in_attesa_di_approvazione(pratica: Mapping[str, Any], difetti: list[str],
                                     adesso: datetime) -> None:
    """La regola è diventata più severa prima che l'annullamento fosse applicato:
    la pratica torna in attesa della seconda persona (nessun lavoro, PdV
    intatto), con l'avviso all'ente."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        if attuale is None:
            return
        dati = dict(attuale["dati"] or {})
        dati["approvazione"] = {**dict(dati.get("approvazione") or {}), "richiesta": True,
                                "richiesta_dal_lavoratore_il": adesso.isoformat()}
        await repo.aggiorna_pratica(session, pratica["id"], adesso, dati=dati, lavoro=None,
                                    ultimo_errore=None)
        await avvisi.avvisa(session, attuale["tenant_id"], avvisi.ANNULLAMENTO_DA_APPROVARE,
                            str(pratica["id"]), {"pratica_id": str(pratica["id"]),
                                                 "pdv_id": str(pratica["pdv_id"]),
                                                 "stato_versamento": dati.get(
                                                     "stato_precedente")})
        await registra_audit(session, evento="ciclo_vita.annullamento.in_attesa_approvazione",
                             tenant_id=str(attuale["tenant_id"]),
                             dettagli={"pratica_id": str(pratica["id"]),
                                       "difetti": difetti[:10]})
    logger.warning("annullamento_in_attesa_di_approvazione", pratica_id=str(pratica["id"]))


def _motivo_respinta(difetti: list[str]) -> str:
    primo = difetti[0] if difetti else ""
    if primo.startswith("PDV_CAMBIATO"):
        return ("il versamento è cambiato prima dell'applicazione (preso in lavorazione o già "
                "concluso)")
    if primo.startswith(("RICHIEDENTE_", "APPROVATORE_", "RUOLO_", "PERMESSO_")):
        return ("chi ha chiesto o approvato non è più attivo o non ha più un ruolo o un "
                "permesso ammesso")
    return "la regola dell'ente per l'annullamento è cambiata prima dell'applicazione"


async def respingi_dal_sistema(session: Any, pratica: Mapping[str, Any], difetti: list[str],
                               adesso: datetime) -> dict[str, Any]:
    """Revisione R09 (W1): annullamento non ancora applicato (PdV intatto) chiuso
    RESPINTO dal lavoratore con il motivo in parole, i difetti, l'audit e
    l'avviso all'ente; la pratica non blocca più una nuova richiesta."""
    motivo = _motivo_respinta(difetti)
    dati = dict(pratica.get("dati") or {})
    dati["respinta_dal_sistema"] = {"motivo": motivo, "difetti": difetti[:20],
                                    "il": adesso.isoformat()}
    aggiornata = await pr.applica_transizione(
        session, pratica, st.RESPINTA, pr.SISTEMA,
        motivo=f"Annullamento non applicato: {motivo}", adesso=adesso, lavoro=None, dati=dati)
    await avvisi.avvisa(session, pratica["tenant_id"], avvisi.ANNULLAMENTO_RESPINTO,
                        str(pratica["id"]), {"pratica_id": str(pratica["id"]),
                                             "pdv_id": str(pratica["pdv_id"]),
                                             "motivo": motivo})
    await registra_audit(session, evento="ciclo_vita.annullamento.respinto_dal_sistema",
                         tenant_id=str(pratica["tenant_id"]),
                         dettagli={"pratica_id": str(pratica["id"]), "motivo": motivo,
                                   "difetti": difetti[:20]})
    logger.warning("annullamento_respinto_dal_sistema", pratica_id=str(pratica["id"]),
                   difetti=difetti[:5])
    return aggiornata


async def passo_applica_annullamento(pratica: Mapping[str, Any], *, adesso: datetime) -> bool:
    """Lavoratore (revisione R09, Z3): riverifica la catena e la regola ATTUALE
    dell'ente; poi, in UNA transazione, fatto ``AUTORIZZAZIONI_VERIFICATE``
    (solo il ruolo del lavoratore lo scrive), PdV → ANNULLATO, pratica → ESEGUITA
    (lavoro REGISTRO: atto, fatto, foglia). La sola approvazione mancante (regola
    più severa) → di nuovo in attesa della seconda persona; uno stato cambiato
    (PdV, persone, ruoli, regola: revisione R09, W1) → RESPINTA dal SISTEMA; ogni
    altro difetto (manomissione) → BLOCCATA con allarme, sempre con il PdV intatto."""
    from app.application import autorizzazioni
    from libs.shared.db import get_async_session

    difetti = await autorizzazioni.verifica_puntuale(pratica["id"])
    if difetti and pratica["stato"] == st.RICHIESTA and all(
            d.startswith(dom_aut.APPROVAZIONE_ASSENTE) for d in difetti):
        await _in_attesa_di_approvazione(pratica, difetti, adesso)
        return False
    if difetti and all(d.startswith((*autorizzazioni.DIFETTI_STATO_CAMBIATO,
                                     dom_aut.APPROVAZIONE_ASSENTE)) for d in difetti):
        # Revisione R09 (W1): qualcosa è cambiato prima dell'applicazione, il PdV è
        # intatto: la pratica si chiude RESPINTA (si può chiedere di nuovo)
        async with get_async_session() as session:
            bloccata = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
            if bloccata is not None and bloccata["stato"] in dom_aut.STATI_APPLICA_ANNULLAMENTO:
                await respingi_dal_sistema(session, bloccata, difetti, adesso)
        return False
    if difetti:
        await autorizzazioni.blocca(pratica, difetti, adesso)
        raise autorizzazioni.AutorizzazioniNonVerificate(difetti)
    dati = dict(pratica["dati"] or {})
    async with get_async_session() as session:
        bloccata = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        if bloccata is None or bloccata["stato"] != pratica["stato"] \
                or bloccata["versione"] != pratica["versione"]:
            return True  # cambiata nel frattempo: si rilegge al prossimo passo
        stato = (await session.execute(text(
            "SELECT stato::text FROM public.pdv WHERE id = :p FOR UPDATE"),
            {"p": pratica["pdv_id"]})).scalar()
        if stato != dati.get("stato_precedente"):
            # Revisione R09 (W1): PdV preso dalla pipeline o cambiato: RESPINTA
            await respingi_dal_sistema(session, bloccata, [
                f"PDV_CAMBIATO: il versamento è {stato} (alla richiesta "
                f"{dati.get('stato_precedente')})"], adesso)
            return False
        else:
            await session.execute(text(
                "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, "
                "esito, dettagli) VALUES ('ANNULLAMENTO', :p, :t, CAST(:a AS uuid), :e, "
                "CAST(:d AS jsonb))"),
                {"p": pratica["id"], "t": pratica["tenant_id"], "a": dati.get("aip_id"),
                 "e": autorizzazioni.ESITO_ANNULLAMENTO_APPLICATO,
                 "d": _json({"verificata_il": adesso.isoformat(),
                             "stato_pratica": pratica["stato"],
                             "pdv_id": str(pratica["pdv_id"]),
                             "stato_precedente": stato})})
            await annulla_logicamente(session, bloccata, stato, adesso)
    if difetti:
        await autorizzazioni.blocca(pratica, difetti, adesso)
        raise autorizzazioni.AutorizzazioniNonVerificate(difetti)
    logger.info("annullamento_applicato", pratica_id=str(pratica["id"]))
    return True


#: Revisione R09 (W3): riga di ``ciclo_vita_periodico`` (scrivibile SOLO dal
#: ruolo del lavoratore, migrazione 048) con l'istante (orologio del database) del
#: primo avvio del lavoratore R09: confine fra annullamenti R08 e R09
PASSO_RECUPERO_R08 = "recupero_annullamenti_r08"


async def recupera_annullamenti_r08() -> int:
    """Revisione R09 (W3), idempotente a ogni avvio del lavoratore: un annullamento
    applicato dall'API R08 (cronologia RICHIESTA → ESEGUITA del SISTEMA, nessuna
    approvazione, lavoro REGISTRO) richiesto PRIMA del primo avvio del lavoratore
    R09 — anche dopo la migrazione 048, fra migrazione e Chart — riceve il fatto
    ``AUTORIZZAZIONI_VERIFICATE`` che la fase REGISTRO pretende. Il confine è
    l'orologio del database: istante del primo avvio registrato dal lavoratore e
    ``created_at`` della voce d'audit della richiesta (scritta dal database, non
    dall'API). Una pratica nata dopo il primo avvio non si recupera mai. Restituisce
    le pratiche recuperate."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        await session.execute(text(
            "INSERT INTO public.ciclo_vita_periodico (nome, ultimo_giro, ultimo_esito) "
            "VALUES (:n, now(), '{}'::jsonb) ON CONFLICT (nome) DO NOTHING"),
            {"n": PASSO_RECUPERO_R08})
        limite = (await session.execute(text(
            "SELECT ultimo_giro FROM public.ciclo_vita_periodico WHERE nome = :n"),
            {"n": PASSO_RECUPERO_R08})).scalar_one()
        recuperate = (await session.execute(text(
            "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, "
            "dettagli) SELECT 'ANNULLAMENTO', p.id, p.tenant_id, NULL, "
            "'AUTORIZZAZIONI_VERIFICATE', jsonb_build_object('origine', 'recupero_r08', "
            "'primo_avvio_r09', CAST(:testo AS text)) FROM public.pratiche p "
            "WHERE p.tipo = 'ANNULLAMENTO' AND p.stato = 'ESEGUITA' AND p.lavoro = 'REGISTRO' "
            "AND NOT EXISTS (SELECT 1 FROM public.esiti_esecuzione e WHERE e.tipo = "
            "'ANNULLAMENTO' AND e.esito = 'AUTORIZZAZIONI_VERIFICATE' AND e.pratica_id = p.id) "
            "AND NOT EXISTS (SELECT 1 FROM public.pratiche_stati s WHERE s.pratica_id = p.id "
            "AND s.a_stato = 'APPROVATA') "
            "AND EXISTS (SELECT 1 FROM public.pratiche_stati s WHERE s.pratica_id = p.id "
            "AND s.da_stato = 'RICHIESTA' AND s.a_stato = 'ESEGUITA' AND s.ruolo = 'SISTEMA') "
            "AND EXISTS (SELECT 1 FROM public.audit_global a WHERE a.evento = "
            "'ciclo_vita.annullamento' AND a.tenant_id = p.tenant_id "
            "AND a.dettagli ->> 'pratica_id' = p.id::text AND a.created_at < :limite "
            "AND a.created_at > :inizio) "
            "RETURNING pratica_id"),
            {"limite": limite, "inizio": limite - timedelta(days=90),
             "testo": limite.isoformat()})).scalars().all()
    if recuperate:
        logger.warning("annullamenti_r08_recuperati", pratiche=[str(p) for p in recuperate])
    return len(recuperate)


async def passo_annullamento(pratica: Mapping[str, Any], registro: RegistroImpronte, *,
                             adesso: datetime) -> bool:
    """Lavoratore: atto nel WORM, poi foglia «annullamento» se il PdA è nel registro."""
    from app.application import autorizzazioni
    from libs.shared.db import get_async_session

    # Revisione R06 (V2): la catena della richiesta si verifica dal lavoratore
    # PRIMA dell'atto e del fatto (non dalla riga che l'API ha scritto)
    await autorizzazioni.assicura_puntuale(pratica, adesso)
    documento = await _atto_nel_worm(pratica, adesso)
    dati = dict(pratica["dati"] or {})
    foglia = None
    if dati.get("urn"):
        # R06 (VD-C-119): il fatto che il registro pretende, scritto dal solo
        # ruolo del lavoratore (l'API esposta non lo può scrivere)
        async with get_async_session() as session:
            await registra_fatto_puntuale(session, pratica, "ANNULLAMENTO", aip_id=dati["aip_id"],
                                          dettagli={"atto_sha256": documento["sha256"],
                                                    "pdv_id": str(pratica["pdv_id"])})
        foglia = await registro.aggiungi_foglia(
            tipo="annullamento", pda=dati["urn"],
            riferimento={"pratica": str(pratica["id"]), "pdv": str(pratica["pdv_id"])},
            impronta_documento_pratica=documento["sha256"])
    dati["registro"] = {"foglia_id": foglia, "atto_sha256": documento["sha256"],
                        "nel_registro": foglia is not None}
    async with get_async_session() as session:
        await repo.aggiorna_pratica(session, pratica["id"], adesso, dati=dati, lavoro=None,
                                    ultimo_errore=None)
        if foglia:
            await session.execute(text(
                "INSERT INTO public.pratiche_oggetti (pratica_id, tenant_id, aip_id, pdv_id, urn, "
                "esito, foglia_id) VALUES (:p, :t, CAST(:a AS uuid), :pdv, :urn, 'SELEZIONATO', "
                ":f) ON CONFLICT DO NOTHING"),
                {"p": pratica["id"], "t": pratica["tenant_id"], "a": dati.get("aip_id"),
                 "pdv": pratica["pdv_id"], "urn": dati["urn"], "f": foglia})
    return False


async def registra_fatto_puntuale(session: Any, pratica: Mapping[str, Any], tipo: str, *,
                                  aip_id: str, dettagli: Mapping[str, Any]) -> None:
    """R06 (VD-C-119): fatto di esecuzione di annullamento (``ATTO_ARCHIVIATO``)
    o rettifica (``PDV_VERSATO``) in ``esiti_esecuzione`` (INSERT concesso SOLO
    a ``verumdoc_ciclo_vita_lavoratore``), preteso dal riscontro della
    hashchain. Idempotente: una ripresa non lo duplica (stesso vincolo unico)."""
    esito = "ATTO_ARCHIVIATO" if tipo == "ANNULLAMENTO" else "PDV_VERSATO"
    await session.execute(text(
        "INSERT INTO public.esiti_esecuzione (tipo, pratica_id, tenant_id, aip_id, esito, "
        "dettagli) VALUES (:tipo, :p, :t, CAST(:aip AS uuid), :esito, CAST(:d AS jsonb)) "
        "ON CONFLICT (tipo, pratica_id, aip_id, esito) DO NOTHING"),
        {"tipo": tipo, "p": pratica["id"], "t": pratica["tenant_id"], "aip": str(aip_id),
         "esito": esito, "d": json.dumps(dict(dettagli), default=str)})


# ── Rettifica ────────────────────────────────────────────────────────────

def controlla_metadati_nuovi(metadati: Mapping[str, Any]) -> dict[str, Any]:
    if not metadati:
        raise ErroreApi("LC-422-METADATI", "Indicare almeno un metadato da rettificare")
    if len(metadati) > 60:
        raise ErroreApi("LC-422-METADATI", "Al più 60 metadati per rettifica")
    puliti: dict[str, Any] = {}
    for chiave, valore in metadati.items():
        if not _CHIAVE_METADATO.fullmatch(str(chiave)) or chiave in METADATI_RISERVATI:
            raise ErroreApi("LC-422-METADATI", f"Nome di metadato non ammesso: {chiave!r}")
        if isinstance(valore, list):
            if not all(isinstance(x, str) for x in valore):
                raise ErroreApi("LC-422-METADATI", f"{chiave}: lista di soli testi")
        elif not (valore is None or isinstance(valore, str | int | float | bool)):
            raise ErroreApi("LC-422-METADATI", f"{chiave}: valore non ammesso")
        puliti[str(chiave)] = valore
    return puliti


async def rettifica(identita: Identita, *, aip_id: str | None, pdv_id: str | None,
                    metadati_nuovi: Mapping[str, Any], motivo: str,
                    idempotency_key: str | None,
                    conferma: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
    if bool(aip_id) == bool(pdv_id):
        raise ErroreApi("LC-422-METADATI", "Indicare aip_id oppure pdv_id (uno dei due)")
    blocco = motivo_blocco_rettifica(identita, settings.rettifica_ruoli)
    if blocco is not None:
        raise ErroreApi(*blocco)
    nuovi = controlla_metadati_nuovi(metadati_nuovi)
    chiave = _chiave_idempotenza(idempotency_key)
    adesso = pr.adesso_utc()
    from libs.shared.db import get_async_session

    try:
        async with get_async_session() as session:
            if aip_id:
                aid = pr.uuid_o_404(aip_id, "LC-404-PDV", "Pacchetto non trovato per questo ente")
                trovato = (await session.execute(text(
                    "SELECT pdv_id FROM public.aip WHERE id = :a AND tenant_id = CAST(:t AS uuid)"),
                    {"a": aid, "t": identita.tenant_id})).scalar()
                if trovato is None:
                    raise ErroreApi("LC-404-PDV", "Pacchetto non trovato per questo ente")
                pid = uuid.UUID(str(trovato))
            else:
                pid = pr.uuid_o_404(str(pdv_id), "LC-404-PDV", "Versamento non trovato")
            tenant = uuid.UUID(identita.tenant_id)
            if chiave:
                esistente = await repo.pratica_per_idempotenza(session, tenant, st.RETTIFICA,
                                                               chiave)
                if esistente is not None:
                    if esistente["pdv_id"] != pid or \
                            (esistente["dati"] or {}).get("metadati_nuovi") != nuovi:
                        raise ErroreApi("LC-409-IDEMPOTENZA",
                                        "Stessa Idempotency-Key con una richiesta diversa")
                    return pr.rappresenta(esistente), False
            pdv = await _pdv_dell_ente(session, identita, pid)
            if pdv["stato"] != "CONSERVATO":
                raise ErroreApi("LC-409-PDV-STATO",
                                f"Si rettifica solo un versamento CONSERVATO (stato attuale: "
                                f"{pdv['stato']})", dettagli={"stato": pdv["stato"]})
            pda = await _pda_del_pdv(session, pid)
            meta = pdv["metadata_json"] or {}
            indice = meta.get("indice") or {}
            principale = next((d for d in indice.get("documenti") or []
                               if d.get("tipo") == "PRINCIPALE"),
                              (indice.get("documenti") or [{}])[0])
            precedenti = dict(principale.get("parametri") or {})
            dati = {"aip_id": str(pda["id"]) if pda else None, "urn": pda["urn"] if pda else None,
                    "classe": pdv["classe"], "metadati_nuovi": nuovi,
                    "metadati_precedenti": {k: precedenti.get(k) for k in sorted(nuovi)},
                    "parametri_principale": precedenti,
                    # R17 (VD-P-164): riferimento come indicato (parte del contenuto confermato)
                    "riferimento": conferme.cd.riferimento_rettifica(aid if aip_id else None,
                                                                     pid)}
            pratica_id = await repo.inserisci_pratica(
                session, tenant_id=tenant, tipo=st.RETTIFICA, stato=st.RICHIESTA, motivo=motivo,
                dati=dati, creata_da=identita.user_id, creata_ruolo=identita.ruolo, pdv_id=pid,
                idempotency_key=chiave, lavoro=LAVORO_VERSA, adesso=adesso)
            await repo.inserisci_stato(session, pratica_id=pratica_id, tenant_id=tenant,
                                       da_stato=None, a_stato=st.RICHIESTA,
                                       utente_id=identita.user_id, ruolo=identita.ruolo,
                                       metodo=identita.metodo, motivo=motivo, allegati=(),
                                       adesso=adesso)
            await pr.evento(session, pr.TOPIC_CAMBIO_STATO, str(pratica_id), {
                "pratica_id": str(pratica_id), "tipo": st.RETTIFICA, "da_stato": None,
                "a_stato": st.RICHIESTA, "versione": 1, "ente": str(tenant),
                "il": adesso.isoformat()}, tenant)
            await registra_audit(session, evento="ciclo_vita.rettifica.richiesta",
                                 tenant_id=str(tenant), user_id=identita.user_id,
                                 aip_id=dati["aip_id"],
                                 dettagli={"pratica_id": str(pratica_id), "pdv_id": str(pid),
                                           "metadati": sorted(nuovi), "motivo": motivo})
            pratica = await repo.leggi_pratica(session, pratica_id)
            assert pratica is not None
            # R17 (VD-P-164): la rettifica è una decisione sensibile (conferma del gateway)
            await conferme.registra(session, identita, azione=conferme.cd.RETTIFICA_RICHIEDI,
                                    pratica=pratica, conferma=conferma,
                                    campi={"riferimento": dati["riferimento"],
                                           "metadati_nuovi": nuovi, "motivo": motivo},
                                    versione_attesa=0, adesso=adesso)
    except IntegrityError:
        raise ErroreApi("LC-409-PRATICA-APERTA",
                        "Esiste già una rettifica in corso per questo versamento") from None
    except SQLAlchemyError as exc:
        raise pr.errore_db("rettifica", exc) from None
    return pr.rappresenta(pratica), True


def indice_rettifica(pratica: Mapping[str, Any], sha256: str, nome_file: str) -> dict[str, Any]:
    """Indice del PdV di rettifica: atto come principale con i metadati del
    principale originale aggiornati e i riferimenti all'originale."""
    dati = dict(pratica["dati"] or {})
    parametri = {**(dati.get("parametri_principale") or {}), **(dati.get("metadati_nuovi") or {})}
    parametri.update({
        "identificativo_documento": f"rettifica-{pratica['id']}",
        "rettifica_di": dati.get("urn") or str(pratica["pdv_id"]),
        "pdv_rettificato": str(pratica["pdv_id"]),
        "pratica_rettifica": str(pratica["id"]),
    })
    return {
        "classe_documentale": dati["classe"],
        "chiave": {"registro": "RETTIFICA", "anno": str(pratica["created_at"].year),
                   "numero": str(pratica["id"])},
        "documenti": [{
            "id": str(uuid.uuid5(NAMESPACE_DOCUMENTI, f"{pratica['id']}:atto")),
            "tipo": "PRINCIPALE", "nome_file": nome_file, "formato": "application/xml",
            "hash": {"impronta": sha256, "algoritmo": "SHA-256"},
            "parametri": parametri,
        }],
    }


async def per_conto_di(pratica: Mapping[str, Any]) -> dict[str, str] | None:
    """Identità per il token di canale: l'utente che ha avviato la pratica con il
    suo ruolo ATTUALE (None se non più attivo)."""
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        ruolo = await repo.ruolo_utente(session, pratica.get("creata_da") and
                                        str(pratica["creata_da"]), pratica["tenant_id"])
    if ruolo is None:
        return None
    return {"tenant_id": str(pratica["tenant_id"]), "user_id": str(pratica["creata_da"]),
            "ruolo": ruolo}


async def _blocchi(contenuto: bytes) -> AsyncIterator[bytes]:
    yield contenuto


async def passo_rettifica(pratica: Mapping[str, Any], registro: RegistroImpronte,
                          versatore: Versatore, *, adesso: datetime) -> bool:
    from app.application import autorizzazioni
    from libs.shared.db import get_async_session

    if pratica["lavoro"] == LAVORO_VERSA:
        # Revisione R06 (V2): catena della richiesta verificata prima dell'atto,
        # del versamento per conto del richiedente e del fatto
        if await autorizzazioni.assicura_puntuale(pratica, adesso) == autorizzazioni.RESPINGI:
            await _conclusa(pratica, st.RESPINTA, "Utente richiedente non più attivo o non più "
                                                  "autorizzato", adesso)
            return False
        documento = await _atto_nel_worm(pratica, adesso)
        contenuto = atto(pratica["tipo"].lower(), campi_atto(pratica))
        nome = documento["nome_file"]
        conto = await per_conto_di(pratica)
        if conto is None:
            await _conclusa(pratica, st.RESPINTA, "Utente richiedente non più attivo", adesso)
            return False
        try:
            caricamento = await versatore.carica(
                conto, nome_file=nome, dimensione=len(contenuto), sha256=documento["sha256"],
                blocchi=lambda: _blocchi(contenuto), chiave=f"cv-{pratica['id']}-atto")
            esito = await versatore.versa(
                conto, indice=indice_rettifica(pratica, documento["sha256"], nome),
                file={nome: caricamento}, chiave=f"cv-{pratica['id']}-versamento")
        except ErroreGateway as exc:
            if exc.ritentabile:
                raise
            await _conclusa(pratica, st.RESPINTA,
                            f"Versamento di rettifica rifiutato: {exc.problema.get('codice')} "
                            f"{exc.problema.get('title') or ''}".strip(), adesso,
                            errore=exc.problema)
            return False
        pdv_rettifica = uuid.UUID(str(esito["pdv_id"]))
        async with get_async_session() as session:
            attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
            assert attuale is not None
            await repo.aggiorna_pratica(session, pratica["id"], adesso,
                                        pdv_risultato_id=pdv_rettifica)
            if (pratica["dati"] or {}).get("aip_id"):
                # R06 (VD-C-119): il PdV di rettifica versato DAL LAVORATORE (con il
                # suo token di canale), fatto che solo il suo ruolo può scrivere
                await registra_fatto_puntuale(
                    session, pratica, "RETTIFICA", aip_id=pratica["dati"]["aip_id"],
                    dettagli={"atto_sha256": documento["sha256"],
                              "pdv_rettifica": str(pdv_rettifica)})
            await pr.applica_transizione(
                session, attuale, st.IN_VERSAMENTO, pr.SISTEMA,
                motivo=f"PdV di rettifica {pdv_rettifica} versato", adesso=adesso,
                lavoro=LAVORO_ATTENDI)
        return False
    # ATTENDI: il PdV di rettifica segue la pipeline normale
    async with get_async_session() as session:
        stato = (await session.execute(text(
            "SELECT stato::text FROM public.pdv WHERE id = :p"),
            {"p": pratica["pdv_risultato_id"]})).scalar()
        pda = await _pda_del_pdv(session, pratica["pdv_risultato_id"])
    if stato == "ERRORE":
        await _conclusa(pratica, st.RESPINTA, "PdV di rettifica rifiutato dalla verifica", adesso)
        return False
    if stato != "CONSERVATO":
        async with get_async_session() as session:
            await repo.aggiorna_pratica(
                session, pratica["id"], adesso,
                prossimo_tentativo=adesso + timedelta(
                    seconds=settings.lavoratore_attesa_esterna_secondi))
        return False
    allegati = []
    async with get_async_session() as session:
        allegati = await repo.allegati(session, pratica["id"])
    atto_sha = next(a["sha256"] for a in allegati if a["tipo"] == st.ALLEGATO_ATTO)
    dati = dict(pratica["dati"] or {})
    foglia = None
    if dati.get("urn"):
        foglia = await registro.aggiungi_foglia(
            tipo="rettifica", pda=dati["urn"],
            riferimento={"pratica": str(pratica["id"]),
                         "pdv_rettifica": str(pratica["pdv_risultato_id"]),
                         "urn_rettifica": pda["urn"] if pda else None},
            impronta_documento_pratica=atto_sha)
    dati["registro"] = {"foglia_id": foglia, "atto_sha256": atto_sha,
                        "urn_rettifica": pda["urn"] if pda else None}
    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert attuale is not None
        if attuale["stato"] != st.IN_VERSAMENTO:
            return False
        await pr.applica_transizione(session, attuale, st.COMPLETATA, pr.SISTEMA,
                                     motivo="PdV di rettifica conservato", adesso=adesso,
                                     lavoro=None, dati=dati)
        await pr.evento(session, pr.TOPIC_RETTIFICATO, str(pratica["pdv_id"]), {
            "pdv_id": str(pratica["pdv_id"]), "pdv_rettifica_id": str(pratica["pdv_risultato_id"]),
            "pratica_id": str(pratica["id"]), "ente": str(pratica["tenant_id"]),
            "urn": dati.get("urn"), "urn_rettifica": dati["registro"]["urn_rettifica"],
            "rettificato_il": adesso.isoformat()}, pratica["tenant_id"])
        await registra_audit(session, evento="ciclo_vita.rettifica.completata",
                             tenant_id=str(pratica["tenant_id"]),
                             dettagli={"pratica_id": str(pratica["id"]),
                                       "pdv_id": str(pratica["pdv_id"]),
                                       "pdv_rettifica_id": str(pratica["pdv_risultato_id"]),
                                       "foglia_id": foglia})
    return False


async def _conclusa(pratica: Mapping[str, Any], stato: str, motivo: str, adesso: datetime, *,
                    errore: Mapping[str, Any] | None = None) -> None:
    from libs.shared.db import get_async_session

    async with get_async_session() as session:
        attuale = await repo.leggi_pratica(session, pratica["id"], per_aggiornare=True)
        assert attuale is not None
        dati = dict(attuale["dati"] or {})
        if errore:
            dati["errore"] = dict(errore)
        await pr.applica_transizione(session, attuale, stato, pr.SISTEMA, motivo=motivo[:1000],
                                     adesso=adesso, lavoro=None, dati=dati)


__all__ = ["LAVORO_ATTENDI", "LAVORO_REGISTRO", "LAVORO_VERSA", "PASSO_RECUPERO_R08",
           "annullamento_bloccato", "annulla", "recupera_annullamenti_r08",
           "respingi_dal_sistema",
           "passo_applica_annullamento",
           "annulla_logicamente", "campi_atto", "decidi_approvazione",
           "controlla_metadati_nuovi", "indice_rettifica", "passo_annullamento",
           "passo_rettifica", "per_conto_di", "rettifica"]
