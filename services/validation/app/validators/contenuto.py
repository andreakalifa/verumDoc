# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Passi 10 e 11 della verifica: riconoscimento del formato sul contenuto intero e
verifica delle firme (R08, decisioni D02 e D01; VD-C-073, VD-C-074).

Entrambi lavorano su una COPIA LOCALE del file (cartella temporanea della
verifica, ``ctx.cartella``), fatta una volta e riusata, entro
``VERIFICA_CONTENUTO_MAX_MB``. Si eseguono dopo il controllo delle impronte e
della dimensione: i byte sono quelli versati e il file non supera il limite.

Passo 10 — formato (``formato_riconosciuto``):
  - identificazione PRONOM (Siegfried; ripiego per firma binaria) e, per i PDF
    che si dichiarano PDF/A, conformità con veraPDF (ripiego: dichiarazione XMP);
  - un eseguibile riconosciuto dal contenuto → VLD-1001;
  - regole del catalogo dei formati RGL-107 (RIFIUTATO) e RGL-108 (AVVISO, DA
    CONVERTIRE) con gli esiti FOTOGRAFATI nel PdV → bloccanti: VLD-1002;
    avvisi: nei dettagli (``rilievi``) e poi nel PdV e nel RdV.

Passo 11 — firme (``verifica_firme``):
  - livello ``FIRME_LIVELLO_VERIFICA`` fotografato (NESSUNA: nulla da fare);
  - ogni file è verificato dal verificatore configurato
    (``libs.shared.firme``: EU DSS con ripiego pyHanko, o pyHanko) e l'esito
    normalizzato entra nel RAPPORTO DI VERIFICA (``dettagli.rapporto``), che il
    costruttore del pacchetto conserva nel PdA e il RdV cita;
  - RGL-109 con l'effetto fotografato: bloccanti → VLD-1101; avvisi nei dettagli;
  - verificatore non raggiungibile: ``ErroreVerificaTransitorio`` (si ritenta,
    poi DLQ): un servizio giù non rende valida né non valida una firma.

PdV con fotografia dei cataloghi 1 e 2: le regole del contenuto non erano in
vigore al versamento; il passo 10 registra solo l'identificazione e il passo 11
non verifica (REGOLE §8.10).

Revisione R08:
  - X13: la copia locale si confronta con le impronte calcolate al passo 5
    (stessi algoritmi): se il file nello staging è cambiato fra le due letture
    la verifica si interrompe (errore transitorio: si ritenta e il passo 5
    rileva la differenza con l'indice). Formato e firme si verificano così sugli
    stessi byte che si conservano;
  - X11: Siegfried non disponibile o in errore = errore di servizio esplicito
    (``ErroreVerificaTransitorio``), mai ripiego silenzioso sulla firma binaria;
  - X5/X8: un file si invia al verificatore (o alla rilevazione) solo entro
    ``FIRME_DIMENSIONE_MAX_MB`` (allineata al servizio verifica-firme). Oltre, il
    formato della firma si rileva dai primi e ultimi byte (per i PDF, revisione
    Y3, «/ByteRange» cercato a blocchi in tutto il file): non firmato →
    nessun rilievo; firmato → esito «firme non verificate» (``limite_superato``):
    nessun rifiuto al livello RILEVATA, rifiuto (RGL-109) da CRITTOGRAFICA. Le
    verifiche pesanti sono al più ``FIRME_CONCORRENZA`` per processo e il
    parsing (pyHanko, rilevazione) gira in un thread con un tempo massimo;
  - X14: una firma staccata ``<nome>.p7s`` si verifica con il documento
    ``<nome>`` dello stesso PdV come contenuto firmato; se il documento non c'è
    l'esito lo dice in parole (firma staccata non verificabile).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.lettura_file import FileOltreLimite, calcolatori
from app.validators.base import (
    BaseValidator,
    ErroreVerificaTransitorio,
    ValidationContext,
    ValidationResult,
    atto_di_sistema,
)
from libs.shared.firme import (
    ErroreVerificatoreNonDisponibile,
    LivelloVerifica,
    verifica_al_livello,
    verificatore,
)
from libs.shared.firme.esito import FormatoFirma, esito_oltre_limite
from libs.shared.firme.rilevazione import (
    MARCATORE_PADES,
    PARZIALE_BYTE,
    cerca_in_blocchi,
    rileva_formato_firma_parziale,
)
from libs.shared.formati import RiconoscitoreNonDisponibile, strumenti
from libs.verumdoc_rules.catalogo_formati import esito_formato
from libs.verumdoc_rules.catalogo_v3 import CATALOGO_V3, REGISTRO_V3
from libs.verumdoc_rules.firme import FIRME_LIVELLO_VERIFICA, esito_in_parole
from libs.verumdoc_rules.impostazioni import (
    ContestoRisoluzione,
    FornitoreDaDizionari,
    Livello,
    Risolutore,
)
from libs.verumdoc_rules.regole import (
    AmbienteRegole,
    DatiVersamento,
    DocumentoDaValutare,
    EsitoRegole,
    Fase,
)

MIME_SCONOSCIUTO = "application/octet-stream"
VERSIONE_RAPPORTO = 1


def ambiente_contenuto(valori: dict[str, Any]) -> AmbienteRegole:
    """Motore con i valori FOTOGRAFATI (livello del conservatore: nessun livello
    del cliente si riapplica, la fotografia è già il valore effettivo)."""
    return AmbienteRegole(
        Risolutore(REGISTRO_V3, FornitoreDaDizionari({Livello.CONSERVATORE: dict(valori)})),
        ContestoRisoluzione(adesso=datetime.now(UTC)))


async def copia_locale(ctx: ValidationContext, nome: str) -> Path | None:
    """Copia locale del file (una volta per verifica); None se oltre il limite.
    X13: le impronte della copia devono essere quelle del passo 5."""
    if nome in ctx.copie:
        return ctx.copie[nome]
    rif = ctx.riferimenti.get(nome)
    if rif is None or ctx.lettore is None or ctx.cartella is None:
        raise ErroreVerificaTransitorio(f"file {nome!r} non disponibile per la verifica")
    letto = ctx.file_letti.get(nome)
    if letto is None or not letto.impronte:
        raise ErroreVerificaTransitorio(
            f"impronte del passo 5 assenti per {nome!r}: copia locale non confrontabile")
    destinazione = ctx.cartella / f"{len(ctx.copie):04d}"
    try:
        await ctx.lettore.copia_in(rif, destinazione,
                                   limite_byte=settings.verifica_contenuto_max_mb * 1024 * 1024)
    except FileOltreLimite:
        ctx.copie[nome] = None
        return None
    impronte = await asyncio.to_thread(_impronte_file, destinazione, list(letto.impronte))
    if any(impronte[a] != v.lower() for a, v in
           ((a.strip().upper(), v) for a, v in letto.impronte.items())):
        destinazione.unlink(missing_ok=True)
        raise ErroreVerificaTransitorio(
            f"il file {nome!r} nello staging è cambiato dopo il calcolo delle impronte "
            "(passo 5): formato e firme non si verificano su byte diversi")
    ctx.copie[nome] = destinazione
    return destinazione


def _impronte_file(percorso: Path, algoritmi: list[str]) -> dict[str, str]:
    calc = calcolatori(algoritmi)
    with percorso.open("rb") as f:
        while blocco := f.read(1024 * 1024):
            for c in calc.values():
                c.update(blocco)
    return {n: c.hexdigest() for n, c in calc.items()}


def _rilievi(esito: EsitoRegole) -> list[dict[str, Any]]:
    return [{"codice": r.regola, "effetto": r.effetto.value, "campo": r.campo,
             "documento": r.documento, "messaggio": r.messaggio} for r in esito.rilievi]


class Step10FormatoRiconosciuto(BaseValidator):
    step = 10
    nome = "formato_riconosciuto"

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        riconoscitore, pdfa = strumenti(settings.ambiente_formati())
        contenuto = ctx.regole.contenuto if ctx.regole is not None else None
        formati: list[dict[str, Any]] = []
        documenti: list[DocumentoDaValutare] = []
        eseguibili: list[dict[str, str]] = []
        for doc in ctx.indice.get("documenti", []):
            nome = doc.get("nome_file", "")
            rif = ctx.riferimenti.get(nome)
            locale = await copia_locale(ctx, nome)
            riga: dict[str, Any] = {"file": nome, "documento": doc.get("id")}
            puid, mime, conforme = None, rif.formato_rilevato if rif else None, None
            if locale is None:
                riga["identificazione"] = {"riconoscitore": "gateway (firma binaria)",
                                           "mime": mime, "nota": "file oltre il limite della "
                                           "copia locale: formato rilevato al versamento"}
            else:
                try:
                    identificato = await riconoscitore.identifica(locale)
                except RiconoscitoreNonDisponibile as exc:
                    # X11: errore di servizio esplicito, nessun ripiego silenzioso
                    raise ErroreVerificaTransitorio(
                        f"riconoscimento dei formati non disponibile: {exc}") from exc
                riga["identificazione"] = identificato.come_dict()
                if identificato.eseguibile:
                    eseguibili.append({"file": nome, "tipo": identificato.eseguibile})
                puid, mime = identificato.puid, identificato.mime or (
                    None if identificato.identificato else MIME_SCONOSCIUTO)
                if mime == "application/pdf":
                    esito_pdfa = await pdfa.valida(locale)
                    riga["pdfa"] = esito_pdfa.come_dict()
                    conforme = esito_pdfa.conforme
            ctx.identificati[nome] = {"puid": puid, "mime": mime}
            documenti.append(DocumentoDaValutare(
                id=str(doc.get("id")), tipo=str(doc.get("tipo")), nome_file=nome,
                puid=puid, formato_rilevato=mime or MIME_SCONOSCIUTO, pdfa_conforme=conforme,
                atto_di_sistema=atto_di_sistema(ctx, doc)))
            if contenuto is not None:
                riga["catalogo"] = esito_formato(
                    puid=puid, mime=mime, pdfa_conforme=conforme,
                    valore=ambiente_contenuto(contenuto).valore).come_dict()
            formati.append(riga)
        if eseguibili:
            return self.fail(ctx, "VLD-1001", "Eseguibili riconosciuti dal contenuto",
                             {"errori": eseguibili, "formati": formati})
        if contenuto is None:
            return self.ok(ctx, {"formati": formati, "catalogo": "non in vigore al versamento"})
        esito = CATALOGO_V3.valuta_regole(
            ["RGL-107", "RGL-108"], DatiVersamento(str(ctx.indice.get("classe_documentale")),
                                                   tuple(documenti)),
            ambiente_contenuto(contenuto))
        dettagli = {"formati": formati, "rilievi": _rilievi(esito)}
        if esito.blocca:
            return self.fail(ctx, "VLD-1002",
                             "Formato rifiutato dal catalogo dei formati per uno o più file",
                             dettagli)
        return self.ok(ctx, dettagli)


_semaforo: asyncio.Semaphore | None = None


def _verifiche_pesanti() -> asyncio.Semaphore:
    """X8: al più ``FIRME_CONCORRENZA`` file in verifica per processo."""
    global _semaforo
    if _semaforo is None:
        _semaforo = asyncio.Semaphore(settings.firme_concorrenza)
    return _semaforo


def _cerca_locale(percorso: Path) -> bool:
    with percorso.open("rb") as f:
        return cerca_in_blocchi(iter(lambda: f.read(1024 * 1024), b""), MARCATORE_PADES)


def _testa_coda_locale(percorso: Path) -> tuple[bytes, bytes]:
    with percorso.open("rb") as f:
        testa = f.read(PARZIALE_BYTE)
        f.seek(max(0, percorso.stat().st_size - PARZIALE_BYTE))
        return testa, f.read(PARZIALE_BYTE)


class Step11VerificaFirme(BaseValidator):
    step = 11
    nome = "verifica_firme"

    async def _oltre_limite(self, ctx: ValidationContext, nome: str, locale: Path | None,
                            livello: LivelloVerifica, limite_mb: int, adesso: str
                            ) -> dict[str, Any]:
        """X5: file oltre il limite della verifica: formato della firma dai soli
        primi e ultimi byte; non firmato = nessun rilievo."""
        if locale is not None:
            testa, coda = await asyncio.to_thread(_testa_coda_locale, locale)
        else:
            rif = ctx.riferimenti[nome]
            assert ctx.lettore is not None
            testa, coda = await ctx.lettore.testa_coda(rif, PARZIALE_BYTE)
        formato = rileva_formato_firma_parziale(testa, coda, nome)
        if testa.startswith(b"%PDF-") and formato is FormatoFirma.NESSUNO:
            # Y3: per un PDF la firma si cerca in TUTTO il file, a blocchi
            if locale is not None:
                trovato = await asyncio.to_thread(_cerca_locale, locale)
            else:
                assert ctx.lettore is not None
                trovato = await ctx.lettore.contiene(ctx.riferimenti[nome], MARCATORE_PADES)
            if trovato:
                formato = FormatoFirma.PADES
        return esito_oltre_limite(formato, livello, "rilevazione parziale (primi e ultimi byte)",
                                  f"file oltre {limite_mb} MB, limite della verifica delle "
                                  "firme", adesso).come_dict()

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        contenuto = ctx.regole.contenuto if ctx.regole is not None else None
        if contenuto is None:
            return self.ok(ctx, {"livello": LivelloVerifica.NESSUNA.value,
                                 "nota": "livello delle firme non in vigore al versamento"})
        ambiente = ambiente_contenuto(contenuto)
        livello = LivelloVerifica(str(ambiente.valore(FIRME_LIVELLO_VERIFICA)))
        if livello is LivelloVerifica.NESSUNA:
            return self.ok(ctx, {"livello": livello.value})
        verifica = verificatore(settings.ambiente_firme())
        adesso = datetime.now(UTC).isoformat()
        limite_mb = min(settings.firme_dimensione_max_mb, settings.verifica_contenuto_max_mb)
        limite = limite_mb * 1024 * 1024
        nomi = {doc.get("nome_file", "") for doc in ctx.indice.get("documenti", [])}
        righe: list[dict[str, Any]] = []
        documenti: list[DocumentoDaValutare] = []
        for doc in ctx.indice.get("documenti", []):
            nome = doc.get("nome_file", "")
            locale = await copia_locale(ctx, nome)
            riga: dict[str, Any] = {"documento": doc.get("id"), "nome_file": nome}
            if locale is None or locale.stat().st_size > limite:
                esito = await self._oltre_limite(ctx, nome, locale, livello, limite_mb, adesso)
            else:
                originale = await self._originale(ctx, nome, nomi, limite, riga)
                dati = await asyncio.to_thread(locale.read_bytes)
                try:
                    async with _verifiche_pesanti():
                        risultato = await verifica_al_livello(
                            verifica, dati, livello, nome_file=nome, originale=originale,
                            verificato_il=adesso)
                except ErroreVerificatoreNonDisponibile as exc:
                    raise ErroreVerificaTransitorio(str(exc)) from exc
                esito = risultato.come_dict() if risultato is not None else None
            righe.append({**riga, "esito_in_parole": esito_in_parole(esito), "esito": esito})
            documenti.append(DocumentoDaValutare(id=str(doc.get("id")),
                                                 tipo=str(doc.get("tipo")),
                                                 nome_file=nome, firme=esito))
        esito_regole = CATALOGO_V3.valuta(
            DatiVersamento(str(ctx.indice.get("classe_documentale")), tuple(documenti)),
            ambiente, Fase.VERIFICA)
        rapporto = {"versione": VERSIONE_RAPPORTO, "pdv": ctx.pdv_id, "livello": livello.value,
                    "verificatore": getattr(verifica, "nome", "?"), "verificato_il": adesso,
                    "file": righe}
        dettagli = {"livello": livello.value, "rapporto": rapporto,
                    "rilievi": _rilievi(esito_regole)}
        if esito_regole.blocca:
            return self.fail(ctx, "VLD-1101",
                             "Firme insufficienti per il livello di verifica richiesto",
                             dettagli)
        return self.ok(ctx, dettagli)

    async def _originale(self, ctx: ValidationContext, nome: str, nomi: set[str], limite: int,
                         riga: dict[str, Any]) -> bytes | None:
        """X14: per ``<nome>.p7s`` il contenuto firmato è il documento ``<nome>``
        dello stesso PdV (entro il limite della verifica)."""
        if not nome.lower().endswith(".p7s"):
            return None
        firmato = nome[:-4]
        if firmato not in nomi:
            riga["firma_staccata_di"] = None
            return None
        locale = await copia_locale(ctx, firmato)
        if locale is None or locale.stat().st_size > limite:
            riga["firma_staccata_di"] = firmato
            riga["nota"] = "documento firmato oltre il limite della verifica"
            return None
        riga["firma_staccata_di"] = firmato
        return await asyncio.to_thread(locale.read_bytes)


__all__ = ["Step10FormatoRiconosciuto", "Step11VerificaFirme", "ambiente_contenuto",
           "copia_locale"]
