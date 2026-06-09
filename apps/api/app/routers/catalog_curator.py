from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_catalog_curator_service, get_job_queue_service
from app.models import CatalogBulkApproveRequest, CatalogProposalDecisionRequest
from app.security import require_jury_or_admin, require_live_elastic, sanitize_mapping
from app.services.catalog_curator import CatalogCuratorService, LOW_RISK_METADATA_FIELDS
from app.services.job_queue import JobQueueNotConfiguredError, JobQueueService

router = APIRouter(prefix="/api/catalog-curator", tags=["catalog-curator"])


@router.get("/inbox", dependencies=[Depends(require_jury_or_admin)])
def inbox(
    library_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    service: CatalogCuratorService = Depends(get_catalog_curator_service),
) -> dict:
    return {
        "backend": service.elastic.mode(),
        "read_only": not service.repository.connected,
        "proposals": sanitize_mapping(service.repository.inbox(library_id=library_id, status=status, limit=limit)),
    }


@router.get("/health", dependencies=[Depends(require_jury_or_admin)])
def health(
    library_id: str | None = None,
    service: CatalogCuratorService = Depends(get_catalog_curator_service),
) -> dict:
    return sanitize_mapping(service.repository.health(library_id))


@router.post(
    "/sources/{source_id}/analyze",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def analyze_source(
    source_id: str,
    service: CatalogCuratorService = Depends(get_catalog_curator_service),
    queue: JobQueueService = Depends(get_job_queue_service),
) -> dict:
    try:
        if not service._source(source_id):
            raise ValueError("Source not found.")
        queued = await queue.enqueue_catalog_source_analysis(source_id)
        return sanitize_mapping({"status": "queued", "source_id": source_id, "job_backend": queued.get("backend"), "operation_name": queued.get("operation_name")})
    except JobQueueNotConfiguredError:
        return sanitize_mapping(service.analyze_source(source_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/libraries/{library_id}/analyze",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
async def analyze_library(
    library_id: str,
    service: CatalogCuratorService = Depends(get_catalog_curator_service),
    queue: JobQueueService = Depends(get_job_queue_service),
) -> dict:
    try:
        queued = await queue.enqueue_catalog_library_analysis(library_id)
        return sanitize_mapping({"status": "queued", "library_id": library_id, "job_backend": queued.get("backend"), "operation_name": queued.get("operation_name")})
    except JobQueueNotConfiguredError:
        return sanitize_mapping(service.analyze_library(library_id))


@router.post(
    "/proposals/{proposal_id}/approve",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
def approve_proposal(
    proposal_id: str,
    payload: CatalogProposalDecisionRequest,
    service: CatalogCuratorService = Depends(get_catalog_curator_service),
) -> dict:
    proposal = service.repository.proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Catalog proposal not found.")
    return sanitize_mapping(service.repository.decide(proposal, "approve", payload.note, payload.edited_proposed_value))


@router.post(
    "/proposals/{proposal_id}/reject",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
def reject_proposal(
    proposal_id: str,
    payload: CatalogProposalDecisionRequest,
    service: CatalogCuratorService = Depends(get_catalog_curator_service),
) -> dict:
    proposal = service.repository.proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Catalog proposal not found.")
    return sanitize_mapping(service.repository.decide(proposal, "reject", payload.note))


@router.post(
    "/proposals/bulk-approve-metadata",
    dependencies=[Depends(require_jury_or_admin), Depends(require_live_elastic)],
)
def bulk_approve_metadata(
    payload: CatalogBulkApproveRequest,
    service: CatalogCuratorService = Depends(get_catalog_curator_service),
) -> dict:
    applied = []
    rejected = []
    for proposal_id in payload.proposal_ids:
        proposal = service.repository.proposal(proposal_id)
        fields = set((proposal or {}).get("proposed_value", {}).get("fields", {}).keys())
        if not proposal or proposal.get("proposal_type") != "metadata" or not fields.issubset(LOW_RISK_METADATA_FIELDS | {"catalog_analysis_version", "catalog_analyzed_at"}):
            rejected.append(proposal_id)
            continue
        applied.append(service.repository.decide(proposal, "approve", payload.note))
    return {"applied_count": len(applied), "rejected_proposal_ids": rejected, "proposals": sanitize_mapping(applied)}
