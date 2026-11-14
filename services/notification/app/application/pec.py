# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
services/notification/app/application/pec.py — canale PEC: accodamento per caso
d'uso e giro del lavoratore della posta (R13b, VD-C-078).

Accodamento (consumer della posta, processo notification):
  dagli eventi ``TOPIC_PEC`` la comunicazione formale (``app.domain.pec``); se il caso
  d'uso è fra i ``PEC_CASI_USO`` dell'ente, una riga ``pec_messaggi`` IN_CODA verso la
  PEC del referente (``tenants.pec_referente``), o NON_PARTITO con il motivo se
  l'indirizzo manca. Deduplica atomica (indice ``uq_pec_messaggi_dedup``).

Giro del lavoratore (``notification-posta``, l'unico con ``PEC_SEGRETI_JSON``):
  1. canali attivi dal registro (``canali_pec``), adattatori costruiti con i segreti
     legati agli host; stato onesto per canale in ``pec_canali_stato``;
  2. recupero dei messaggi rimasti IN_INVIO dopo un arresto; prelievo con SKIP LOCKED
     (più repliche insieme), ``message_id`` e canale fissati al primo prelievo (lo
     stesso Message-ID a ogni ritentativo: le ricevute si associano comunque);
  3. invio: INVIATO (solo se ancora IN_INVIO: una ricevuta può essere già arrivata),
     ritentativo con attesa crescente, NON_PARTITO per errori permanenti o tentativi
     esauriti;
  4. lettura delle caselle (IMAP, UID crescenti, sola lettura): ogni messaggio letto
     diventa una riga ``pec_ricevute`` (idempotente per UID); solo una ricevuta
     AUTENTICA (firma S/MIME di un gestore fra le radici del canale) e ASSOCIATA (msgid,
     mittente e destinatario FIRMATI corrispondenti al messaggio) cambia lo stato, nella
     stessa transazione e con il messaggio bloccato. Il database lo riverifica (trigger
     ``pec_messaggi_protetti``).
R16 (VD-C-171): consegna SENZA DOPPIONI dopo un arresto fra l'accettazione SMTP del gestore
e la scrittura di INVIATO. Il Message-ID è DETERMINISTICO (``<pec.<id del messaggio>@<dominio
della casella>>``, fissato al primo prelievo) e un messaggio rimasto IN_INVIO oltre
``PEC_RECUPERO_DOPO_SECONDI`` si RICONCILIA con le ricevute prima di essere rimandato:
  - se fra le ricevute del canale ce n'è una con quel Message-ID (il gestore l'ha preso in
    carico), il messaggio diventa INVIATO senza reinvio (le ricevute associate fanno il
    resto; senza accettazione valida scatta ``PecSenzaRicevuta``);
  - si rimanda (stesso Message-ID) solo se nessuna ricevuta lo cita E la casella del canale
    è stata letta con successo almeno ``PEC_RECUPERO_DOPO_SECONDI`` dopo l'arresto (le
    ricevute di accettazione arrivano in pochi secondi): senza una lettura riuscita il
    messaggio resta IN_INVIO e si riprova al giro dopo.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.domain import pec as dom
from app.infrastructure.pec_canale import (
    CanaleNonCostruibile,
    CanalePec,
    ErroreCasella,
    MessaggioCasella,
    costruisci,
)
from app.services.smtp import INVIATA, indirizzo_plausibile, maschera
from libs.shared.pec.configurazione import (
    CanaleRegistrato,
    leggi_segreti,
    segreti_presenti,
    vincoli_deploy,
)
from libs.shared.pec.ricevute import (
    POSTA_CERTIFICATA,
    RicevutaNonAutentica,
    leggi_ricevuta,
    motivo_non_associata,
)
from libs.shared.pec.smime import radici_da_pem

logger = structlog.get_logger(__name__)


def _fabbrica_conservatore(motivo: str) -> Any:
    from libs.shared.db.ambito import fabbrica_sessione_conservatore

    return fabbrica_sessione_conservatore(motivo)


# ── Accodamento ───────────────────────────────────────────────────────

LettoreCasi = Callable[[str], Any]


async def casi_uso_ente(tenant_id: str) -> tuple[str, ...]:
    from libs.shared.parametri import parametri
    from libs.verumdoc_rules.parametri_servizio import PEC_CASI_USO

    return tuple((await parametri([PEC_CASI_USO], tenant_id)).get(PEC_CASI_USO) or ())


_INSERISCI = (
    "INSERT INTO public.pec_messaggi (tenant_id, caso_uso, oggetto_id, destinatario, oggetto, "
    " testo, stato, errore, creato_da) VALUES (CAST(:t AS uuid), :caso, :oid, :dest, :ogg, "
    " :testo, :stato, :errore, CAST(:da AS uuid)) "
    "ON CONFLICT (tenant_id, caso_uso, destinatario, oggetto_id) DO NOTHING RETURNING id::text"
)


async def inserisci(sessione_factory: Any, *, tenant_id: str | None, caso_uso: str,
                    oggetto_id: str, destinatario: str, oggetto: str, testo: str,
                    stato: str = dom.IN_CODA, errore: str | None = None,
                    creato_da: str | None = None) -> str | None:
    """Id del messaggio accodato, None se già presente (deduplica)."""
    async with sessione_factory() as s:
        return (await s.execute(text(_INSERISCI), {
            "t": tenant_id, "caso": caso_uso, "oid": oggetto_id[:200],
            "dest": destinatario[:320], "ogg": oggetto[:300], "testo": testo, "stato": stato,
            "errore": (errore or None) and errore[:500], "da": creato_da})).scalar_one_or_none()


async def accoda_comunicazione(topic: str, evento: Mapping[str, Any], *,
                               sessione_factory: Any = None,
                               casi: LettoreCasi = casi_uso_ente,
                               lettore_cliente: Any = None) -> int:
    """Accoda la comunicazione PEC dell'evento se il caso d'uso è scelto per l'ente."""
    from app.services.destinatari import leggi_cliente
    from app.services.elaborazione import tenant_o_vuoto

    c = dom.comunicazione(topic, evento)
    if c is None:
        return 0
    tenant_id = tenant_o_vuoto(evento.get("tenant_id") or evento.get("ente"))
    if not tenant_id:
        logger.warning("pec_evento_senza_ente", topic=topic)
        return 0
    if c.caso_uso not in await casi(tenant_id):
        return 0
    cliente = await (lettore_cliente or leggi_cliente)(tenant_id) or {}
    pec = str(cliente.get("pec_referente") or "").strip().lower()
    fabbrica = sessione_factory or _fabbrica_conservatore(
        "coda PEC: comunicazioni formali di tutti gli enti")
    if not indirizzo_plausibile(pec):
        await inserisci(fabbrica, tenant_id=tenant_id, caso_uso=c.caso_uso,
                        oggetto_id=c.oggetto_id, destinatario=pec or "-", oggetto=c.oggetto,
                        testo=c.testo, stato=dom.NON_PARTITO,
                        errore="PEC dell'ente non configurata (pec_referente)")
        return 0
    nuovo = await inserisci(fabbrica, tenant_id=tenant_id, caso_uso=c.caso_uso,
                            oggetto_id=c.oggetto_id, destinatario=pec, oggetto=c.oggetto,
                            testo=c.testo)
    logger.info("pec_accodata", tenant_id=tenant_id, caso=c.caso_uso, nuova=bool(nuovo),
                destinatario=maschera(pec))
    return int(bool(nuovo))


# ── Lavoratore ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ImpostazioniPec:
    segreti_json: str
    produzione: bool
    ritardi_secondi: list[int]
    lotto: int = 20
    recupero_dopo_secondi: int = 900
    lettura_massimo: int = 200


@dataclass
class CanalePronto:
    registrato: CanaleRegistrato
    adattatore: CanalePec | None
    problemi: list[str] = field(default_factory=list)
    segreti: dict[str, bool] = field(default_factory=dict)


async def canali_attivi(fabbrica: Any) -> list[CanaleRegistrato]:
    async with fabbrica() as s:
        righe = (await s.execute(text(
            "SELECT codice, tipo, nome, priorita, configurazione, versione "
            "FROM public.canali_pec WHERE attivo ORDER BY priorita, codice"))).mappings().all()
    return [CanaleRegistrato(r["codice"], r["tipo"], r["nome"], int(r["priorita"]),
                             dict(r["configurazione"] or {}), int(r["versione"])) for r in righe]


def prepara(canali: list[CanaleRegistrato], imp: ImpostazioniPec,
            costruttore: Callable[..., CanalePec] = costruisci) -> list[CanalePronto]:
    pronti = []
    for c in canali:
        try:
            segreti = leggi_segreti(imp.segreti_json, c.codice)
            vincoli = vincoli_deploy(imp.segreti_json, c.codice)
        except ValueError as exc:
            pronti.append(CanalePronto(c, None, [str(exc)]))
            continue
        presenti = segreti_presenti(c.tipo, segreti)
        try:
            pronti.append(CanalePronto(c, costruttore(c, segreti, vincoli,
                                                      produzione=imp.produzione), [], presenti))
        except CanaleNonCostruibile as exc:
            pronti.append(CanalePronto(c, None, exc.motivi, presenti))
    return pronti


async def scrivi_stato(fabbrica: Any, p: CanalePronto, adesso: datetime, *,
                       invio: bool = False, lettura: bool = False,
                       errore: str | None = None) -> None:
    async with fabbrica() as s:
        await s.execute(text(
            "INSERT INTO public.pec_canali_stato (canale_codice, segreti_presenti, problemi, "
            " ultimo_invio_il, ultima_lettura_il, ultimo_errore, aggiornato_il) VALUES (:c, "
            " CAST(:seg AS jsonb), CAST(:pr AS jsonb), CASE WHEN :inv THEN CAST(:a AS timestamptz) "
            " END, CASE WHEN :let THEN CAST(:a AS timestamptz) END, :err, :a) "
            "ON CONFLICT (canale_codice) DO UPDATE SET "
            "segreti_presenti = EXCLUDED.segreti_presenti,"
            " problemi = EXCLUDED.problemi, ultimo_invio_il = COALESCE(EXCLUDED.ultimo_invio_il, "
            " pec_canali_stato.ultimo_invio_il), ultima_lettura_il = COALESCE("
            " EXCLUDED.ultima_lettura_il, pec_canali_stato.ultima_lettura_il), "
            " ultimo_errore = CASE WHEN :azzera THEN NULL ELSE COALESCE(EXCLUDED.ultimo_errore, "
            " pec_canali_stato.ultimo_errore) END, aggiornato_il = EXCLUDED.aggiornato_il"),
            {"c": p.registrato.codice, "seg": json.dumps(p.segreti),
             "pr": json.dumps(p.problemi[:20]), "inv": invio, "let": lettura,
             "a": adesso, "err": (errore or None) and errore[:500],
             "azzera": (invio or lettura) and not errore})


_PRELEVA = (
    "UPDATE public.pec_messaggi m SET stato = 'IN_INVIO', updated_at = :adesso, "
    " canale_codice = COALESCE(m.canale_codice, :canale), "
    " message_id = COALESCE(m.message_id, '<pec.' || m.id::text || '@' || :dominio || '>') "
    "WHERE m.id IN (SELECT id FROM public.pec_messaggi WHERE stato = 'IN_CODA' "
    "  AND (prossimo_tentativo IS NULL OR prossimo_tentativo <= :adesso) "
    "  AND (canale_codice IS NULL OR canale_codice = ANY(CAST(:canali AS text[]))) "
    "  ORDER BY prossimo_tentativo NULLS FIRST, created_at LIMIT :n FOR UPDATE SKIP LOCKED) "
    "RETURNING m.id::text AS id, m.canale_codice, m.message_id, m.destinatario, m.oggetto, "
    "          m.testo, m.tentativi, m.tenant_id::text AS tenant_id, m.caso_uso"
)


def dominio_message_id(indirizzo: str) -> str:
    """Dominio del Message-ID: quello della casella del canale (R16, VD-C-171)."""
    dominio = indirizzo.split("@", 1)[1] if "@" in indirizzo else ""
    return dominio if re.fullmatch(r"[A-Za-z0-9.-]{1,200}", dominio) else "verumdoc.invalid"


def message_id(messaggio_id: str, indirizzo: str) -> str:
    """Message-ID DETERMINISTICO di un messaggio PEC: ``<pec.<id>@<dominio>>``; lo stesso
    calcolato dal database al primo prelievo (``_PRELEVA``)."""
    return f"<pec.{messaggio_id}@{dominio_message_id(indirizzo)}>"


#: R16 (VD-C-171): riconciliazione dei messaggi rimasti IN_INVIO dopo un arresto
_RICONCILIA_CON_RICEVUTA = (
    "UPDATE public.pec_messaggi m SET stato = 'INVIATO', updated_at = :a, "
    " inviato_il = COALESCE(m.inviato_il, :a), errore = :nota "
    "WHERE m.stato = 'IN_INVIO' AND m.updated_at < :soglia AND m.message_id IS NOT NULL "
    "  AND EXISTS (SELECT 1 FROM public.pec_ricevute r WHERE r.canale_codice = m.canale_codice "
    "              AND r.msgid = m.message_id)")
_RIMANDA_SENZA_RICEVUTA = (
    "UPDATE public.pec_messaggi m SET stato = 'IN_CODA', updated_at = :a "
    "WHERE m.stato = 'IN_INVIO' AND m.updated_at < :soglia "
    "  AND NOT EXISTS (SELECT 1 FROM public.pec_ricevute r "
    "                  WHERE r.canale_codice = m.canale_codice AND r.msgid = m.message_id) "
    "  AND (m.canale_codice IS NULL OR EXISTS (SELECT 1 FROM public.pec_canali_stato s "
    "       WHERE s.canale_codice = m.canale_codice "
    "         AND s.ultima_lettura_il >= m.updated_at + make_interval(secs => :rec)))")
NOTA_RICONCILIATO = ("riconciliato dopo un arresto: il gestore ha già una ricevuta per questo "
                     "Message-ID, nessun reinvio")


async def _invia(fabbrica: Any, pronti: list[CanalePronto], imp: ImpostazioniPec,
                 adesso: datetime, conteggi: dict[str, int]) -> None:
    disponibili = {p.registrato.codice: p for p in pronti if p.adattatore is not None}
    soglia = adesso - timedelta(seconds=imp.recupero_dopo_secondi)
    async with fabbrica() as s:
        # R16 (VD-C-171): prima la riconciliazione con le ricevute, poi il reinvio
        conteggi["riconciliati"] += int((await s.execute(text(_RICONCILIA_CON_RICEVUTA), {
            "a": adesso, "soglia": soglia, "nota": NOTA_RICONCILIATO})).rowcount or 0)
        conteggi["recuperati"] += int((await s.execute(text(_RIMANDA_SENZA_RICEVUTA), {
            "a": adesso, "soglia": soglia, "rec": float(imp.recupero_dopo_secondi)})
        ).rowcount or 0)
    if not disponibili:
        # nessun canale utilizzabile: i messaggi in coda attendono e poi non partono
        await _senza_canale(fabbrica, imp, adesso, conteggi)
        return
    principale = next(iter(disponibili.values()))
    dominio = dominio_message_id(principale.adattatore.indirizzo)  # type: ignore[union-attr]
    async with fabbrica() as s:
        righe = (await s.execute(text(_PRELEVA), {
            "adesso": adesso, "canale": principale.registrato.codice, "dominio": dominio,
            "canali": list(disponibili), "n": imp.lotto,
        })).mappings().all()
    for r in righe:
        p = disponibili[r["canale_codice"]]
        assert p.adattatore is not None
        esito = await p.adattatore.invia(r["destinatario"], r["oggetto"], r["testo"],
                                         r["message_id"])
        tentativo = int(r["tentativi"]) + 1
        async with fabbrica() as s:
            if esito.esito == INVIATA:
                await s.execute(text(
                    "UPDATE public.pec_messaggi SET stato = 'INVIATO', inviato_il = :a, "
                    " tentativi = :t, errore = NULL, prossimo_tentativo = NULL, updated_at = :a "
                    "WHERE id = CAST(:id AS uuid) AND stato = 'IN_INVIO'"),
                    {"a": adesso, "t": tentativo, "id": r["id"]})
                conteggi["inviati"] += 1
            elif esito.ritentabile and tentativo <= len(imp.ritardi_secondi):
                await s.execute(text(
                    "UPDATE public.pec_messaggi SET stato = 'IN_CODA', tentativi = :t, "
                    " prossimo_tentativo = :p, errore = :e, updated_at = :a "
                    "WHERE id = CAST(:id AS uuid) AND stato = 'IN_INVIO'"),
                    {"t": tentativo, "a": adesso, "e": esito.messaggio[:500], "id": r["id"],
                     "p": adesso + timedelta(seconds=imp.ritardi_secondi[tentativo - 1])})
                conteggi["riprogrammati"] += 1
            else:
                motivo = esito.messaggio if not esito.ritentabile else \
                    f"tentativi esauriti ({tentativo}): {esito.messaggio}"
                await s.execute(text(
                    "UPDATE public.pec_messaggi SET stato = 'NON_PARTITO', tentativi = :t, "
                    " errore = :e, esito_il = :a, prossimo_tentativo = NULL, updated_at = :a "
                    "WHERE id = CAST(:id AS uuid) AND stato = 'IN_INVIO'"),
                    {"t": tentativo, "e": motivo[:500], "a": adesso, "id": r["id"]})
                conteggi["non_partiti"] += 1
                logger.warning("pec_non_partita", id=r["id"], motivo=motivo[:200],
                               destinatario=maschera(r["destinatario"]))
        if esito.esito == INVIATA:
            await scrivi_stato(fabbrica, p, adesso, invio=True)


async def _senza_canale(fabbrica: Any, imp: ImpostazioniPec, adesso: datetime,
                        conteggi: dict[str, int]) -> None:
    motivo = "nessun canale PEC attivo e utilizzabile"
    massimo = len(imp.ritardi_secondi)
    async with fabbrica() as s:
        righe = (await s.execute(text(
            "SELECT id::text, tentativi FROM public.pec_messaggi WHERE stato = 'IN_CODA' "
            "AND (prossimo_tentativo IS NULL OR prossimo_tentativo <= :a) "
            "ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED"),
            {"a": adesso, "n": imp.lotto})).all()
        for id_, tentativi in righe:
            t = int(tentativi) + 1
            if t <= massimo:
                await s.execute(text(
                    "UPDATE public.pec_messaggi SET tentativi = :t, prossimo_tentativo = :p, "
                    " errore = :e, updated_at = :a WHERE id = CAST(:id AS uuid)"),
                    {"t": t, "p": adesso + timedelta(seconds=imp.ritardi_secondi[t - 1]),
                     "e": motivo, "a": adesso, "id": id_})
                conteggi["riprogrammati"] += 1
            else:
                await s.execute(text(
                    "UPDATE public.pec_messaggi SET stato = 'NON_PARTITO', tentativi = :t, "
                    " errore = :e, esito_il = :a, prossimo_tentativo = NULL, updated_at = :a "
                    "WHERE id = CAST(:id AS uuid)"),
                    {"t": t, "e": f"tentativi esauriti: {motivo}", "a": adesso, "id": id_})
                conteggi["non_partiti"] += 1


async def _ultimo_uid(fabbrica: Any, codice: str) -> tuple[int | None, int]:
    async with fabbrica() as s:
        riga = (await s.execute(text(
            "SELECT imap_uidvalidity FROM public.pec_ricevute WHERE canale_codice = :c "
            "ORDER BY id DESC LIMIT 1"), {"c": codice})).first()
        if riga is None:
            return None, 0
        massimo = (await s.execute(text(
            "SELECT max(imap_uid) FROM public.pec_ricevute WHERE canale_codice = :c "
            "AND imap_uidvalidity = :v"), {"c": codice, "v": int(riga[0])})).scalar_one()
    return int(riga[0]), int(massimo or 0)


_REGISTRA = (
    "INSERT INTO public.pec_ricevute (canale_codice, imap_uidvalidity, imap_uid, esito, motivo, "
    " messaggio_id, tenant_id, tipo, msgid, identificativo, gestore, consegna, errore, "
    " errore_esteso, data_ricevuta, certificato_sha256, certificato_soggetto, impronta_sha256, "
    " dimensione, eml, ricevuta_il) VALUES (:c, :v, :u, :esito, :motivo, CAST(:mid AS uuid), "
    " CAST(:t AS uuid), :tipo, :msgid, :ident, :gestore, :consegna, :errore, :esteso, :data, "
    " :cert, :sogg, :impronta, :dim, :eml, :a) "
    "ON CONFLICT (canale_codice, imap_uidvalidity, imap_uid) DO NOTHING RETURNING id"
)


async def elabora_messaggio(fabbrica: Any, canale: CanalePronto, uidvalidity: int,
                            m: MessaggioCasella, adesso: datetime) -> str:
    """Registra un messaggio della casella e, se è una ricevuta autentica e associata,
    applica il suo effetto al messaggio PEC. Restituisce l'esito registrato."""
    import hashlib

    base: dict[str, Any] = {
        "c": canale.registrato.codice, "v": uidvalidity, "u": m.uid, "mid": None, "t": None,
        "tipo": None, "msgid": None, "ident": None, "gestore": None, "consegna": None,
        "errore": None, "esteso": None, "data": None, "cert": None, "sogg": None,
        "impronta": hashlib.sha256(m.grezzo).hexdigest() if m.grezzo is not None else None,
        "dim": m.dimensione, "eml": m.grezzo, "a": adesso, "motivo": None}
    if m.grezzo is None:
        async with fabbrica() as s:
            await s.execute(text(_REGISTRA), {**base, "esito": "OLTRE_LIMITE",
                                              "motivo": "messaggio oltre il limite di "
                                                        "dimensione del canale: non scaricato"})
        return "OLTRE_LIMITE"
    radici = radici_da_pem(str(canale.registrato.configurazione.get("radici_pem") or ""))
    try:
        ric = leggi_ricevuta(m.grezzo, radici, adesso=adesso)
    except RicevutaNonAutentica as exc:
        async with fabbrica() as s:
            await s.execute(text(_REGISTRA), {**base, "esito": "NON_AUTENTICA",
                                              "motivo": str(exc)[:500]})
        logger.warning("pec_ricevuta_non_autentica", canale=canale.registrato.codice,
                       uid=m.uid, motivo=str(exc)[:200])
        return "NON_AUTENTICA"
    dati = {**base, "tipo": ric.tipo, "msgid": ric.msgid[:500] or None,
            "ident": ric.identificativo or None, "gestore": ric.gestore or None,
            "consegna": ric.consegna or None, "errore": ric.errore or None,
            "esteso": ric.errore_esteso or None, "data": ric.data,
            "cert": ric.firma.certificato_sha256, "sogg": ric.firma.soggetto}
    async with fabbrica() as s:
        messaggio = None
        if ric.tipo != POSTA_CERTIFICATA and ric.msgid:
            messaggio = (await s.execute(text(
                "SELECT id::text AS id, tenant_id::text AS tenant_id, stato, message_id, "
                " destinatario, accettato_il FROM public.pec_messaggi "
                "WHERE message_id = :m AND canale_codice = :c FOR UPDATE"),
                {"m": ric.msgid, "c": canale.registrato.codice})).mappings().first()
        if messaggio is None:
            motivo = ("messaggio PEC in arrivo, non una ricevuta" if ric.tipo == POSTA_CERTIFICATA
                      else "nessun messaggio inviato da questo canale con il msgid firmato")
            await s.execute(text(_REGISTRA), {**dati, "esito": "NON_ASSOCIATA",
                                              "motivo": motivo})
            return "NON_ASSOCIATA"
        motivo = motivo_non_associata(ric, message_id=messaggio["message_id"],
                                      mittente=str(canale.registrato.configurazione.get(
                                          "indirizzo") or ""),
                                      destinatario=messaggio["destinatario"])
        if motivo is not None:
            await s.execute(text(_REGISTRA), {**dati, "esito": "NON_ASSOCIATA",
                                              "motivo": motivo, "t": messaggio["tenant_id"]})
            logger.warning("pec_ricevuta_non_associata", canale=canale.registrato.codice,
                           uid=m.uid, motivo=motivo)
            return "NON_ASSOCIATA"
        inserita = (await s.execute(text(_REGISTRA), {
            **dati, "esito": "ASSOCIATA", "mid": messaggio["id"],
            "t": messaggio["tenant_id"]})).scalar_one_or_none()
        if inserita is None:
            return "GIA_REGISTRATA"
        effetto = dom.effetto_ricevuta(messaggio["stato"], ric.tipo)
        insiemi = ["updated_at = :a"]
        if effetto.nuovo_stato:
            insiemi.append("stato = :stato")
        if effetto.colonna_ora:
            # nome di colonna da un elenco chiuso del dominio (mai dall'esterno)
            insiemi.append(f"{effetto.colonna_ora} = COALESCE({effetto.colonna_ora}, :quando)")
        if effetto.nuovo_stato in (dom.NON_ACCETTATO, dom.MANCATA_CONSEGNA):
            insiemi.append("errore = :errore")
        if len(insiemi) > 1:
            await s.execute(text(
                f"UPDATE public.pec_messaggi SET {', '.join(insiemi)} "  # noqa: S608 — elenco chiuso
                "WHERE id = CAST(:id AS uuid)"),
                {"a": adesso, "stato": effetto.nuovo_stato, "id": messaggio["id"],
                 "quando": ric.data or adesso,
                 "errore": (f"{ric.errore}: {ric.errore_esteso}" if ric.errore_esteso
                            else ric.errore)[:500]})
    logger.info("pec_ricevuta_associata", canale=canale.registrato.codice, tipo=ric.tipo,
                messaggio_id=messaggio["id"], stato=effetto.nuovo_stato or messaggio["stato"])
    return "ASSOCIATA"


async def _leggi_caselle(fabbrica: Any, pronti: list[CanalePronto], imp: ImpostazioniPec,
                         adesso: datetime, conteggi: dict[str, int]) -> None:
    for p in pronti:
        if p.adattatore is None:
            continue
        uidvalidity, ultimo = await _ultimo_uid(fabbrica, p.registrato.codice)
        try:
            lettura = await p.adattatore.leggi(ultimo, uidvalidity, massimo=imp.lettura_massimo)
        except (ErroreCasella, CanaleNonCostruibile) as exc:
            conteggi["letture_fallite"] += 1
            await scrivi_stato(fabbrica, p, adesso, errore=f"lettura delle ricevute: {exc}")
            logger.warning("pec_lettura_non_riuscita", canale=p.registrato.codice,
                           errore=str(exc)[:200])
            continue
        for m in lettura.messaggi:
            esito = await elabora_messaggio(fabbrica, p, lettura.uidvalidity, m, adesso)
            chiave = {"ASSOCIATA": "ricevute_associate", "NON_AUTENTICA": "non_autentiche"
                      }.get(esito, "altri_letti")
            conteggi[chiave] += 1
        await scrivi_stato(fabbrica, p, adesso, lettura=True)


async def giro_pec(imp: ImpostazioniPec, *, adesso: datetime, sessione_factory: Any = None,
                   costruttore: Callable[..., CanalePec] = costruisci) -> dict[str, int]:
    """Un giro del lavoratore: stato dei canali, invio, lettura delle ricevute."""
    fabbrica = sessione_factory or _fabbrica_conservatore(
        "lavoratore della posta: PEC di tutti gli enti")
    conteggi = dict.fromkeys(("inviati", "riprogrammati", "non_partiti", "recuperati",
                              "riconciliati", "ricevute_associate", "non_autentiche", "altri_letti",
                              "letture_fallite"), 0)
    pronti = prepara(await canali_attivi(fabbrica), imp, costruttore)
    for p in pronti:
        await scrivi_stato(fabbrica, p, adesso)
    await _invia(fabbrica, pronti, imp, adesso, conteggi)
    await _leggi_caselle(fabbrica, pronti, imp, adesso, conteggi)
    return conteggi


def nuovo_id() -> str:
    return str(uuid.uuid4())


__all__ = ["CanalePronto", "ImpostazioniPec", "accoda_comunicazione", "canali_attivi",
           "casi_uso_ente", "elabora_messaggio", "giro_pec", "inserisci", "message_id",
           "prepara"]
