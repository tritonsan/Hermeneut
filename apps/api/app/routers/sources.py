from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_source_discovery_service
from app.models import (
    OcrCorrectionRequest,
    SourceDiscoverRequest,
    SourceHit,
    SourceIngestRequest,
    SourceIngestResult,
)
from app.services.job_queue import JobQueueNotConfiguredError, JobQueueService
from app.services.ocr_editor import OcrEditorService
from app.services.source_discovery import SourceDiscoveryService
from app.security import require_jury_or_admin, require_live_elastic, sanitize_mapping

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.post("/discover", response_model=list[SourceHit], dependencies=[Depends(require_jury_or_admin)])
async def discover(
    payload: SourceDiscoverRequest,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> list[SourceHit]:
    return await service.discover(payload)


@router.post(
    "/ingest",
    response_model=SourceIngestResult,
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def ingest(
    payload: SourceIngestRequest,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> SourceIngestResult:
    try:
        return await service.ingest(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/{source_id}/process",
    response_model=SourceIngestResult,
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def process_source(
    source_id: str,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> SourceIngestResult:
    try:
        source_doc = service._source_doc(source_id)
        if not source_doc:
            raise ValueError("Source is not known. Ingest or discover it before processing.")
        queued_doc = service._mark_source_job(
            source_doc,
            job_status="queued",
            lifecycle_status=source_doc.get("ingestion_status", "raw_stored"),
            progress_percent=5,
            note="Manual OCR/index job queued.",
            ocr_status="ocr_pending",
        )
        enqueue_result = await JobQueueService(service.settings).enqueue_source_processing(source_id)
        queued_doc = service._mark_source_job(
            queued_doc,
            job_status="queued",
            lifecycle_status=queued_doc.get("ingestion_status", "raw_stored"),
            progress_percent=8,
            note="Durable Cloud Run OCR/index job submitted.",
            ocr_status="ocr_pending",
            extra={
                "job_backend": enqueue_result["backend"],
                "cloud_run_job_name": enqueue_result["job_name"],
                "cloud_run_job_location": enqueue_result["location"],
                "cloud_run_operation_name": enqueue_result.get("operation_name"),
            },
        )
        return SourceIngestResult(
            source_id=source_id,
            gcs_raw_path=queued_doc.get("gcs_raw_path", ""),
            gcs_ocr_path=queued_doc.get("gcs_ocr_path"),
            gcs_normalized_path=queued_doc.get("gcs_normalized_path"),
            indexed=True,
            ingestion_status=queued_doc.get("ingestion_status", "queued"),
            note="Durable Cloud Run OCR/index job submitted; poll source status for progress.",
            metadata=queued_doc,
        )
    except JobQueueNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{source_id}/status")
async def source_status(
    source_id: str,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> dict:
    source_doc = service._source_doc(source_id)
    if not source_doc:
        raise HTTPException(status_code=404, detail="Source not found")
    library_id = str(source_doc.get("library_id") or "")
    work_id = str(source_doc.get("work_id") or "")
    edges = service.elastic.library_relationship_graph(library_id) if library_id else []
    direct = [
        edge for edge in edges
        if source_id in {str(edge.get("from_id") or edge.get("from") or ""), str(edge.get("to_id") or edge.get("to") or "")}
    ]
    work_edges = [
        edge for edge in edges
        if work_id and work_id in {str(edge.get("from_id") or edge.get("from") or ""), str(edge.get("to_id") or edge.get("to") or "")}
    ]
    total = len({str(edge.get("edge_id") or edge) for edge in [*direct, *work_edges]})
    if total:
        graph_status = "graph_loaded"
    elif service.elastic.mode() not in {"elasticsearch", "elastic_backup_preview"}:
        graph_status = "elastic_unavailable"
    elif str(source_doc.get("graph_status") or "").lower() in {"pending", "analysis_pending", "pending_after_ocr"}:
        graph_status = "analysis_pending"
    else:
        graph_status = "no_known_relationships"
    return {
        "source_id": source_id,
        "lifecycle": source_doc.get("ingestion_status", "discovered"),
        "lifecycle_status": source_doc.get("lifecycle_status", source_doc.get("ingestion_status", "discovered")),
        "progress_percent": source_doc.get("processing_job", {}).get("progress_percent", 100 if source_doc.get("ingestion_status") == "searchable" else 0),
        "graph_status": graph_status,
        "relationship_edge_count": total,
        "relationship_summary": {
            "direct_count": len(direct),
            "work_count": len(work_edges),
            "total_count": total,
            "status": graph_status,
        },
        "ocr_status": source_doc.get("ocr_status", "unknown"),
        "ocr_page_count": source_doc.get("ocr_page_count", 0),
        "ocr_total_pages": source_doc.get("ocr_total_pages"),
        "ocr_processed_pages": source_doc.get("ocr_processed_pages"),
        "ocr_next_page": source_doc.get("ocr_next_page"),
        "ocr_batch_size": source_doc.get("ocr_batch_size"),
        "ocr_resume_available": source_doc.get("ocr_resume_available", False),
        "ocr_full_document_max_pages": source_doc.get("ocr_full_document_max_pages"),
        "ocr_avg_confidence": source_doc.get("ocr_avg_confidence"),
        "ocr_quality_status": source_doc.get("ocr_quality_status"),
        "indexed_passage_count": source_doc.get("indexed_passage_count", 0),
        "metadata": sanitize_mapping(
            {
                "source_id": source_doc.get("source_id"),
                "title": source_doc.get("title"),
                "provider": source_doc.get("provider"),
                "url": source_doc.get("url"),
                "source_page_url": source_doc.get("source_page_url"),
                "file_type": source_doc.get("file_type"),
                "license_status": source_doc.get("license_status"),
                "license_note": source_doc.get("license_note"),
                "library_id": source_doc.get("library_id"),
                "work_id": source_doc.get("work_id"),
                "work_title": source_doc.get("work_title"),
                "author_name": source_doc.get("author_name"),
            }
        ),
    }


@router.get("/{source_id}/pages/{page_number}", dependencies=[Depends(require_jury_or_admin)])
async def source_page(
    source_id: str,
    page_number: int,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> dict:
    try:
        return OcrEditorService(service.settings).page(source_id, page_number)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{source_id}/pages/{page_number}/corrections",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def save_source_page_correction(
    source_id: str,
    page_number: int,
    payload: OcrCorrectionRequest,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> dict:
    try:
        return OcrEditorService(service.settings).save_correction(source_id, page_number, payload).model_dump()
    except ValueError as exc:
        if str(exc).startswith("ocr_correction_partial_failure"):
            raise HTTPException(status_code=500, detail={"code": "ocr_correction_partial_failure", "message": str(exc)}) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{source_id}/pages/{page_number}/gemini-audit",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def source_page_gemini_audit(
    source_id: str,
    page_number: int,
    service: SourceDiscoveryService = Depends(get_source_discovery_service),
) -> dict:
    try:
        return await OcrEditorService(service.settings).gemini_audit(source_id, page_number)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
