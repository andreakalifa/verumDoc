# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Lettura delle ricevute PEC e associazione al messaggio inviato (R13b, VD-C-078).

Riferimenti: DM 2/11/2005 (regole tecniche PEC), allegato «Formato dei messaggi»:
ricevute e avvisi con intestazioni ``X-Ricevuta`` (accettazione,
non-accettazione, presa-in-carico, avvenuta-consegna, errore-consegna,
preavviso-errore-consegna, rilevazione-virus), ``X-Riferimento-Message-ID`` e
l'allegato ``daticert.xml`` (elemento ``postacert`` con ``tipo``, ``errore``,
``intestazione`` e ``dati``: gestore emittente, data, identificativo, msgid del
messaggio originale, consegna, errore esteso), il tutto firmato S/MIME dal
gestore. Il dettaglio dei formati dei singoli gestori (ricevuta completa, breve,
sintetica; codifiche della data) resta DA VERIFICARE con il gestore scelto
(checklist in ``docs/integrazione/pec.md``).

Regola di sicurezza: si decide SOLO sui dati firmati (``daticert.xml`` parte diretta
del ``multipart/mixed`` firmato, mai dentro un messaggio allegato). ``X-Ricevuta`` e
``X-Riferimento-Message-ID`` sono obbligatorie nelle ricevute e devono coincidere con i
dati firmati; le buste di anomalia (``X-Trasporto: errore``) non sono mai ricevute.
Altrimenti la ricevuta è respinta (``RicevutaNonAutentica``); una ricevuta
autentica che non corrisponde al messaggio (msgid, mittente, destinatario) non
cambia lo stato di nessun messaggio (``motivo_non_associata``).
"""
from __future__ import annotations

import email
import email.parser
import email.policy
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import Message

from cryptography import x509
from defusedxml import ElementTree

from libs.shared.pec.smime import (
    FirmaVerificata,
    RicevutaNonAutentica,
    separa_multipart_signed,
    verifica_firma,
)

ACCETTAZIONE = "ACCETTAZIONE"
NON_ACCETTAZIONE = "NON_ACCETTAZIONE"
PRESA_IN_CARICO = "PRESA_IN_CARICO"
AVVENUTA_CONSEGNA = "AVVENUTA_CONSEGNA"
ERRORE_CONSEGNA = "ERRORE_CONSEGNA"
PREAVVISO_ERRORE_CONSEGNA = "PREAVVISO_ERRORE_CONSEGNA"
RILEVAZIONE_VIRUS = "RILEVAZIONE_VIRUS"
#: Busta di trasporto: un messaggio PEC in ARRIVO (non una ricevuta)
POSTA_CERTIFICATA = "POSTA_CERTIFICATA"
#: Valore di ``X-Ricevuta`` / ``postacert@tipo`` → tipo interno
TIPI: dict[str, str] = {
    "accettazione": ACCETTAZIONE,
    "non-accettazione": NON_ACCETTAZIONE,
    "presa-in-carico": PRESA_IN_CARICO,
    "avvenuta-consegna": AVVENUTA_CONSEGNA,
    "errore-consegna": ERRORE_CONSEGNA,
    "preavviso-errore-consegna": PREAVVISO_ERRORE_CONSEGNA,
    "rilevazione-virus": RILEVAZIONE_VIRUS,
    "posta-certificata": POSTA_CERTIFICATA,
}
#: Ricevute che riguardano un destinatario preciso (``dati/consegna``)
PER_DESTINATARIO = frozenset({AVVENUTA_CONSEGNA, ERRORE_CONSEGNA, PREAVVISO_ERRORE_CONSEGNA,
                              RILEVAZIONE_VIRUS, PRESA_IN_CARICO})
DATICERT_MASSIMO_BYTE = 64 * 1024
TIPI_CONTENUTO_DATICERT = frozenset({"application/xml", "text/xml"})
#: ``X-Trasporto`` delle buste di trasporto; ``errore`` = busta di anomalia (mai una ricevuta)
TRASPORTO_ANOMALIA = "errore"


@dataclass(frozen=True)
class Ricevuta:
    tipo: str
    msgid: str
    identificativo: str
    gestore: str
    mittente: str
    destinatari: tuple[str, ...]
    consegna: str
    errore: str
    errore_esteso: str
    data: datetime | None
    oggetto: str
    firma: FirmaVerificata
    impronta_sha256: str
    tipo_ricevuta: str = ""
    avvisi: tuple[str, ...] = field(default_factory=tuple)


def _normalizza_msgid(valore: str) -> str:
    v = (valore or "").strip()
    if v and not v.startswith("<"):
        v = f"<{v}>"
    return v


def _testo(nodo: object | None) -> str:
    if nodo is None:
        return ""
    return " ".join(str(getattr(nodo, "text", "") or "").split())[:1000]


def _data(nodo: object | None) -> datetime | None:
    """``<data zona="+0100"><giorno>gg/mm/aaaa</giorno><ora>hh:mm:ss</ora></data>``."""
    if nodo is None:
        return None
    giorno = _testo(nodo.find("giorno"))  # type: ignore[attr-defined]
    ora = _testo(nodo.find("ora"))  # type: ignore[attr-defined]
    zona = str(nodo.get("zona") or "+0000").strip()  # type: ignore[attr-defined]
    try:
        base = datetime.strptime(f"{giorno} {ora}", "%d/%m/%Y %H:%M:%S")
        segno = -1 if zona.startswith("-") else 1
        cifre = zona.lstrip("+-")
        scarto = timedelta(hours=int(cifre[:2]), minutes=int(cifre[2:4] or 0))
        return base.replace(tzinfo=timezone(segno * scarto))
    except (ValueError, IndexError):
        return None


def _daticert(contenuto_firmato: bytes) -> bytes:
    """Byte di ``daticert.xml``: parte DIRETTA (uno solo) del ``multipart/mixed`` firmato.

    Mai dentro un ``message/rfc822`` allegato: le buste di anomalia del gestore sono
    firmate da lui ma contengono il messaggio di un terzo, che può portare un proprio
    ``daticert.xml`` (revisione R13b, rilievo 1)."""
    parte: Message = email.message_from_bytes(contenuto_firmato, policy=email.policy.compat32)
    if parte.get_content_type() != "multipart/mixed" or not parte.is_multipart():
        raise RicevutaNonAutentica("la parte firmata non è multipart/mixed")
    figli = parte.get_payload()
    if not isinstance(figli, list):
        raise RicevutaNonAutentica("la parte firmata non è multipart/mixed")
    trovati = [p for p in figli
               if isinstance(p, Message) and not p.is_multipart()
               and (p.get_filename() or "").strip().lower() == "daticert.xml"]
    if len(trovati) != 1:
        raise RicevutaNonAutentica("la parte firmata deve contenere un solo daticert.xml "
                                   "come parte diretta")
    if trovati[0].get_content_type() not in TIPI_CONTENUTO_DATICERT:
        raise RicevutaNonAutentica("daticert.xml con tipo di contenuto inatteso")
    dati = trovati[0].get_payload(decode=True)
    if not isinstance(dati, bytes) or not dati or len(dati) > DATICERT_MASSIMO_BYTE:
        raise RicevutaNonAutentica("daticert.xml vuoto o troppo grande")
    return dati


def leggi_ricevuta(grezzo: bytes, radici: list[x509.Certificate], *,
                   adesso: datetime | None = None) -> Ricevuta:
    """Ricevuta autentica dal messaggio grezzo (RFC 5322) o ``RicevutaNonAutentica``."""
    parti = separa_multipart_signed(grezzo)
    firma = verifica_firma(parti.contenuto, parti.firma_der, radici, adesso=adesso)
    esterna = email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(grezzo)
    x_trasporto = str(esterna.get("X-Trasporto") or "").strip().lower()
    if x_trasporto == TRASPORTO_ANOMALIA:
        raise RicevutaNonAutentica("busta di anomalia (X-Trasporto: errore), non una ricevuta")
    try:
        radice = ElementTree.fromstring(_daticert(parti.contenuto),
                                        forbid_dtd=False, forbid_entities=True,
                                        forbid_external=True)
    except RicevutaNonAutentica:
        raise
    except Exception:  # noqa: BLE001 — XML non valido o costrutti vietati
        raise RicevutaNonAutentica("daticert.xml non leggibile") from None
    if radice.tag != "postacert":
        raise RicevutaNonAutentica("daticert.xml senza elemento postacert")
    tipo_firmato = str(radice.get("tipo") or "").strip().lower()
    if tipo_firmato not in TIPI:
        raise RicevutaNonAutentica(f"tipo di ricevuta sconosciuto: {tipo_firmato[:40]}")
    intestazione = radice.find("intestazione")
    dati = radice.find("dati")
    if intestazione is None or dati is None:
        raise RicevutaNonAutentica("daticert.xml senza intestazione o dati")
    msgid = _normalizza_msgid(_testo(dati.find("msgid")))
    x_ricevuta = str(esterna.get("X-Ricevuta") or "").strip().lower()
    x_rif = _normalizza_msgid(str(esterna.get("X-Riferimento-Message-ID") or ""))
    tipo = TIPI[tipo_firmato]
    if tipo == POSTA_CERTIFICATA:
        # busta di trasporto di un messaggio in arrivo: mai una ricevuta
        if x_ricevuta:
            raise RicevutaNonAutentica("X-Ricevuta su una busta di trasporto")
    else:
        # ricevuta: intestazioni non firmate OBBLIGATORIE e uguali ai dati firmati
        if x_ricevuta != tipo_firmato:
            raise RicevutaNonAutentica("X-Ricevuta assente o diversa dal tipo firmato in "
                                       "daticert.xml")
        if not msgid:
            raise RicevutaNonAutentica("ricevuta senza msgid del messaggio originale")
        if x_rif != msgid:
            raise RicevutaNonAutentica("X-Riferimento-Message-ID assente o diverso dal msgid "
                                       "firmato")
    ricevuta_el = dati.find("ricevuta")
    return Ricevuta(
        tipo=tipo, msgid=msgid,
        identificativo=_testo(dati.find("identificativo"))[:300],
        gestore=_testo(dati.find("gestore-emittente"))[:300],
        mittente=_testo(intestazione.find("mittente")).lower()[:320],
        destinatari=tuple(_testo(d).lower()[:320] for d in intestazione.findall("destinatari")),
        consegna=_testo(dati.find("consegna")).lower()[:320],
        errore=str(radice.get("errore") or "nessuno").strip()[:40],
        errore_esteso=_testo(dati.find("errore-esteso"))[:500],
        data=_data(dati.find("data")),
        oggetto=_testo(intestazione.find("oggetto"))[:300],
        firma=firma,
        impronta_sha256=hashlib.sha256(grezzo).hexdigest(),
        tipo_ricevuta=str(ricevuta_el.get("tipo") or "") if ricevuta_el is not None else "",
    )


def motivo_non_associata(ricevuta: Ricevuta, *, message_id: str, mittente: str,
                         destinatario: str) -> str | None:
    """None se la ricevuta (autentica) riguarda proprio questo messaggio: stesso
    msgid firmato, mittente = casella del canale, destinatario fra quelli firmati
    (o la consegna, per le ricevute per destinatario). Altrimenti il motivo."""
    if ricevuta.tipo == POSTA_CERTIFICATA:
        return "messaggio PEC in arrivo, non una ricevuta"
    if not message_id or ricevuta.msgid != _normalizza_msgid(message_id):
        return "msgid della ricevuta diverso dal messaggio"
    if ricevuta.mittente != (mittente or "").strip().lower():
        return "mittente firmato diverso dalla casella del canale"
    dest = (destinatario or "").strip().lower()
    if ricevuta.tipo in PER_DESTINATARIO:
        if ricevuta.consegna != dest:
            return "destinatario della consegna diverso da quello del messaggio"
    elif ricevuta.destinatari and dest not in ricevuta.destinatari:
        return "destinatario del messaggio assente dalla ricevuta"
    return None


__all__ = [
    "ACCETTAZIONE", "AVVENUTA_CONSEGNA", "ERRORE_CONSEGNA", "NON_ACCETTAZIONE",
    "PER_DESTINATARIO", "POSTA_CERTIFICATA", "PREAVVISO_ERRORE_CONSEGNA", "PRESA_IN_CARICO",
    "RILEVAZIONE_VIRUS", "Ricevuta", "RicevutaNonAutentica", "TIPI", "leggi_ricevuta",
    "motivo_non_associata",
]
