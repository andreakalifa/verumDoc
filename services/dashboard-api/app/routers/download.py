# SPDX-FileCopyrightText: 2026 Contributori di VerumDoc
# SPDX-License-Identifier: EUPL-1.2
"""
Download proxy — scarica file AIP da MinIO e li streamma al client.
Usa boto3 sincrono in asyncio.to_thread per evitare dipendenze extra.

CRIT-001 FIX: auth JWT + tenant_id filter su tutti gli endpoint.
PRIMA: 5 endpoint completamente aperti senza autenticazione.
DOPO:  JWT obbligatorio, query filtrata per tenant_id.

VD-C-022 (R03): i parametri si convertono con ``CAST(:tid AS uuid)``; id
dell'AIP non valido → 404; token senza cliente → 403.

G2 (secondo giro, decisione VD-C-005 del coordinatore):
- METADATI (elenco file, manifest, indice SInCRO, rapporto di versamento): un
  profilo globale (SUPER_ADMIN, RSC_CONSERVATORE) che ha scelto un ente li
  legge passando ``?tenant_id=<ente>``; gli altri profili solo per il proprio
  ente (altro ente → 403).
- CONTENUTO (``/download/completo``, che contiene i documenti): solo l'ente del
  token, per tutti i profili. ``tenant_id`` di un altro ente → 403; un AIP di
  un altro ente senza ``tenant_id`` → 404 come uno inesistente.
- RdV: ``pdv.metadata_json.rdv_definitiva`` è un riferimento S3 (be-pipeline):
  si legge da S3 e se ne verifica l'impronta; il RdV preliminare (XML nel DB)
  si restituisce solo se il definitivo non c'è, dichiarandolo in
  ``X-RdV-Tipo``.
- Errori S3: 502 con id di correlazione, mai il testo dell'eccezione; lo ZIP
  completo non si produce se manca o non corrisponde anche un solo file (prima
  conteneva file ``*_ERROR.txt`` e sembrava completo).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
import uuid
import zipfile

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth.jwt_handler import verify_access_token
from app.auth.limiti import limite_richieste
from app.config import settings
from app.errori import errore_db, errore_interno
from libs.shared.db import get_async_session
from libs.shared.security.revoca import dipendenza_non_revocato

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/dashboard/aip", tags=["Download"],
                   # Revisione B5: token di utenti disattivati o enti sospesi → 401
                   dependencies=[Depends(dipendenza_non_revocato(verify_access_token)),
                                 # R03 f2 (REV-03): limite di richieste al minuto del piano
                                 Depends(limite_richieste)])

RUOLI_GLOBALI = ("SUPER_ADMIN", "RSC_CONSERVATORE")


def _require_download_auth(authorization: str | None) -> dict:
    """CRIT-001 FIX: auth obbligatoria su tutti i download."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            401, {"type": "DL-401", "title": "Token JWT obbligatorio per download"},
        )
    return verify_access_token(authorization.split(" ", 1)[1])


def _uuid_o_none(valore: object) -> str | None:
    try:
        return str(uuid.UUID(str(valore or "")))
    except ValueError:
        return None


def _id_e_tenant(
    aip_id: str, user: dict, tenant_scelto: str | None = None, *, contenuto: bool = False,
) -> tuple[str, str]:
    """
    Valida l'id dell'AIP e decide su quale ente si legge.

    - senza ``tenant_scelto``: l'ente del token (token senza ente → 403);
    - con ``tenant_scelto`` uguale all'ente del token: come sopra;
    - con un altro ente: per i metadati solo i ruoli globali; per il
      contenuto nessuno (VD-C-005, §19.9) → 403.
    """
    aid = _uuid_o_none(aip_id)
    if aid is None:
        raise HTTPException(404, {"type": "DL-404", "title": "AIP non trovato"})
    tenant_token = _uuid_o_none(user.get("tenant_id"))
    if tenant_scelto:
        scelto = _uuid_o_none(tenant_scelto)
        if scelto is None:
            raise HTTPException(400, {"type": "DL-400", "title": "tenant_id non valido"})
        if scelto != tenant_token:
            if contenuto:
                raise HTTPException(403, {
                    "type": "DL-403-CONTENUTO",
                    "title": "Il contenuto dei pacchetti di un altro ente non è accessibile",
                    "detail": "Anche i profili del conservatore leggono solo i metadati "
                              "dei pacchetti di altri enti (VD-C-005).",
                })
            if user.get("ruolo") not in RUOLI_GLOBALI:
                raise HTTPException(403, {
                    "type": "AUTH-020", "title": "Accesso negato a questo ente",
                    "detail": "Puoi leggere solo i pacchetti del tuo ente",
                })
        return aid, scelto
    if tenant_token is None:
        raise HTTPException(
            403, {"type": "DL-403", "title": "Il token non indica un cliente: accesso negato"},
        )
    return aid, tenant_token


def _boto_download(bucket: str, key: str) -> bytes:
    """
    Download sincrono da MinIO — viene eseguito in un thread separato.
    B-R03 FIX: credenziali da configurazione (variabili d'ambiente).
    """
    from libs.shared.storage.client import client_s3

    # R04: client unico per processo; chiavi vuote → catena di credenziali (IRSA)
    s3 = client_s3(endpoint_url=settings.s3_endpoint_url, access_key=settings.s3_access_key,
                   secret_key=settings.s3_secret_key,
                   region=getattr(settings, "s3_region", "eu-south-1"))
    resp = s3.get_object(Bucket=bucket, Key=key)
    return resp["Body"].read()


async def _get_s3_file(bucket: str, key: str) -> bytes:
    """Wrapper asincrono per il download S3."""
    return await asyncio.to_thread(_boto_download, bucket, key)


async def _leggi_s3(bucket: str, key: str, *, aip_id: str, cosa: str) -> bytes:
    try:
        return await _get_s3_file(bucket, key)
    except Exception as exc:  # noqa: BLE001 — qualsiasi errore di storage: 502 esplicito
        raise errore_interno(
            502, "DL-502", f"Archivio non disponibile: {cosa} non letto", exc,
            aip_id=aip_id, chiave=key,
        ) from None


async def _riga(query: str, parametri: dict, operazione: str):
    try:
        async with get_async_session() as session:
            return (await session.execute(text(query), parametri)).fetchone()
    except SQLAlchemyError as exc:
        raise errore_db(operazione, exc, codice="DL-503") from None


def _suffisso(tenant_scelto: str | None) -> str:
    return f"?tenant_id={tenant_scelto}" if tenant_scelto else ""


@router.get("/{aip_id}/files", summary="Lista file dell'AIP (metadati)")
async def lista_file_aip(
    aip_id: str,
    tenant_id: str | None = Query(None, description="Ente scelto (solo profili globali)"),
    authorization: str | None = Header(default=None),
) -> dict:
    user = _require_download_auth(authorization)
    aip_id, tid = _id_e_tenant(aip_id, user, tenant_id)
    row = await _riga(
        "SELECT a.urn, a.s3_key, a.sincro_xml_key, a.hash_sha256, a.dimensione_bytes, "
        "a.data_conservazione, p.metadata_json "
        "FROM public.aip a JOIN public.pdv p ON p.id = a.pdv_id "
        "WHERE a.id = CAST(:id AS uuid) AND a.tenant_id = CAST(:tid AS uuid)",
        {"id": aip_id, "tid": tid}, "lettura dei file del pacchetto",
    )
    if not row:
        raise HTTPException(404, {"type": "DL-404", "title": "AIP non trovato"})

    urn, s3_key, sincro_key, sha256, dim, data_c, meta = row
    meta = meta or {}
    suffisso = _suffisso(tenant_id)
    base = f"/api/dashboard/aip/{aip_id}/download"
    files = []
    if s3_key:
        files.append({"nome": "manifest.json", "tipo": "MANIFEST", "mime": "application/json",
                      "categoria": "metadati", "download_url": f"{base}/manifest{suffisso}"})
    if sincro_key:
        files.append({"nome": "IndiceAIP_SInCRO.xml", "tipo": "SINCRO", "mime": "application/xml",
                      "categoria": "metadati", "download_url": f"{base}/sincro{suffisso}"})
    if isinstance(meta.get("rdv_definitiva"), dict) or meta.get("rdv_preliminare"):
        files.append({
            "nome": "RdV.xml", "tipo": "RDV", "mime": "application/xml", "categoria": "metadati",
            "rdv_tipo": "definitivo" if isinstance(meta.get("rdv_definitiva"), dict)
            else "preliminare",
            "download_url": f"{base}/rdv{suffisso}",
        })
    # Il pacchetto completo contiene i documenti: solo per l'ente del token
    contenuto_accessibile = tid == _uuid_o_none(user.get("tenant_id"))
    return {
        "aip_id": aip_id,
        "tenant_id": tid,
        "urn": urn,
        "hash_sha256": sha256,
        "dimensione_bytes": dim,
        "data_conservazione": data_c.isoformat() if data_c else None,
        "files": files,
        "contenuto_accessibile": contenuto_accessibile,
        "download_completo_url": f"{base}/completo" if contenuto_accessibile else None,
    }


@router.get("/{aip_id}/download/manifest", summary="Scarica manifest.json (metadati)")
async def download_manifest(
    aip_id: str,
    tenant_id: str | None = Query(None, description="Ente scelto (solo profili globali)"),
    authorization: str | None = Header(default=None),
) -> Response:
    user = _require_download_auth(authorization)
    aip_id, tid = _id_e_tenant(aip_id, user, tenant_id)
    row = await _riga(
        "SELECT s3_key, s3_bucket FROM public.aip "
        "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)",
        {"id": aip_id, "tid": tid}, "lettura del manifest",
    )
    if not row or not row[0]:
        raise HTTPException(404, {"type": "DL-404", "title": "manifest.json non trovato"})
    content = await _leggi_s3(row[1] or settings.s3_bucket_aip, row[0],
                              aip_id=aip_id, cosa="manifest")
    return Response(
        content=content, media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="manifest_{aip_id[:8]}.json"'},
    )


@router.get("/{aip_id}/download/sincro", summary="Scarica IndiceAIP SInCRO XML (metadati)")
async def download_sincro(
    aip_id: str,
    tenant_id: str | None = Query(None, description="Ente scelto (solo profili globali)"),
    authorization: str | None = Header(default=None),
) -> Response:
    user = _require_download_auth(authorization)
    aip_id, tid = _id_e_tenant(aip_id, user, tenant_id)
    row = await _riga(
        "SELECT sincro_xml_key, s3_bucket FROM public.aip "
        "WHERE id = CAST(:id AS uuid) AND tenant_id = CAST(:tid AS uuid)",
        {"id": aip_id, "tid": tid}, "lettura dell'indice SInCRO",
    )
    if not row or not row[0]:
        raise HTTPException(404, {"type": "DL-404", "title": "SInCRO XML non trovato"})
    content = await _leggi_s3(row[1] or settings.s3_bucket_aip, row[0],
                              aip_id=aip_id, cosa="indice SInCRO")
    return Response(
        content=content, media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="IndiceAIP_{aip_id[:8]}.xml"'},
    )


async def _contenuto_rdv(meta: dict, aip_id: str) -> tuple[bytes, str] | None:
    """(XML, tipo) del RdV: definitivo da S3 con impronta verificata, altrimenti
    il preliminare registrato nel DB; None se nessuno dei due esiste."""
    definitivo = meta.get("rdv_definitiva")
    if isinstance(definitivo, dict) and definitivo.get("s3_key"):
        contenuto = await _leggi_s3(
            definitivo.get("s3_bucket") or settings.s3_bucket_aip, definitivo["s3_key"],
            aip_id=aip_id, cosa="rapporto di versamento",
        )
        attesa = definitivo.get("impronta_sha256")
        if attesa and hashlib.sha256(contenuto).hexdigest() != attesa:
            raise errore_interno(
                502, "DL-502-IMPRONTA",
                "Il rapporto di versamento letto non corrisponde all'impronta registrata",
                None, aip_id=aip_id,
            )
        return contenuto, "definitivo"
    preliminare = meta.get("rdv_preliminare")
    if isinstance(preliminare, str) and preliminare:
        return preliminare.encode("utf-8"), "preliminare"
    return None


@router.get("/{aip_id}/download/rdv", summary="Scarica il rapporto di versamento (metadati)")
async def download_rdv(
    aip_id: str,
    tenant_id: str | None = Query(None, description="Ente scelto (solo profili globali)"),
    authorization: str | None = Header(default=None),
) -> Response:
    """Rapporto di versamento: definitivo se emesso, altrimenti preliminare
    (dichiarato nell'intestazione ``X-RdV-Tipo``)."""
    user = _require_download_auth(authorization)
    aip_id, tid = _id_e_tenant(aip_id, user, tenant_id)
    row = await _riga(
        "SELECT p.metadata_json FROM public.pdv p JOIN public.aip a ON a.pdv_id = p.id "
        "WHERE a.id = CAST(:id AS uuid) AND a.tenant_id = CAST(:tid AS uuid)",
        {"id": aip_id, "tid": tid}, "lettura del rapporto di versamento",
    )
    if not row:
        raise HTTPException(404, {"type": "DL-404", "title": "AIP o PdV non trovato"})
    rdv = await _contenuto_rdv(row[0] or {}, aip_id)
    if rdv is None:
        raise HTTPException(404, {"type": "DL-404-RDV",
                                  "title": "RdV non ancora disponibile — in attesa di hashchain"})
    contenuto, tipo = rdv
    nome = f"RdV_{aip_id[:8]}.xml" if tipo == "definitivo" else f"RdV_preliminare_{aip_id[:8]}.xml"
    return Response(
        content=contenuto, media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{nome}"', "X-RdV-Tipo": tipo},
    )


_CARATTERI_VIETATI_NEL_NOME = re.compile(r'[\x00-\x1f\x7f<>:"|?*]')


def nome_nel_zip(nome: str | None, usati: set[str]) -> str:
    """Nome sicuro di un documento dentro lo ZIP (revisione di sicurezza B8,
    zip-slip): solo il nome finale (niente '/', '\\', '..', percorsi assoluti
    o unità), senza caratteri di controllo, al più 150 caratteri; i doppioni
    ricevono un suffisso. I dati del DB non sono considerati attendibili."""
    base = re.split(r"[\\/]", str(nome or ""))[-1]
    base = _CARATTERI_VIETATI_NEL_NOME.sub("_", base).strip().lstrip(".").strip() or "file"
    base = base[:150]
    candidato, n = base, 2
    radice, punto, estensione = base.rpartition(".")
    while candidato.lower() in usati:
        candidato = f"{radice}-{n}.{estensione}" if punto and radice else f"{base}-{n}"
        n += 1
    usati.add(candidato.lower())
    return candidato


@router.get("/{aip_id}/download/completo",
            summary="Scarica AIP completo (zip con tutti i file) — solo il proprio ente")
async def download_completo(
    aip_id: str,
    tenant_id: str | None = Query(None, description="Deve coincidere con l'ente del token"),
    authorization: str | None = Header(default=None),
) -> Response:
    """Archivio ZIP con manifest, indice, documenti e RdV, con le impronte
    ricalcolate. Se un file manca o non corrisponde all'impronta registrata
    non si produce lo ZIP (502)."""
    user = _require_download_auth(authorization)
    aip_id, tid = _id_e_tenant(aip_id, user, tenant_id, contenuto=True)

    try:
        async with get_async_session() as session:
            aip_row = (await session.execute(
                text("SELECT a.s3_key, a.s3_bucket, a.sincro_xml_key, a.urn, p.metadata_json "
                     "FROM public.aip a JOIN public.pdv p ON p.id = a.pdv_id "
                     "WHERE a.id = CAST(:id AS uuid) AND a.tenant_id = CAST(:tid AS uuid)"),
                {"id": aip_id, "tid": tid},
            )).fetchone()
            doc_rows = (await session.execute(
                text("SELECT nome_file, s3_key, hash_sha256 FROM public.documenti "
                     "WHERE aip_id = CAST(:id AS uuid) AND s3_key IS NOT NULL ORDER BY ordine"),
                {"id": aip_id},
            )).fetchall() if aip_row else []
    except SQLAlchemyError as exc:
        raise errore_db("lettura del pacchetto", exc, codice="DL-503") from None
    if not aip_row:
        raise HTTPException(404, {"type": "DL-404", "title": "AIP non trovato"})

    s3_key, bucket, sincro_key, urn, meta_pdv = aip_row
    bucket = bucket or settings.s3_bucket_aip
    short_id = aip_id[:8]
    righe_readme: list[str] = []

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        def aggiungi(nome: str, dati: bytes) -> None:
            zf.writestr(f"AIP_{short_id}/{nome}", dati)
            righe_readme.append(f"{hashlib.sha256(dati).hexdigest()}  {nome}")

        if s3_key:
            aggiungi("manifest.json",
                     await _leggi_s3(bucket, s3_key, aip_id=aip_id, cosa="manifest"))
        if sincro_key:
            aggiungi("IndiceAIP_SInCRO.xml",
                     await _leggi_s3(bucket, sincro_key, aip_id=aip_id, cosa="indice SInCRO"))
        nomi_usati: set[str] = set()
        for nome_db, doc_key, impronta in doc_rows:
            nome = nome_nel_zip(nome_db, nomi_usati)
            dati = await _leggi_s3(bucket, doc_key, aip_id=aip_id, cosa=f"documento {nome}")
            if impronta and hashlib.sha256(dati).hexdigest() != impronta:
                raise errore_interno(
                    502, "DL-502-IMPRONTA",
                    f"Il documento {nome} non corrisponde all'impronta registrata",
                    None, aip_id=aip_id, documento=nome,
                )
            aggiungi(f"files/{nome}", dati)
        rdv = await _contenuto_rdv(meta_pdv or {}, aip_id)
        if rdv is not None:
            aggiungi("RdV.xml" if rdv[1] == "definitivo" else "RdV_preliminare.xml", rdv[0])

        zf.writestr(
            f"AIP_{short_id}/README.txt",
            "Pacchetto di archiviazione\n"
            f"URN: {urn or aip_id}\n"
            f"ID: {aip_id}\n"
            "Impronte SHA-256 dei file contenuti (ricalcolate al momento dello scaricamento):\n"
            + "\n".join(righe_readme) + "\n",
        )

    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="AIP_{short_id}_completo.zip"'},
    )
