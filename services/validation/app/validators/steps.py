# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
9 step di validazione LG AGID 2022 per un Pacchetto di Versamento.

Step 1:  Verifica struttura IndiceUD (campi obbligatori)
Step 2:  Verifica unicita documenti (no duplicati per id)
Step 3:  Verifica presenza documento PRINCIPALE
Step 4:  Verifica formato ammesso (dichiarato e rilevato; elenco della classe fotografato)
Step 5:  Verifica integrita delle impronte (lettura dallo storage in streaming)
Step 6:  Verifica metadati Allegato 5 (se presenti)
Step 7:  Verifica classe documentale (OPA policy)
Step 8:  Verifica dimensione file (limite della classe fotografato nel PdV)
Step 9:  Verifica coerenza chiave documentale (anno, numero, registro)
Step 10: (R08, D02) Formato riconosciuto dal contenuto (PRONOM, PDF/A) e catalogo dei formati
Step 11: (R08, D01) Verifica delle firme al livello della classe, rapporto di verifica

Riferimento: LG AGID 2022, SInCRO 2020 UNI 11386
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text

from app.config import settings
from app.services.lettura_file import FileOltreLimite
from app.services.regole_pdv import RegolePdv, normalizza_mime, predefinite
from app.validators.base import (
    BaseValidator,
    ErroreVerificaTransitorio,
    ValidationContext,
    ValidationResult,
    atto_di_sistema,
)
from libs.shared.db import get_async_session
from libs.shared.impronte import ALGORITMI_IMPRONTA
from libs.shared.models import ModalitaFormazione
from libs.verumdoc_rules.catalogo_v1 import metadati_minimi_mancanti, minimi_predefiniti

#: Algoritmo calcolato SEMPRE (oltre al dichiarato): è quello dell'IPdA e del
#: confronto con le impronte calcolate dal gateway durante il trasferimento.
ALGORITMO_RIFERIMENTO = "SHA-256"


def regole_ctx(ctx: ValidationContext) -> RegolePdv:
    """Regole del PdV (fotografia) o, se assenti nel contesto, predefiniti del servizio."""
    if ctx.regole is not None:
        return ctx.regole
    return predefinite(limite_mb=settings.validazione_max_file_mb, formati=settings.formati_ammessi)


class Step1StrutturaIndice(BaseValidator):
    """Step 1 — Verifica campi obbligatori IndiceUD."""
    step = 1
    nome = "struttura_indice"

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        indice = ctx.indice
        if "classe_documentale" not in indice:
            return self.fail(ctx, "VLD-101", "Campo 'classe_documentale' mancante nell'IndiceUD")
        if not indice.get("documenti"):
            return self.fail(ctx, "VLD-102", "Nessun documento nell'IndiceUD")
        for doc in indice["documenti"]:
            for campo in ("id", "tipo", "nome_file", "hash"):
                if campo not in doc:
                    return self.fail(ctx, "VLD-103",
                        f"Campo obbligatorio '{campo}' mancante nel documento "
                        f"'{doc.get('nome_file', '?')}'")
            if "impronta" not in doc["hash"] or "algoritmo" not in doc["hash"]:
                return self.fail(ctx, "VLD-104",
                    f"Hash incompleto per '{doc.get('nome_file', '?')}': richiesti 'impronta' e "
                    f"'algoritmo'")
        return self.ok(ctx)


class Step2UnicitaDocumenti(BaseValidator):
    """Step 2 — Verifica che non ci siano documenti duplicati per id."""
    step = 2
    nome = "unicita_documenti"

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        ids = [doc["id"] for doc in ctx.indice.get("documenti", [])]
        seen = set()
        duplicati = []
        for doc_id in ids:
            if doc_id in seen:
                duplicati.append(doc_id)
            seen.add(doc_id)
        if duplicati:
            return self.fail(ctx, "VLD-201", "Documenti duplicati rilevati",
                             {"duplicati": duplicati})
        nomi = [doc["nome_file"] for doc in ctx.indice.get("documenti", [])]
        nomi_visti = set()
        nomi_dup = []
        for n in nomi:
            if n in nomi_visti:
                nomi_dup.append(n)
            nomi_visti.add(n)
        if nomi_dup:
            return self.fail(ctx, "VLD-202", "Nome file duplicato",
                             {"duplicati": nomi_dup})
        return self.ok(ctx)


class Step3DocumentoPrincipale(BaseValidator):
    """Step 3 — Verifica presenza di almeno un documento PRINCIPALE."""
    step = 3
    nome = "documento_principale"

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        principali = [
            doc for doc in ctx.indice.get("documenti", [])
            if doc.get("tipo") == "PRINCIPALE"
        ]
        if not principali:
            return self.fail(ctx, "VLD-301",
                "Nessun documento di tipo PRINCIPALE nell'IndiceUD. "
                "Richiesto almeno un documento principale (LG AGID 2022 §5.1)")
        if len(principali) > 1:
            return self.fail(ctx, "VLD-302",
                f"Trovati {len(principali)} documenti PRINCIPALE. Ammesso al massimo 1.",
                {"principali": [d["nome_file"] for d in principali]})
        return self.ok(ctx)


class Step4FormatoMIME(BaseValidator):
    """
    Step 4 — Formato ammesso.

    R03 (b5): l'elenco è quello della classe fotografato nel PdV
    (FORMATI_AMMESSI con l'effetto di RGL-105; vuoto = nessuna restrizione); per i
    PdV senza fotografia resta l'elenco del servizio. Si controllano il formato
    dichiarato e quello riconosciuto dal gateway sui primi KB del contenuto
    (``formato_rilevato`` del riferimento): la verifica non rilegge la testa del
    file perché lo Step 5 prova che i byte nello storage sono gli stessi.
    """
    step = 4
    nome = "formato_mime"

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        regole = regole_ctx(ctx)
        ammessi = regole.formati_ammessi
        if ammessi is None:
            return self.ok(ctx, {"formati": "nessuna_restrizione",
                                 "origine": regole.origine_formati})
        errori = []
        for doc in ctx.indice.get("documenti", []):
            nome_file = doc.get("nome_file", "")
            if atto_di_sistema(ctx, doc):   # R09 (D24): atto XML di rettifica
                continue
            rif = ctx.riferimenti.get(nome_file)
            da_controllare = {"dichiarato": doc.get("formato"),
                              "rilevato": rif.formato_rilevato if rif else None}
            for origine, fmt in da_controllare.items():
                if fmt and normalizza_mime(fmt) not in ammessi:
                    errori.append({"file": nome_file, "formato": fmt, "origine_formato": origine,
                                   "ammessi": list(ammessi)})
        if errori:
            return self.fail(ctx, "VLD-401",
                "Formato non ammesso per la classe documentale per uno o più documenti",
                {"errori": errori, "origine_elenco": regole.origine_formati})
        return self.ok(ctx)


class Step5IntegritaHash(BaseValidator):
    """
    Step 5 — Ricalcolo delle impronte sui file letti dallo storage (VD-C-008).

    R03 (b5): ogni file si legge IN STREAMING a blocchi (``LettoreS3``): nessun
    file intero in memoria, qualunque sia la dimensione. Prima si legge solo la
    dimensione (HEAD): un file oltre il limite della classe non si scarica (lo
    rifiuta lo Step 8 con VLD-801). Si calcolano l'algoritmo dichiarato e SHA-256.

    Fail-closed: un documento il cui file non è stato letto NON supera il passo.
    Ogni algoritmo ammesso si verifica davvero; un algoritmo non ammesso è un
    errore. Esiti:
      - VLD-501: impronta ricalcolata ≠ dichiarata (o file non disponibile,
        algoritmo non ammesso);
      - VLD-502: file nello storage diverso da quello ricevuto dal gateway
        (dimensione o impronte calcolate in transito non corrispondono).
    """
    step = 5
    nome = "integrita_hash"

    # Tabella unica degli algoritmi ammessi (libs/shared/impronte.py)
    ALGORITMI = ALGORITMI_IMPRONTA

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        regole = regole_ctx(ctx)
        limite = regole.limite_byte_file
        errori: list[dict] = []
        errori_storage: list[dict] = []
        calcolate: dict[str, dict] = {}
        oltre_limite: list[str] = []
        for doc in ctx.indice.get("documenti", []):
            nome = doc["nome_file"]
            rif = ctx.riferimenti.get(nome)
            if rif is None or ctx.lettore is None:
                errori.append({"file": nome, "errore": "file_non_disponibile"})
                continue
            algoritmo = str(doc["hash"].get("algoritmo", "")).strip().upper()
            impronta_attesa = str(doc["hash"].get("impronta", ""))
            if algoritmo not in self.ALGORITMI:
                errori.append({"file": nome, "errore": f"algoritmo_non_ammesso: {algoritmo}"})
                continue
            # Dimensione dai metadati: non si scarica un file che va rifiutato
            dimensione = await ctx.lettore.dimensione(rif)
            ctx.dimensioni[nome] = dimensione
            if limite is not None and dimensione > limite:
                oltre_limite.append(nome)
                continue
            try:
                letto = await ctx.lettore.leggi(
                    rif, algoritmi=sorted({algoritmo, ALGORITMO_RIFERIMENTO}), limite_byte=limite,
                )
            except FileOltreLimite as exc:  # l'oggetto è cresciuto fra HEAD e lettura
                ctx.dimensioni[nome] = exc.letti
                oltre_limite.append(nome)
                continue
            ctx.dimensioni[nome] = letto.dimensione
            ctx.file_letti[nome] = letto
            # R12a (VD-C-146): le letture dei passi successivi sui byte di QUESTA versione
            ctx.riferimenti[nome] = rif.legato_a(letto)
            calcolato = letto.impronte[algoritmo]
            calcolate[nome] = {"algoritmo": algoritmo, "impronta": calcolato,
                               "sha256": letto.impronte[ALGORITMO_RIFERIMENTO],
                               "dimensione": letto.dimensione}
            if calcolato.lower() != impronta_attesa.lower():
                errori.append({
                    "file": nome,
                    "algoritmo": algoritmo,
                    "atteso": impronta_attesa,
                    "calcolato": calcolato,
                })
            errori_storage.extend(_confronta_con_versamento(nome, rif, letto))
        if errori:
            return self.fail(ctx, "VLD-501",
                "Verifica integrità hash fallita per uno o più documenti",
                {"errori": errori + errori_storage})
        if errori_storage:
            return self.fail(ctx, "VLD-502",
                "Il file nello storage non corrisponde a quello ricevuto al versamento",
                {"errori": errori_storage})
        dettagli: dict = {"impronte_ricalcolate": calcolate}
        if oltre_limite:
            # Non letti perché oltre il limite: lo Step 8 li rifiuta (VLD-801)
            dettagli["non_letti_oltre_limite"] = oltre_limite
        return self.ok(ctx, dettagli)


def _confronta_con_versamento(nome: str, rif, letto) -> list[dict]:
    """Dimensione e impronte lette ora vs quelle misurate dal gateway in transito."""
    errori = []
    if rif.dimensione_versata is not None and rif.dimensione_versata != letto.dimensione:
        errori.append({"file": nome, "errore": "dimensione_diversa_dal_versamento",
                       "versata": rif.dimensione_versata, "letta": letto.dimensione})
    for algoritmo, impronta in letto.impronte.items():
        versata = rif.impronte_versamento.get(algoritmo)
        if versata is not None and versata.lower() != impronta.lower():
            errori.append({"file": nome, "errore": "impronta_diversa_dal_versamento",
                           "algoritmo": algoritmo, "versata": versata, "calcolata": impronta})
    return errori


class Step6MetadatiAllegato5(BaseValidator):
    """
    Step 6 — Verifica metadati Allegato 5 AGID nel campo 'parametri' di ogni documento.
    B-04 FIX: ora valida doc['parametri'] (MetadatiDocumento JSON) invece
    del vecchio campo metadati_file (file XML separato — non più usato).

    Metadati minimi del documento PRINCIPALE (R03 f1, difetto 3 del rapporto c2):
    UNA definizione, nel motore delle regole. L'elenco è quello fotografato nel
    PdV (``METADATI_MINIMI_PRINCIPALE``, ``ctx.regole.metadati_minimi``; per i
    PdV senza fotografia il predefinito normativo del catalogo) e il controllo è
    la stessa funzione della regola RGL-101 del gateway
    (``metadati_minimi_mancanti``: solo al primo livello di ``parametri``).
    Così un PdV accettato dal gateway (202) non è rifiutato qui per lo stesso
    controllo. Restano i controlli di schema (modalità di formazione, oggetto di
    almeno 5 caratteri), uguali a quelli dello schema dell'indice del gateway.
    """
    step = 6
    nome = "metadati_allegato5"

    # VD-C-037: unica definizione in libs.shared.models (Allegato 5, LG §2.1.1:
    # valori a, b, c, d). Nessun elenco locale.
    MODALITA_VALIDE = frozenset(m.value for m in ModalitaFormazione)

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        errori = []
        documenti = ctx.indice.get("documenti", [])
        minimi = ctx.regole.metadati_minimi if ctx.regole is not None else minimi_predefiniti()

        for doc in documenti:
            tipo_doc = doc.get("tipo", "PRINCIPALE")
            nome_file = doc.get("nome_file", "")
            parametri = doc.get("parametri")

            # Documenti non-principali: metadati opzionali
            if tipo_doc != "PRINCIPALE":
                if parametri is not None:
                    # Se presenti, verifica che siano un dict valido
                    if not isinstance(parametri, dict):
                        errori.append({
                            "doc": nome_file,
                            "errore": "parametri deve essere un oggetto JSON",
                        })
                continue

            # Tipo PRINCIPALE: parametri obbligatori se ci sono metadati minimi da avere
            if parametri is None:
                if not minimi:
                    continue
                errori.append({
                    "doc": nome_file,
                    "errore": (
                        "Campo 'parametri' (MetadatiDocumento Allegato 5 AGID) "
                        "obbligatorio per documento di tipo PRINCIPALE"
                    ),
                    "campi_richiesti": list(minimi),
                })
                continue

            if not isinstance(parametri, dict):
                errori.append({
                    "doc": nome_file,
                    "errore": "parametri deve essere un oggetto JSON (dict)",
                })
                continue

            # Metadati minimi: stessa funzione e stesso elenco della regola RGL-101
            for campo in metadati_minimi_mancanti(parametri, minimi):
                errori.append({
                    "doc": nome_file,
                    "campo": campo,
                    "errore": f"Campo obbligatorio Allegato 5 mancante o vuoto: {campo!r}",
                })

            # Verifica modalita_formazione è un valore AGID valido
            modalita = parametri.get("modalita_formazione", "")
            if modalita and modalita not in self.MODALITA_VALIDE:
                errori.append({
                    "doc": nome_file,
                    "campo": "modalita_formazione",
                    "errore": (
                        f"Valore non valido: {modalita!r}. "
                        f"Valori ammessi: {sorted(self.MODALITA_VALIDE)}"
                    ),
                })

            # Verifica oggetto ha almeno 5 caratteri
            oggetto = parametri.get("oggetto", "")
            if oggetto and len(str(oggetto).strip()) < 5:
                errori.append({
                    "doc": nome_file,
                    "campo": "oggetto",
                    "errore": "Il campo 'oggetto' deve contenere almeno 5 caratteri",
                })

        if errori:
            return self.fail(
                ctx, "VLD-601",
                f"Metadati Allegato 5 AGID non conformi ({len(errori)} errori)",
                {"errori": errori},
            )
        return self.ok(ctx, {"allegato5_validato": True})


class Step7ClasseDocumentaleOPA(BaseValidator):
    """
    Step 7 — Classe documentale censita e attiva per il cliente (VD-C-017).

    Fail-closed in ogni ambiente: classe sconosciuta = rifiuto. Si cerca prima
    la classe del cliente, poi la classe di base del servizio (tenant_id NULL).
    Un errore del database non è un esito della verifica: si solleva
    ErroreVerificaTransitorio e il consumer ritenta (poi DLQ).
    """
    step = 7
    nome = "classe_documentale"

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        classe = ctx.indice.get("classe_documentale", "")
        if not classe:
            return self.fail(ctx, "VLD-701", "Campo classe_documentale mancante")
        try:
            tenant = uuid.UUID(str(ctx.tenant_id))
        except ValueError:
            return self.fail(ctx, "VLD-705", "Identificativo del cliente non valido",
                             {"tenant_id": str(ctx.tenant_id)})

        try:
            async with get_async_session() as session:
                row = (await session.execute(text(
                    "SELECT id, attiva FROM public.classi_documentali "
                    "WHERE codice = :c AND (tenant_id = :t OR tenant_id IS NULL) "
                    "ORDER BY tenant_id NULLS LAST LIMIT 1"
                ), {"c": classe, "t": tenant})).fetchone()
        except Exception as exc:  # noqa: BLE001 — errore tecnico, non esito della verifica
            raise ErroreVerificaTransitorio(
                f"Lettura della classe documentale non riuscita: {type(exc).__name__}"
            ) from exc

        if row is None:
            return self.fail(ctx, "VLD-703",
                f"Classe documentale {classe!r} non censita per il cliente. "
                "Configurare la classe tramite il pannello Classi Documentali "
                "prima di effettuare il versamento.",
                {"classe": classe})
        if not row[1]:
            return self.fail(ctx, "VLD-702",
                f"Classe documentale {classe!r} non attiva",
                {"classe": classe})
        return self.ok(ctx, {"classe_id": str(row[0])})


class Step8DimensioneFile(BaseValidator):
    """
    Step 8 — Nessun file oltre il limite per file.

    R03 (b5): niente più limite fisso di 100 MB. Il limite è quello della classe
    fotografato nel PdV (CLASSE_DIMENSIONE_MAX_MB, solo se RGL-104 BLOCCA); per i
    PdV senza fotografia vale VALIDAZIONE_MAX_FILE_MB (predefinito 5120 MB).
    La dimensione è quella letta dallo storage (Step 5) o, se manca, dai
    metadati dell'oggetto (HEAD): il file non si scarica.
    """
    step = 8
    nome = "dimensione_file"

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        regole = regole_ctx(ctx)
        limite = regole.limite_byte_file
        if limite is None:
            return self.ok(ctx, {"limite": "nessuno", "origine": regole.origine_limite})
        errori = []
        for doc in ctx.indice.get("documenti", []):
            nome = doc.get("nome_file", "")
            size = ctx.dimensioni.get(nome)
            if size is None:
                rif = ctx.riferimenti.get(nome)
                if rif is None or ctx.lettore is None:
                    errori.append({"file": nome, "errore": "dimensione_non_determinabile"})
                    continue
                size = await ctx.lettore.dimensione(rif)
                ctx.dimensioni[nome] = size
            if size > limite:
                errori.append({
                    "file": nome,
                    "size_bytes": size,
                    "max_bytes": limite,
                    "size_mb": round(size / 1024 / 1024, 2),
                })
        if errori:
            return self.fail(ctx, "VLD-801",
                f"File oltre il limite massimo per file ({limite // 1024 // 1024} MB)",
                {"errori": errori, "origine_limite": regole.origine_limite})
        return self.ok(ctx, {"max_bytes": limite, "origine": regole.origine_limite})


class Step9CoerenzaChiave(BaseValidator):
    """
    Step 9 — Verifica coerenza della chiave documentale.
    Anno deve essere un anno valido, numero non vuoto se presente.
    """
    step = 9
    nome = "coerenza_chiave"

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        chiave = ctx.indice.get("chiave")
        if not chiave:
            return self.ok(ctx)  # chiave opzionale
        anno = chiave.get("anno")
        if anno:
            try:
                anno_int = int(anno)
                anno_corrente = datetime.now().year
                if not (1900 <= anno_int <= anno_corrente + 1):
                    return self.fail(ctx, "VLD-901",
                        f"Anno '{anno}' non valido. Atteso: 1900-{anno_corrente + 1}")
            except ValueError:
                return self.fail(ctx, "VLD-902", f"Anno '{anno}' non è un numero intero valido")
        numero = chiave.get("numero", "")
        if numero is not None and len(str(numero).strip()) == 0:
            return self.fail(ctx, "VLD-903",
                "Numero documento vuoto. Se presente deve essere non vuoto.")
        return self.ok(ctx)


from app.validators.contenuto import Step10FormatoRiconosciuto, Step11VerificaFirme  # noqa: E402

# Pipeline completa — ordine fisso per LG AGID
VALIDATION_PIPELINE: list[BaseValidator] = [
    Step1StrutturaIndice(),
    Step2UnicitaDocumenti(),
    Step3DocumentoPrincipale(),
    Step4FormatoMIME(),
    Step5IntegritaHash(),
    Step6MetadatiAllegato5(),
    Step7ClasseDocumentaleOPA(),
    Step8DimensioneFile(),
    Step9CoerenzaChiave(),
    # R08: dopo impronte e dimensione (byte versati, entro il limite): contenuto intero
    Step10FormatoRiconosciuto(),
    Step11VerificaFirme(),
]
