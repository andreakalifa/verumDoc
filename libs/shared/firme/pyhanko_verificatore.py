# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
libs/shared/firme/pyhanko_verificatore.py — verificatore pyHanko + asn1crypto
(R08, D01): CAdES (.p7m attached o detached) e PAdES.

È il ripiego dichiarato per i PDF quando EU DSS non risponde e, dove il
servizio ``verifica-firme`` non è distribuito, il verificatore provato di
CAdES e PAdES. Limiti (dichiarati nell'esito con le sotto-indicazioni):
XAdES e ASiC non supportati (FORMATO_NON_SUPPORTATO); Trusted List UE non
caricata da sé: le trust anchor sono quelle configurate (``FIRME_RADICI_PEM``,
in collaudo la CA di prova locale).

Livelli:
  - CRITTOGRAFICA: impronta, firma, catena fino a una trust anchor; revoche
    in «soft-fail» (si usano quelle disponibili: incluse nel documento,
    fornite al costruttore o, con ``rete=True``, scaricate);
  - CON_REVOCHE: in più lo stato di revoca DEVE essere noto («require»):
    senza informazioni → INDETERMINATE / TRY_LATER.

Ogni errore della libreria su una firma è un esito (TOTAL_FAILED o
INDETERMINATE con il motivo), mai un'eccezione verso il chiamante.

R12a (VD-C-147): la verifica gira in un PROCESSO separato (``forkserver`` con
pyHanko precaricato: avvio di pochi millisecondi) con un tempo massimo e, se
configurato, un limite di memoria (``FIRME_MEMORIA_MAX_MB``); oltre il tempo il
processo si TERMINA (kill) e l'esito è «firme non verificate». Prima la verifica
girava in un thread che, oltre il tempo, continuava da solo occupando CPU e
memoria. Anche un annullamento del chiamante termina il processo.
"""
from __future__ import annotations

import asyncio
import io
import logging
import multiprocessing
from collections.abc import Awaitable, Callable, Iterable, Sequence
from functools import partial
from importlib import metadata
from typing import Any

from asn1crypto import cms, crl, ocsp, pem, x509

import libs.shared.firme.asn1_etsi  # noqa: F401 — R13a: attributi B-LTA leggibili
from libs.shared.firme.esito import (
    SOTTO_CATENA,
    SOTTO_DATI_ASSENTI,
    SOTTO_FIRMA,
    SOTTO_HASH,
    SOTTO_NON_SUPPORTATO,
    SOTTO_REVOCA_ASSENTE,
    SOTTO_REVOCATO_SENZA_POE,
    EsitoVerifica,
    FirmaVerificata,
    FormatoFirma,
    Indicazione,
    LivelloVerifica,
    StatoRevoca,
    esito_illeggibile,
    esito_non_firmato,
    esito_oltre_limite,
    in_parole,
)
from libs.shared.firme.rilevazione import (
    certificato,
    decodifica_cms,
    nome_firmatario,
    rileva,
    rileva_formato_firma,
)

# pyHanko registra con traceback ogni catena non costruibile: per noi è un esito
logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)


def _versione() -> str:
    try:
        return metadata.version("pyHanko")
    except metadata.PackageNotFoundError:  # pragma: no cover
        return "?"


def carica_certificati_pem(testo: bytes) -> list[x509.Certificate]:
    """Certificati da un PEM (anche più di uno) o da un DER singolo."""
    if not testo.strip():
        return []
    if pem.detect(testo):
        return [x509.Certificate.load(der) for tipo, _, der in pem.unarmor(testo, multiple=True)
                if tipo == "CERTIFICATE"]
    return [x509.Certificate.load(testo)]


class VerificatorePyHanko:
    """CAdES e PAdES con pyHanko; trust anchor e revoche come parametri."""

    formati = frozenset({FormatoFirma.CADES, FormatoFirma.PADES})

    def __init__(self, radici: Iterable[x509.Certificate] = (), *,
                 crl_locali: Sequence[bytes] = (), ocsp_locali: Sequence[bytes] = (),
                 rete: bool = False, tempo_max_s: float = 120.0,
                 processo_separato: bool = True, memoria_max_mb: int = 0) -> None:
        self.radici = list(radici)
        self.tempo_max_s = tempo_max_s
        self._crl_der = [bytes(c) for c in crl_locali]
        self._ocsp_der = [bytes(o) for o in ocsp_locali]
        self.crl_locali = [crl.CertificateList.load(c) for c in crl_locali]
        self.ocsp_locali = [ocsp.OCSPResponse.load(o) for o in ocsp_locali]
        self.rete = rete
        self.nome = f"pyhanko {_versione()}"
        #: R12a (VD-C-147): verifica in un processo che si può terminare
        self.processo_separato = processo_separato
        self.memoria_max_mb = memoria_max_mb
        #: pid dell'ultimo processo di verifica (diagnostica e test)
        self.ultimo_pid: int | None = None

    def _contesto(self, modo: str):  # type: ignore[no-untyped-def]
        from pyhanko_certvalidator import ValidationContext

        return ValidationContext(trust_roots=self.radici, allow_fetching=self.rete,
                                 revocation_mode=modo, crls=self.crl_locali,
                                 ocsps=self.ocsp_locali)

    async def verifica(self, contenuto: bytes, *, livello: LivelloVerifica,
                       nome_file: str = "", originale: bytes | None = None,
                       verificato_il: str | None = None) -> EsitoVerifica:
        """Revisione X8: il parsing di pyHanko è sincrono (anche dentro le sue
        funzioni async) e ha un tempo massimo; oltre, l'esito è «firme non
        verificate». R12a (VD-C-147): in un processo separato che oltre il tempo
        (o se il chiamante annulla) si termina; un processo morto (limite di
        memoria) dà lo stesso esito."""
        argomenti = {"contenuto": contenuto, "livello": livello, "nome_file": nome_file,
                     "originale": originale, "verificato_il": verificato_il}

        def oltre(motivo: str) -> EsitoVerifica:
            return esito_oltre_limite(rileva_formato_firma(contenuto, nome_file), livello,
                                      self.nome, motivo, verificato_il)

        if self.processo_separato:
            try:
                return await self._in_processo(argomenti)
            except TimeoutError:
                return oltre(f"tempo massimo della verifica superato ({self.tempo_max_s:.0f} s): "
                             "verifica terminata")
            except ProcessoVerificaTerminato as exc:
                return oltre(str(exc))
        from libs.shared.firme.interfaccia import in_thread

        def esegui() -> EsitoVerifica:
            return asyncio.run(self._verifica(**argomenti))
        try:
            return await in_thread(esegui, tempo_max_s=self.tempo_max_s)
        except TimeoutError:
            return oltre(f"tempo massimo della verifica superato ({self.tempo_max_s:.0f} s)")

    async def _in_processo(self, argomenti: dict[str, Any]) -> EsitoVerifica:
        """Un processo per verifica (forkserver), risultato su una pipe; il processo
        si termina SEMPRE all'uscita (tempo scaduto, errore, annullamento)."""
        ctx = _contesto_processi()
        ricevi, invia = ctx.Pipe(duplex=False)
        processo = ctx.Process(
            target=_verifica_nel_figlio, daemon=True,
            args=(invia, type(self), [c.dump() for c in self.radici], self._crl_der,
                  self._ocsp_der, self.rete, self.memoria_max_mb, argomenti))
        processo.start()
        invia.close()
        self.ultimo_pid = processo.pid
        try:
            if not await asyncio.to_thread(ricevi.poll, self.tempo_max_s):
                raise TimeoutError
            try:
                tipo, valore = await asyncio.to_thread(ricevi.recv)
            except EOFError:
                raise ProcessoVerificaTerminato(
                    "verifica interrotta: il processo è terminato senza esito (limite di "
                    "memoria o errore grave)") from None
        finally:
            if processo.is_alive():
                processo.kill()
            await asyncio.to_thread(processo.join, 10)
            ricevi.close()
        if tipo == "oltre":
            raise ProcessoVerificaTerminato(str(valore))
        if tipo == "errore":
            raise RuntimeError(f"verifica pyHanko fallita: {valore}")
        return valore  # type: ignore[no-any-return]

    async def _verifica(self, contenuto: bytes, *, livello: LivelloVerifica,
                        nome_file: str = "", originale: bytes | None = None,
                        verificato_il: str | None = None) -> EsitoVerifica:
        formato = rileva_formato_firma(contenuto, nome_file)
        if formato is FormatoFirma.NESSUNO:
            return esito_non_firmato(livello, self.nome, verificato_il)
        if formato not in self.formati:
            rilevato = rileva(contenuto, nome_file, verificato_il=verificato_il)
            firme = tuple(FirmaVerificata(
                formato, Indicazione.INDETERMINATE, firmatario=f.firmatario,
                certificato=f.certificato, sottoindicazione=SOTTO_NON_SUPPORTATO,
                motivi=(in_parole(SOTTO_NON_SUPPORTATO) or "",)) for f in rilevato.firme)
            return EsitoVerifica(formato, livello, self.nome, firme,
                                 verificato_il=verificato_il,
                                 motivo=in_parole(SOTTO_NON_SUPPORTATO))
        if formato is FormatoFirma.PADES:
            return await self._pades(contenuto, livello, verificato_il)
        return await self._cades(contenuto, livello, originale, verificato_il)

    # ── CAdES ────────────────────────────────────────────────────────

    async def _cades(self, contenuto: bytes, livello: LivelloVerifica,
                     originale: bytes | None, verificato_il: str | None) -> EsitoVerifica:
        formato = FormatoFirma.CADES
        try:
            ci = cms.ContentInfo.load(decodifica_cms(contenuto))
            if ci["content_type"].native != "signed_data":
                raise ValueError("non è un SignedData")
            sd = ci["content"]
            _ = sd.native  # parsing completo: un DER corrotto è illeggibile
        except Exception as exc:  # noqa: BLE001 — qualunque busta illeggibile
            return esito_illeggibile(formato, livello, self.nome, type(exc).__name__,
                                     verificato_il)
        staccata = sd["encap_content_info"]["content"].native is None
        if staccata and originale is None:
            rilevate = rileva(contenuto, verificato_il=verificato_il).firme
            return EsitoVerifica(formato, livello, self.nome, tuple(FirmaVerificata(
                formato, Indicazione.INDETERMINATE, firmatario=f.firmatario,
                certificato=f.certificato, sottoindicazione=SOTTO_DATI_ASSENTI,
                motivi=(in_parole(SOTTO_DATI_ASSENTI) or "",)) for f in rilevate),
                verificato_il=verificato_il, motivo=in_parole(SOTTO_DATI_ASSENTI))
        firme: list[FirmaVerificata] = []
        grezzi: list[str] = []
        for si in sd["signer_infos"]:
            parziale = cms.SignedData({**{k: sd[k] for k in (
                "version", "digest_algorithms", "encap_content_info", "certificates")},
                "signer_infos": [si]})
            firma, grezzo = await self._valuta(
                formato, livello, partial(self._cms, parziale, staccata, originale))
            firme.append(firma)
            grezzi.append(grezzo)
        return EsitoVerifica(formato, livello, self.nome, tuple(firme),
                             verificato_il=verificato_il, rapporto_grezzo="\n\n".join(grezzi))

    async def _cms(self, sd: cms.SignedData, staccata: bool, originale: bytes | None,
                   modo: str) -> Any:
        from pyhanko.sign.validation import async_validate_cms_signature
        from pyhanko.sign.validation.generic_cms import async_validate_detached_cms
        from pyhanko.sign.validation.status import StandardCMSSignatureStatus

        if staccata:
            assert originale is not None
            return await async_validate_detached_cms(
                originale, sd, signer_validation_context=self._contesto(modo))
        return await async_validate_cms_signature(
            sd, status_cls=StandardCMSSignatureStatus, validation_context=self._contesto(modo))

    # ── PAdES ────────────────────────────────────────────────────────

    async def _pades(self, contenuto: bytes, livello: LivelloVerifica,
                     verificato_il: str | None) -> EsitoVerifica:
        from pyhanko.pdf_utils.reader import PdfFileReader

        formato = FormatoFirma.PADES
        try:
            firme_pdf = PdfFileReader(io.BytesIO(contenuto), strict=False).embedded_signatures
        except Exception as exc:  # noqa: BLE001
            return esito_illeggibile(formato, livello, self.nome, type(exc).__name__,
                                     verificato_il)
        firme: list[FirmaVerificata] = []
        grezzi: list[str] = []
        for emb in firme_pdf:
            firma, grezzo = await self._valuta(formato, livello, partial(self._pdf, emb))
            firme.append(firma)
            grezzi.append(grezzo)
        if not firme:
            return esito_illeggibile(formato, livello, self.nome, "nessuna firma leggibile",
                                     verificato_il)
        return EsitoVerifica(formato, livello, self.nome, tuple(firme),
                             verificato_il=verificato_il, rapporto_grezzo="\n\n".join(grezzi))

    async def _pdf(self, firma: Any, modo: str) -> Any:
        from pyhanko.sign.validation import async_validate_pdf_signature

        return await async_validate_pdf_signature(
            firma, signer_validation_context=self._contesto(modo))

    # ── valutazione comune ───────────────────────────────────────────

    async def _valuta(self, formato: FormatoFirma, livello: LivelloVerifica,
                      valida: Callable[[str], Awaitable[Any]]) -> tuple[FirmaVerificata, str]:
        try:
            stato = await valida("soft-fail")
        except Exception as exc:  # noqa: BLE001 — struttura non valida = TOTAL_FAILED
            return FirmaVerificata(formato, Indicazione.TOTAL_FAILED,
                                   crittografia_valida=False, sottoindicazione=SOTTO_FIRMA,
                                   motivi=(f"struttura della firma non valida: "
                                           f"{type(exc).__name__}",)), ""
        revoca = StatoRevoca.NON_CONTROLLATO
        stato_rev = None
        if livello is LivelloVerifica.CON_REVOCHE and stato.intact and stato.valid \
                and stato.trusted:
            try:
                stato_rev = await valida("require")
            except Exception:  # noqa: BLE001 — trattato come revoca non determinabile
                stato_rev = None
            if stato_rev is not None and stato_rev.trusted:
                revoca = StatoRevoca.BUONO
            elif stato_rev is not None and stato_rev.revoked:
                revoca = StatoRevoca.REVOCATO
            else:
                revoca = StatoRevoca.SCONOSCIUTO
        if stato.revoked:
            revoca = StatoRevoca.REVOCATO
        return self._normalizza(formato, livello, stato, revoca), _grezzo(stato_rev or stato)

    def _normalizza(self, formato: FormatoFirma, livello: LivelloVerifica, stato: Any,
                    revoca: StatoRevoca) -> FirmaVerificata:
        cert = stato.signing_cert
        motivi: list[str] = []
        indicazione = Indicazione.TOTAL_PASSED
        sotto: str | None = None
        if not stato.intact:
            indicazione, sotto = Indicazione.TOTAL_FAILED, SOTTO_HASH
        elif not stato.valid:
            indicazione, sotto = Indicazione.TOTAL_FAILED, SOTTO_FIRMA
        elif revoca is StatoRevoca.REVOCATO:
            indicazione, sotto = Indicazione.INDETERMINATE, SOTTO_REVOCATO_SENZA_POE
        elif not stato.trusted:
            indicazione = Indicazione.INDETERMINATE
            problema = stato.trust_problem_indic
            sotto = problema.name if problema is not None else SOTTO_CATENA
        elif revoca is StatoRevoca.SCONOSCIUTO:
            indicazione, sotto = Indicazione.INDETERMINATE, SOTTO_REVOCA_ASSENTE
        docmdp = getattr(stato, "docmdp_ok", True)
        if docmdp is False and indicazione is Indicazione.TOTAL_PASSED:
            indicazione = Indicazione.INDETERMINATE
            motivi.append("il PDF contiene modifiche non ammesse dopo la firma")
        if sotto:
            motivi.insert(0, in_parole(sotto) or sotto)
        ancora = None
        if stato.validation_path is not None:
            try:
                ancora = stato.validation_path.trust_anchor.authority.name.human_friendly
            except AttributeError:  # pragma: no cover — versioni diverse di certvalidator
                ancora = None
        data = getattr(stato, "signer_reported_dt", None)
        return FirmaVerificata(
            formato, indicazione, firmatario=nome_firmatario(cert),
            certificato=certificato(cert), crittografia_valida=bool(stato.intact and stato.valid),
            catena_attendibile=bool(stato.trusted or revoca is StatoRevoca.REVOCATO
                                    and stato.validation_path is not None),
            trust_anchor=ancora, revoca=revoca,
            data_firma=data.isoformat() if data else None, sottoindicazione=sotto,
            motivi=tuple(motivi))


class ProcessoVerificaTerminato(RuntimeError):
    """R12a (VD-C-147): il processo di verifica è morto senza esito."""


_CONTESTO: Any = None


def _contesto_processi() -> Any:
    """Contesto ``forkserver`` (processi figli da un server pulito, senza thread né
    loop del chiamante) con pyHanko precaricato; ``spawn`` dove non esiste."""
    global _CONTESTO
    if _CONTESTO is None:
        metodi = multiprocessing.get_all_start_methods()
        _CONTESTO = multiprocessing.get_context(
            "forkserver" if "forkserver" in metodi else "spawn")
        if "forkserver" in metodi:
            _CONTESTO.set_forkserver_preload(["libs.shared.firme.pyhanko_verificatore"])
    return _CONTESTO


def _verifica_nel_figlio(conn: Any, classe: type[VerificatorePyHanko], radici_der: list[bytes],
                         crl_der: list[bytes], ocsp_der: list[bytes], rete: bool,
                         memoria_max_mb: int, argomenti: dict[str, Any]) -> None:
    """Corpo del processo di verifica (R12a, VD-C-147)."""
    try:
        if memoria_max_mb > 0:
            import resource

            limite = int(memoria_max_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limite, limite))
        verificatore = classe(
            [x509.Certificate.load(d) for d in radici_der], crl_locali=crl_der,
            ocsp_locali=ocsp_der, rete=rete, processo_separato=False)
        conn.send(("ok", asyncio.run(verificatore._verifica(**argomenti))))
    except MemoryError:
        conn.send(("oltre", f"memoria massima della verifica superata ({memoria_max_mb} MB): "
                            "verifica terminata"))
    except BaseException as exc:  # noqa: BLE001 — riferito al padre come errore
        try:
            conn.send(("errore", f"{type(exc).__name__}: {exc}"[:500]))
        except Exception:  # noqa: BLE001, S110 — pipe chiusa: il padre vede EOF
            pass
    finally:
        conn.close()


def _grezzo(stato: Any) -> str:
    try:
        return str(stato.pretty_print_details())
    except Exception:  # noqa: BLE001 — il rapporto grezzo è accessorio
        return repr(stato)[:2000]


__all__ = ["VerificatorePyHanko", "carica_certificati_pem"]
