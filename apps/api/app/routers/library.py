from fastapi import APIRouter, Depends, Request

from app.dependencies import get_elastic_service
from app.security import require_admin, require_jury_or_admin, sanitize_library_search
from app.services.elastic_service import ElasticService
from app.services.elastic_tools import elastic_agent_builder_tools
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/search")
def search_library(
    request: Request,
    q: str = "",
    elastic: ElasticService = Depends(get_elastic_service),
    settings: Settings = Depends(get_settings),
):
    if q.strip():
        require_jury_or_admin(request, settings)
    result = elastic.search_library(q)
    return sanitize_library_search(result)


@router.get("/elastic-status")
def elastic_status(elastic: ElasticService = Depends(get_elastic_service)):
    return {"mode": elastic.mode(), "health": elastic.health(), "indices": elastic.index_counts()}


@router.get("/elastic-tools")
def elastic_tools():
    return {"tools": elastic_agent_builder_tools()}


@router.post("/bootstrap-elastic", dependencies=[Depends(require_admin)])
def bootstrap_elastic(elastic: ElasticService = Depends(get_elastic_service)):
    return elastic.bootstrap_seed_corpus()
