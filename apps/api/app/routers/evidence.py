from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_elastic_service, get_run_repository
from app.models import EvidenceItem
from app.services.elastic_service import ElasticService
from app.services.run_repository import RunRepository

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


@router.get("/{passage_id}/context")
def evidence_context(
    passage_id: str,
    window: int = 2,
    elastic: ElasticService = Depends(get_elastic_service),
) -> dict:
    result = elastic.passage_context(passage_id, window)
    if not result.get("items"):
        raise HTTPException(status_code=404, detail="Passage context not found")
    return result


@router.get("/{run_id}", response_model=list[EvidenceItem])
def evidence(run_id: str, runs: RunRepository = Depends(get_run_repository)) -> list[EvidenceItem]:
    run = runs.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.evidence
