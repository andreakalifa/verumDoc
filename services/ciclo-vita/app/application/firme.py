# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
app/application/firme.py — verifica delle firme dei documenti del ciclo di vita
con l'interfaccia dei verificatori intercambiabili (R08: D01 e D22; VD-C-073,
VD-C-120, VD-C-141).

Documenti con firma:
  - AUTORIZZAZIONE_PRODUTTORE (scarto): firma SEMPRE richiesta e verificata;
  - NULLA_OSTA (scarto, enti PA): firma verificata se presente; se manca vale
    ``NULLA_OSTA_FIRMA`` dell'ente: AVVISO (accettato con avviso, predefinito
    D22) o BLOCCANTE (rifiutato; il conservatore lo impone per tutti o per ente);
  - RICHIESTA_ANNULLAMENTO: documento firmato che cita il versamento,
    ``ANNULLAMENTO_RICHIESTA_FIRMATA`` FACOLTATIVA (predefinito) o OBBLIGATORIA.

Il livello è ``CICLO_VITA_FIRME_LIVELLO`` (CRITTOGRAFICA, predefinito, o
CON_REVOCHE). Il giudizio è UNA funzione (``problemi_documento``) usata dall'API
al caricamento (rifiuto immediato di una firma non valida) e dal LAVORATORE,
che riverifica sui byte riletti dal WORM prima di cancellare o di scrivere il
fatto: è il lavoratore il controllo di sicurezza (l'API può essere compromessa).

Revisione R08:
  - X1: ``problemi_firme`` è rigorosa: una firma INDETERMINATE non è mai valida
    nel ciclo di vita (nessuna concessione del conservatore);
  - X3 (VD-P-76): oltre alla firma valida serve il FIRMATARIO AUTORIZZATO:
    almeno una firma del documento deve essere di un titolare il cui codice
    fiscale (serialNumber «TINIT-…» del certificato) o la cui impronta SHA-256
    del certificato è nell'elenco che il conservatore configura per l'ente
    (``CICLO_VITA_FIRMATARI_AUTORIZZATI`` per autorizzazione e richiesta di
    annullamento, ``CICLO_VITA_FIRMATARI_NULLA_OSTA`` per il nulla osta);
  - X12: il riferimento al versamento si cerca SOLO nel contenuto firmato: per i
    PAdES nei byte coperti dal /ByteRange dell'ultima firma, che deve coprire
    l'intero file (nessuna modifica dopo la firma); per i CAdES nel contenuto
    incapsulato;
  - X14: una firma staccata (.p7s) non porta il documento firmato: l'esito lo
    dice in parole e il documento si rifiuta (caricare il .p7m o il PDF firmato).

R12a (VD-C-145): l'autorizzazione allo scarto e il nulla osta devono citare
l'identificativo della PRATICA nel contenuto firmato (``problemi_riferimento_pratica``,
stesse regole X12/Y2), al caricamento e nella riverifica del lavoratore, che
rifiuta anche un documento con la stessa impronta già usato in un'altra pratica.
R12b (VD-C-160): lo stesso legame vale per l'autorizzazione del produttore alla
RESTITUZIONE (riversamento in uscita verso l'ente a fine contratto): pratiche dei
tipi ``TIPI_PRATICA_LEGATI``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain import stati as st
from libs.shared.firme import (
    LivelloVerifica,
    rileva,
    verifica_al_livello,
    verificatore,
)
from libs.verumdoc_rules.firme import esito_in_parole, problemi_firme
from libs.verumdoc_rules.parametri_servizio import (
    ANNULLAMENTO_RICHIESTA_FIRMATA,
    CICLO_VITA_FIRMATARI_AUTORIZZATI,
    CICLO_VITA_FIRMATARI_NULLA_OSTA,
    CICLO_VITA_FIRME_LIVELLO,
    FIRMA_BLOCCANTE,
    NULLA_OSTA_FIRMA,
    RICHIESTA_OBBLIGATORIA,
)

#: Allegati la cui firma si verifica (caricamento e lavoratore).
ALLEGATI_CON_FIRMA = frozenset({st.ALLEGATO_AUTORIZZAZIONE, st.ALLEGATO_NULLA_OSTA,
                                st.ALLEGATO_RICHIESTA_ANNULLAMENTO})
CODICI = (NULLA_OSTA_FIRMA, CICLO_VITA_FIRME_LIVELLO, ANNULLAMENTO_RICHIESTA_FIRMATA,
          CICLO_VITA_FIRMATARI_AUTORIZZATI, CICLO_VITA_FIRMATARI_NULLA_OSTA)


@dataclass(frozen=True)
class ParametriFirme:
    livello: LivelloVerifica
    nulla_osta_bloccante: bool
    annullamento_obbligatoria: bool
    #: X3: codici fiscali o impronte SHA-256 dei certificati ammessi
    firmatari_autorizzati: frozenset[str] = frozenset()
    firmatari_nulla_osta: frozenset[str] = frozenset()

    def come_dict(self) -> dict[str, Any]:
        return {"livello": self.livello.value, "nulla_osta_bloccante": self.nulla_osta_bloccante,
                "annullamento_obbligatoria": self.annullamento_obbligatoria,
                "firmatari_autorizzati": len(self.firmatari_autorizzati),
                "firmatari_nulla_osta": len(self.firmatari_nulla_osta)}


def _elenco(valore: Any) -> frozenset[str]:
    return frozenset(_normalizza(str(v)) for v in valore or ())


def _normalizza(codice: str) -> str:
    """CF maiuscolo; impronta SHA-256 minuscola senza il prefisso «C-» di EU DSS."""
    c = codice.strip()
    if c[:2].upper() == "C-":
        c = c[2:]
    return c.lower() if len(c) == 64 else c.upper()


def da_valori(valori: Mapping[str, Any]) -> ParametriFirme:
    return ParametriFirme(LivelloVerifica(str(valori[CICLO_VITA_FIRME_LIVELLO])),
                          valori[NULLA_OSTA_FIRMA] == FIRMA_BLOCCANTE,
                          valori[ANNULLAMENTO_RICHIESTA_FIRMATA] == RICHIESTA_OBBLIGATORIA,
                          _elenco(valori.get(CICLO_VITA_FIRMATARI_AUTORIZZATI)),
                          _elenco(valori.get(CICLO_VITA_FIRMATARI_NULLA_OSTA)))


async def parametri_firme(tenant_id: Any, *, session: Any = None,
                          rigoroso: bool = False) -> ParametriFirme:
    """Parametri dell'ente. ``rigoroso`` (lavoratore): un errore del database si
    propaga (si ritenta) invece di valere come predefinito."""
    from libs.shared.parametri import leggi_livelli, parametri, risolvi

    if rigoroso:
        return da_valori(risolvi(await leggi_livelli(CODICI, tenant_id, session=session), CODICI))
    return da_valori(await parametri(CODICI, tenant_id, session=session))


async def verifica_documento(contenuto: bytes, nome_file: str,
                             livello: LivelloVerifica) -> dict[str, Any]:
    """Esito normalizzato (senza rapporto grezzo, che resta nei log del
    verificatore) con il riassunto in parole. ``ErroreVerificatoreNonDisponibile``
    si propaga."""
    from app.config import settings

    esito = await verifica_al_livello(verificatore(settings.ambiente_firme()), contenuto, livello,
                                      nome_file=nome_file)
    assert esito is not None  # il livello del ciclo di vita non è mai NESSUNA
    dati = esito.come_dict()
    dati.pop("rapporto_grezzo", None)
    dati["in_parole"] = esito_in_parole(dati)
    dati["verificata"] = True
    return dati


def solo_rilevata(contenuto: bytes, nome_file: str, motivo: str) -> dict[str, Any]:
    """Esito di ripiego al caricamento se il verificatore non risponde: si rileva
    soltanto e il lavoratore verificherà (``verificata`` False)."""
    dati = rileva(contenuto, nome_file).come_dict()
    dati.pop("rapporto_grezzo", None)
    dati.update({"in_parole": "da verificare", "verificata": False,
                 "nota": f"verificatore non disponibile al caricamento ({motivo}): la firma "
                         "sarà verificata dal lavoratore prima dell'esecuzione"})
    return dati


def firmato(esito: Mapping[str, Any] | None) -> bool:
    return bool(esito) and esito.get("formato") not in (None, "NESSUNO")  # type: ignore[union-attr]


def _ammesso(cert: Mapping[str, Any] | None, ammessi: frozenset[str]) -> bool:
    cert = cert or {}
    return any(codice and _normalizza(str(codice)) in ammessi
               for codice in (cert.get("codice_fiscale"), cert.get("impronta_sha256")))


def firmatario_autorizzato(esito: Mapping[str, Any], ammessi: frozenset[str]) -> bool:
    """Il documento è firmato da firmatari ammessi (CF o impronta).

    Revisione Y2: per i PAdES OGNI firma deve essere di un firmatario ammesso
    (una firma successiva di un estraneo potrebbe aggiungere testo, es. il
    /Reason, coperto dall'ultima revisione); per i CAdES i SignerInfo firmano
    tutti lo stesso contenuto e basta che uno sia ammesso."""
    lista = list(esito.get("firme") or ())
    if not lista:
        return False
    if esito.get("formato") == "PAdES":
        return all(_ammesso(f.get("certificato"), ammessi) for f in lista)
    return any(_ammesso(f.get("certificato"), ammessi) for f in lista)


def problemi_documento(tipo: str, esito: Mapping[str, Any] | None,
                       parametri: ParametriFirme) -> tuple[list[str], str | None]:
    """(problemi bloccanti, avviso) della firma di un documento del ciclo di vita.
    Una firma presente ma NON valida per il livello è sempre un problema, anche
    dove la firma è facoltativa (segnale di alterazione). X3: una firma valida di
    un titolare NON autorizzato è un problema (per il nulla osta: secondo
    ``NULLA_OSTA_FIRMA``)."""
    if tipo not in ALLEGATI_CON_FIRMA:
        return [], None
    if not firmato(esito):
        if tipo == st.ALLEGATO_NULLA_OSTA and not parametri.nulla_osta_bloccante:
            return [], "nulla osta senza firma: accettato con avviso (NULLA_OSTA_FIRMA = AVVISO)"
        return [f"il documento {tipo} deve essere firmato (CAdES .p7m o PDF con firma PAdES)"], \
            None
    if esito is not None and esito.get("verificata") is False:
        return [], None  # solo rilevata al caricamento: decide il lavoratore
    assert esito is not None
    problemi = problemi_firme(esito, parametri.livello.value)
    if problemi:
        return problemi, None
    nulla_osta = tipo == st.ALLEGATO_NULLA_OSTA
    ammessi = parametri.firmatari_nulla_osta if nulla_osta else parametri.firmatari_autorizzati
    if firmatario_autorizzato(esito, ammessi):
        return [], None
    codice = CICLO_VITA_FIRMATARI_NULLA_OSTA if nulla_osta else CICLO_VITA_FIRMATARI_AUTORIZZATI
    firmatari = ", ".join(str(f.get("firmatario") or "?") for f in esito.get("firme") or ()
                          if not _ammesso(f.get("certificato"), ammessi))
    motivo = (f"firmatario non autorizzato ({firmatari}): il codice fiscale o l'impronta del "
              f"certificato non è nell'elenco {codice} configurato dal conservatore"
              + ("" if ammessi else " (elenco vuoto)"))
    if nulla_osta and not parametri.nulla_osta_bloccante:
        return [], f"nulla osta: {motivo}; accettato con avviso (NULLA_OSTA_FIRMA = AVVISO)"
    return [motivo], None


#: R12a (VD-C-145): documenti dello SCARTO che devono citare l'identificativo della
#: pratica nel contenuto firmato (stesse regole X12/Y2 della richiesta di annullamento)
ALLEGATI_LEGATI_ALLA_PRATICA = frozenset({st.ALLEGATO_AUTORIZZAZIONE, st.ALLEGATO_NULLA_OSTA})
#: Tipi di pratica i cui documenti autorizzativi sono legati alla pratica: scarto
#: (R12a, VD-C-145) e restituzione (R12b, VD-C-160: l'unico riversamento in uscita
#: con l'autorizzazione del produttore nella macchina a stati)
#: R13b (VD-C-162): anche il riversamento in uscita quando richiede l'autorizzazione
#: del produttore (solo allora l'allegato AUTORIZZAZIONE_PRODUTTORE è previsto)
TIPI_PRATICA_LEGATI = frozenset({st.SCARTO, st.RESTITUZIONE, st.RIVERSAMENTO_USCITA})
#: Testo dell'avviso per le pratiche aperte prima dell'obbligo (elenco delle
#: migrazioni 20261104_051 per lo scarto e 20261106_053 per la restituzione)
AVVISO_TRANSIZIONE = "pratica aperta prima dell'obbligo del riferimento, accettato con avviso"


def problemi_riferimento_pratica(tipo: str, contenuto: bytes, esito: Mapping[str, Any] | None,
                                 pratica_id: Any, parametri: ParametriFirme, *,
                                 transizione: bool = False) -> tuple[list[str], str | None]:
    """(problemi bloccanti, avviso) del legame fra il documento e la pratica (VD-C-145).

    Si chiama DOPO ``problemi_documento`` senza problemi. L'autorizzazione allo
    scarto e il nulla osta firmati da firmatari ammessi devono riportare
    l'identificativo della pratica NEL CONTENUTO FIRMATO da quei firmatari
    (``contiene_riferimento``: PAdES con l'ultima firma che copre tutto il file e
    tutte le firme ammesse; CAdES con un SignerInfo ammesso): un documento
    autentico di un'altra pratica non si riusa. Un nulla osta già accettato con
    avviso (senza firma o di un firmatario non in elenco con ``NULLA_OSTA_FIRMA``
    = AVVISO) non ha contenuto firmato da controllare: resta l'avviso esistente.
    ``transizione``: pratica aperta prima dell'obbligo (elenco delle migrazioni
    20261104_051 e 20261106_053): l'assenza del riferimento è un avviso, non un
    rifiuto. R12b (VD-C-160): vale anche per l'autorizzazione della restituzione."""
    if tipo not in ALLEGATI_LEGATI_ALLA_PRATICA:
        return [], None
    if esito is not None and esito.get("verificata") is False:
        return [], None  # solo rilevata al caricamento: decide il lavoratore
    nulla_osta = tipo == st.ALLEGATO_NULLA_OSTA
    ammessi = parametri.firmatari_nulla_osta if nulla_osta else parametri.firmatari_autorizzati
    if nulla_osta and not parametri.nulla_osta_bloccante and not (
            firmato(esito) and esito is not None and firmatario_autorizzato(esito, ammessi)):
        return [], None
    if contiene_riferimento(contenuto, str(pratica_id), ammessi=ammessi):
        return [], None
    motivo = (f"il documento {tipo} non riporta l'identificativo della pratica {pratica_id} "
              "nel contenuto firmato da un firmatario autorizzato")
    if transizione:
        return [], f"{motivo}: {AVVISO_TRANSIZIONE}"
    return [motivo], None


def _cert_ammesso(cert: Any, ammessi: frozenset[str]) -> bool:
    """Certificato asn1crypto di una firma nell'elenco dei firmatari ammessi."""
    from libs.shared.firme.rilevazione import certificato

    if cert is None:
        return False
    return _ammesso(certificato(cert).come_dict(), ammessi)


def _revisione_firmata_pades(contenuto: bytes, ammessi: frozenset[str]) -> list[bytes] | None:
    """Parti del PDF coperte dal /ByteRange dell'ULTIMA firma, solo se coprono
    l'intero file (nessun aggiornamento dopo la firma) e se TUTTE le firme del
    PDF sono di firmatari ammessi (Y2: una firma di un estraneo dopo quella
    autorizzata potrebbe introdurre il riferimento); None altrimenti."""
    import io

    from pyhanko.pdf_utils.reader import PdfFileReader

    try:
        firme_pdf = PdfFileReader(io.BytesIO(contenuto), strict=False).embedded_signatures
        if not firme_pdf:
            return None
        if not all(_cert_ammesso(f.signer_cert, ammessi) for f in firme_pdf):
            return None
        intervallo = [int(x) for x in firme_pdf[-1].sig_object["/ByteRange"]]
    except Exception:  # noqa: BLE001 — PDF o firma illeggibili: nessun contenuto firmato
        return None
    if len(intervallo) != 4:
        return None
    a, b, c, d = intervallo
    if a != 0 or c + d != len(contenuto) or not 0 < b <= c:
        return None
    return [contenuto[a:a + b], contenuto[c:c + d]]


def contiene_riferimento(contenuto: bytes, riferimento: str, *,
                         ammessi: frozenset[str]) -> bool:
    """La richiesta firmata cita il versamento NEL CONTENUTO FIRMATO DA UN
    FIRMATARIO AMMESSO (X12, Y2): per un PDF la revisione coperta dall'ultima
    firma, che deve coprire l'intero file, con TUTTE le firme di firmatari
    ammessi; per una busta CAdES il contenuto incapsulato, con almeno un
    SignerInfo ammesso e senza buste annidate (un p7m dentro un p7m firmato
    all'esterno da un estraneo non vale). Testo non compresso."""
    from asn1crypto import cms

    from libs.shared.firme.rilevazione import _firmatari_cms, _sembra_der_cms, decodifica_cms

    chiave = riferimento.encode("ascii")
    if contenuto.startswith(b"%PDF-"):
        parti = _revisione_firmata_pades(contenuto, ammessi)
        return parti is not None and any(chiave in p for p in parti)
    try:
        ci = cms.ContentInfo.load(decodifica_cms(contenuto))
        sd = ci["content"]
        dati = sd["encap_content_info"]["content"].native
        firmatari = [c for c, _ in _firmatari_cms(sd)]
    except Exception:  # noqa: BLE001 — non è una busta CAdES leggibile
        return False
    if not isinstance(dati, bytes) or _sembra_der_cms(dati) or b"-----BEGIN" in dati[:64]:
        return False
    if not any(_cert_ammesso(c, ammessi) for c in firmatari):
        return False
    return chiave in dati


__all__ = ["ALLEGATI_CON_FIRMA", "ALLEGATI_LEGATI_ALLA_PRATICA", "AVVISO_TRANSIZIONE",
           "ParametriFirme", "TIPI_PRATICA_LEGATI", "contiene_riferimento", "da_valori",
           "firmatario_autorizzato", "firmato", "parametri_firme", "problemi_documento",
           "problemi_riferimento_pratica",
           "solo_rilevata", "verifica_documento"]
