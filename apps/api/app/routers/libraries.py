from datetime import timedelta
from hashlib import sha1
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.dependencies import get_source_discovery_service
from app.models import (
    LibraryUploadCompleteRequest,
    LibraryUploadUrlRequest,
    LibraryUploadUrlResult,
    SourceIngestRequest,
    SourceIngestResult,
)
from app.services.google_clients import storage_client
from app.services.library_relationship_analyst import LibraryRelationshipAnalyst
from app.services.catalog_curator import CatalogCuratorService
from app.services.shamsiyya_library import LIBRARY_ID as SHAMSIYYA_LIBRARY_ID
from app.services.shamsiyya_library import ShamsiyyaLibraryParser
from app.services.source_discovery import SourceDiscoveryService
from app.security import require_jury_or_admin, require_live_elastic, sanitize_mapping

router = APIRouter(prefix="/api/libraries", tags=["libraries"])


@router.post(
    "/{library_id}/sources/upload-url",
    response_model=LibraryUploadUrlResult,
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def create_library_upload_url(
    library_id: str,
    payload: LibraryUploadUrlRequest,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> LibraryUploadUrlResult:
    if payload.file_size and payload.file_size > service.settings.library_direct_upload_max_bytes:
        raise HTTPException(status_code=413, detail={"code": "upload_too_large", "message": "Manual GCS import required for files larger than the direct upload limit."})
    safe_source_id = _safe_id(payload.source_id or f"upload-{sha1(payload.filename.encode()).hexdigest()[:12]}")
    safe_library_id = _safe_id(library_id)
    extension = _extension(payload.filename, payload.content_type)
    raw_object = f"raw/{safe_library_id}/institutional_upload/{safe_source_id}/source.{extension}"
    if not service.settings.google_cloud_project:
        raise HTTPException(status_code=400, detail="Google Cloud project is not configured for direct GCS upload.")
    try:
        client = storage_client(service.settings)
        blob = client.bucket(service.settings.gcs_bucket).blob(raw_object)
        upload_url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=45),
            method="PUT",
            content_type=payload.content_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not create signed upload URL: {exc}") from exc
    gcs_raw_path = f"gs://{service.settings.gcs_bucket}/{raw_object}"
    return LibraryUploadUrlResult(
        source_id=safe_source_id,
        upload_url=upload_url,
        gcs_raw_path=gcs_raw_path,
        raw_object=raw_object,
        headers={"Content-Type": payload.content_type},
        metadata={
            "library_id": library_id,
            "title": payload.title or payload.filename,
            "author_name": payload.author_name,
            "work_id": payload.work_id or safe_source_id,
            "domain": payload.domain,
            "notes": payload.notes,
        },
    )


@router.post(
    "/{library_id}/sources/complete-upload",
    response_model=SourceIngestResult,
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def complete_library_upload(
    library_id: str,
    payload: LibraryUploadCompleteRequest,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> SourceIngestResult:
    safe_library_id = _safe_id(library_id)
    safe_source_id = _safe_id(payload.source_id)
    extension = _extension(payload.raw_object, payload.content_type)
    expected_raw_object = f"raw/{safe_library_id}/institutional_upload/{safe_source_id}/source.{extension}"
    if payload.raw_object != expected_raw_object:
        raise HTTPException(status_code=400, detail={"code": "upload_object_invalid", "message": "Uploaded object path does not match the expected library/source namespace."})
    _validate_uploaded_object(service, payload.raw_object, payload.content_type)
    source_doc = _library_upload_source_doc(
        service,
        library_id=safe_library_id,
        source_id=safe_source_id,
        raw_object=payload.raw_object,
        extension=extension,
        title=payload.title or payload.source_id,
        author_name=payload.author_name,
        work_id=payload.work_id,
        domain=payload.domain,
        notes=payload.notes,
        url=f"gcs://{service.settings.gcs_bucket}/{payload.raw_object}",
    )
    indexed = service.elastic.index_source_metadata(source_doc)
    return SourceIngestResult(
        source_id=payload.source_id,
        gcs_raw_path=source_doc["gcs_raw_path"],
        gcs_ocr_path=source_doc["gcs_ocr_path"],
        gcs_normalized_path=source_doc["gcs_normalized_path"],
        indexed=indexed,
        ingestion_status=source_doc["ingestion_status"],
        note="Direct GCS upload registered. Source is queued; start OCR/indexing from the source status panel.",
        metadata=source_doc,
    )


@router.post(
    "/{library_id}/sources",
    response_model=SourceIngestResult,
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def add_library_source(
    library_id: str,
    provider: str = Form("Institutional Upload"),
    source_id: str | None = Form(None),
    work_id: str | None = Form(None),
    title: str | None = Form(None),
    author_name: str | None = Form(None),
    domain: str | None = Form(None),
    notes: str | None = Form(None),
    url: str | None = Form(None),
    file: UploadFile | None = File(None),
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> SourceIngestResult:
    if file:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        if len(content) > service.settings.library_multipart_upload_max_bytes:
            raise HTTPException(status_code=413, detail={"code": "upload_too_large", "message": "Use direct GCS upload for files larger than the multipart upload limit."})
        safe_source_id = _safe_id(source_id or f"upload-{sha1((file.filename or 'source').encode() + content[:256]).hexdigest()[:12]}")
        safe_library_id = _safe_id(library_id)
        extension = _extension(file.filename, file.content_type)
        raw_object = f"raw/{safe_library_id}/institutional_upload/{safe_source_id}/source.{extension}"
        source_doc = _library_upload_source_doc(
            service,
            library_id=safe_library_id,
            source_id=safe_source_id,
            raw_object=raw_object,
            extension=extension,
            title=title or file.filename or safe_source_id,
            author_name=author_name,
            work_id=work_id,
            domain=domain,
            notes=notes,
            url=f"upload://{library_id}/{safe_source_id}/{file.filename or 'source'}",
        )
        service._store_raw_object(raw_object, content)
        indexed = service.elastic.index_source_metadata(source_doc)
        return SourceIngestResult(
            source_id=safe_source_id,
            gcs_raw_path=source_doc["gcs_raw_path"],
            gcs_ocr_path=source_doc["gcs_ocr_path"],
            gcs_normalized_path=source_doc["gcs_normalized_path"],
            indexed=indexed,
            ingestion_status=source_doc["ingestion_status"],
            note="Library source uploaded and registered. Start OCR/indexing from the source status panel.",
            metadata=source_doc,
        )

    if not url:
        raise HTTPException(status_code=400, detail="Provide either a file upload or a source URL.")

    ingest_result = await service.ingest(
        SourceIngestRequest(
            provider=provider,
            source_id=source_id or f"url-{sha1(url.encode()).hexdigest()[:12]}",
            url=url,
            work_id=work_id,
            title=title,
            library_id=library_id,
            approved=True,
        )
    )
    source_doc = ingest_result.metadata | {
        "ocr_mode": "full",
        "ocr_full_document": True,
        "ocr_max_pages": None,
        "author_name": author_name or ingest_result.metadata.get("author_name", "Unknown"),
        "domain": domain or ingest_result.metadata.get("domain", "classical texts"),
        "notes": notes,
        "processing_job_id": f"job-{ingest_result.source_id}",
        "processing_job": {
            "job_id": f"job-{ingest_result.source_id}",
            "source_id": ingest_result.source_id,
            "status": "queued",
            "progress_percent": 5,
            "note": "URL source stored in GCS raw vault; full OCR job queued.",
        },
        "job_events": [
            {
                "status": "queued",
                "lifecycle_status": ingest_result.ingestion_status,
                "note": "URL source stored in GCS raw vault; full OCR job queued.",
                "progress_percent": 5,
            }
        ],
        "graph_status": "pending_after_ocr",
    }
    service.elastic.index_source_metadata(source_doc)
    return ingest_result.model_copy(update={"note": "Library source ingested and registered. Start OCR/indexing from the source status panel.", "metadata": source_doc})


@router.post(
    "/{library_id}/relationships/analyze",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def analyze_library_relationships(
    library_id: str,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> dict:
    sources = service.elastic.library_sources(library_id)
    if not sources:
        raise HTTPException(status_code=404, detail=f"No indexed sources found for library_id '{library_id}'.")
    passage_samples = service.elastic.library_passage_samples(library_id)
    seed_edges = service.elastic.library_relationship_graph(library_id)
    analyst = LibraryRelationshipAnalyst(service.settings)
    analysis = analyst.analyze(library_id, sources, passage_samples, seed_edges)
    proposal_result = CatalogCuratorService(service.settings, service.elastic).store_relationship_analysis(library_id, analysis["edges"])
    return sanitize_mapping({
        "library_id": library_id,
        "model_used": analysis["model_used"],
        "model_assisted": analysis["model_assisted"],
        "library_profile": analysis["library_profile"],
        "relationship_edge_count": 0,
        "relationship_proposal_count": proposal_result["stored_proposal_count"],
        "rejected_relations": analysis["rejected_relations"],
        "edges": analysis["edges"],
    })


@router.get("/{library_id}/relationships")
async def library_relationships(
    library_id: str,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> dict:
    edges = service.elastic.library_relationship_graph(library_id)
    return {
        "library_id": library_id,
        "relationship_edge_count": len(edges),
        "edges": sanitize_mapping(edges),
    }


@router.post(
    "/{library_id}/shamsiyya-docx-import",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def import_shamsiyya_docx_library(
    library_id: str,
    files: list[UploadFile] = File(...),
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> dict:
    if library_id != SHAMSIYYA_LIBRARY_ID:
        raise HTTPException(
            status_code=400,
            detail=f"Use library_id '{SHAMSIYYA_LIBRARY_ID}' for the Shamsiyya layered demo import.",
        )
    uploaded: list[tuple[str, bytes]] = []
    total_bytes = 0
    for file in files:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"Uploaded file {file.filename} is empty.")
        total_bytes += len(content)
        if total_bytes > service.settings.shamsiyya_import_max_bytes:
            raise HTTPException(status_code=413, detail={"code": "upload_too_large", "message": "Shamsiyya import exceeds the configured batch size limit."})
        uploaded.append((file.filename or "source.docx", content))

    parser = ShamsiyyaLibraryParser(gcs_bucket=service.settings.gcs_bucket)
    payload = parser.parse_files(uploaded)
    indexed_sources: list[dict] = []
    for source_doc in payload["sources"]:
        source_passages = [passage for passage in payload["passages"] if passage["source_id"] == source_doc["source_id"]]
        raw_text = parser.raw_text_for_source(source_doc, source_passages).encode("utf-8")
        service._store_raw_object(source_doc["raw_object"], raw_text)
        docx_pages = [
            {
                "page_number": int(passage.get("passage_order") or index),
                "text": passage.get("text_raw", ""),
                "text_layer": passage.get("text_raw", ""),
                "vision_text": "",
                "confidence": float(passage.get("ocr_confidence", 0.96)),
                "extraction_method": passage.get("extraction_method", "docx_text_layer_author_split"),
                "page_ref": passage.get("page_ref"),
                "section_ref": passage.get("section_ref"),
            }
            for index, passage in enumerate(source_passages, start=1)
        ]
        service.ocr._store_json(
            source_doc["gcs_ocr_path"],
            {
                "source_id": source_doc["source_id"],
                "engine": "docx_text_layer_splitter",
                "status": "docx_text_layer_completed",
                "page_count": len(docx_pages),
                "pages": docx_pages,
            },
        )
        service.ocr._store_jsonl(source_doc["gcs_normalized_path"], source_passages)
        indexed_count = service.elastic.index_extracted_passages(source_doc, source_passages)
        source_doc = {
            **source_doc,
            "indexed_passage_count": indexed_count,
            "raw_object": None,
            "ocr_object": None,
            "normalized_object": None,
        }
        service.elastic.index_source_metadata(source_doc)
        indexed_sources.append(source_doc)
    indexed_edges = service.elastic.index_library_relationship_edges(payload["library_id"], payload.get("edges", []))

    response = {
        "library_id": payload["library_id"],
        "source_count": len(indexed_sources),
        "passage_count": sum(source["indexed_passage_count"] for source in indexed_sources),
        "relationship_edge_count": indexed_edges,
        "sources": [
            {
                "source_id": source["source_id"],
                "work_id": source["work_id"],
                "title": source["title"],
                "author_name": source["author_name"],
                "text_layer": source["text_layer"],
                "indexed_passage_count": source["indexed_passage_count"],
                "gcs_raw_path": source["gcs_raw_path"],
                "gcs_normalized_path": source["gcs_normalized_path"],
            }
            for source in indexed_sources
        ],
        "note": (
            "Shamsiyya DOCX library imported as separate author-layer sources. "
            "Each source is searchable in Elastic under shamsiyya_hashiya_demo."
        ),
    }
    analyst = LibraryRelationshipAnalyst(service.settings)
    samples = _relationship_samples(payload["passages"])
    analysis = analyst.analyze(payload["library_id"], indexed_sources, samples, payload.get("edges", []))
    proposal_result = CatalogCuratorService(service.settings, service.elastic).store_relationship_analysis(payload["library_id"], analysis["edges"])
    response.update(
        {
            "gemini_relationship_edge_count": 0,
            "gemini_relationship_proposal_count": proposal_result["stored_proposal_count"],
            "relationship_model_used": analysis["model_used"],
            "relationship_model_assisted": analysis["model_assisted"],
            "relationship_profile": analysis["library_profile"],
            "relationship_rejected_relations": analysis["rejected_relations"],
        }
    )
    return response


def _extension(filename: str | None, content_type: str | None) -> str:
    lowered = (filename or "").lower()
    if lowered.endswith(".pdf") or content_type == "application/pdf":
        return "pdf"
    return "text"


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    if not safe:
        raise HTTPException(status_code=400, detail={"code": "upload_object_invalid", "message": "A valid library/source id is required."})
    return safe[:120]


def _validate_uploaded_object(service: SourceDiscoveryService, raw_object: str, content_type: str) -> None:
    if not service.settings.google_cloud_project:
        raise HTTPException(status_code=400, detail="Google Cloud project is not configured for direct GCS upload.")
    try:
        client = storage_client(service.settings)
        blob = client.bucket(service.settings.gcs_bucket).blob(raw_object)
        if not blob.exists():
            raise HTTPException(status_code=400, detail={"code": "upload_object_invalid", "message": "Uploaded object was not found in the GCS vault."})
        blob.reload()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not validate uploaded object: {exc}") from exc
    if blob.size and blob.size > service.settings.library_direct_upload_max_bytes:
        raise HTTPException(status_code=413, detail={"code": "upload_too_large", "message": "Manual GCS import required for files larger than the direct upload limit."})
    extension = _extension(raw_object, content_type)
    if extension == "pdf" and content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail={"code": "upload_object_invalid", "message": "Uploaded object content type does not match the requested file type."})


def _library_upload_source_doc(
    service: SourceDiscoveryService,
    *,
    library_id: str,
    source_id: str,
    raw_object: str,
    extension: str,
    title: str,
    author_name: str | None,
    work_id: str | None,
    domain: str | None,
    notes: str | None,
    url: str,
) -> dict:
    return {
        "source_id": source_id,
        "work_id": work_id or source_id,
        "provider": "Institutional Upload",
        "title": title,
        "author_name": author_name or "Unknown",
        "domain": domain or "classical texts",
        "notes": notes,
        "url": url,
        "source_page_url": None,
        "download_url": None,
        "file_type": extension,
        "license_note": "Institution-owned or user-uploaded source; verify rights before public redistribution.",
        "quality": 0.7,
        "library_id": library_id,
        "visibility": "private",
        "license_status": "institution_owned",
        "institution_owned": True,
        "ingestion_status": "raw_stored",
        "lifecycle_status": "raw_stored",
        "download_policy": "institutional_upload",
        "verification_status": "uploaded_by_user",
        "ocr_status": "ocr_pending",
        "ocr_engine": service.settings.ocr_engine,
        "ocr_mode": "full",
        "ocr_full_document": True,
        "ocr_max_pages": None,
        "processing_job_id": f"job-{source_id}",
        "processing_job": {
            "job_id": f"job-{source_id}",
            "source_id": source_id,
            "status": "queued",
            "progress_percent": 5,
            "note": "PDF stored in GCS raw vault; full OCR job queued.",
        },
        "job_events": [
            {
                "status": "queued",
                "lifecycle_status": "raw_stored",
                "note": "PDF stored in GCS raw vault; full OCR job queued.",
                "progress_percent": 5,
            }
        ],
        "graph_status": "pending_after_ocr",
        "gcs_raw_path": f"gs://{service.settings.gcs_bucket}/{raw_object}",
        "gcs_ocr_path": f"gs://{service.settings.gcs_bucket}/ocr/{library_id}/{source_id}/ocr.json",
        "gcs_normalized_path": f"gs://{service.settings.gcs_bucket}/normalized/{library_id}/{source_id}/passages.jsonl",
    }


def _relationship_samples(passages: list[dict], per_source: int = 4) -> list[dict]:
    counts: dict[str, int] = {}
    samples: list[dict] = []
    for passage in passages:
        source_id = passage.get("source_id", "unknown")
        counts[source_id] = counts.get(source_id, 0)
        if counts[source_id] >= per_source:
            continue
        samples.append(passage)
        counts[source_id] += 1
    return samples
