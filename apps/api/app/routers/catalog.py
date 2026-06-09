from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import get_app_settings, get_elastic_service
from app.models import CatalogSearchRequest
from app.security import require_jury_or_admin
from app.services.catalog import CatalogIntelligenceService
from app.services.elastic_service import ElasticService
from app.settings import Settings

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.post("/search")
async def catalog_search(
    payload: CatalogSearchRequest,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    elastic: ElasticService = Depends(get_elastic_service),
) -> list[dict]:
    if payload.endpoint_url:
        require_jury_or_admin(request, settings)
        _require_live_elastic(elastic)
    return await CatalogIntelligenceService(settings).search(payload)


@router.post("/harvest")
async def catalog_harvest(
    payload: CatalogSearchRequest,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    elastic: ElasticService = Depends(get_elastic_service),
) -> dict:
    require_jury_or_admin(request, settings)
    _require_live_elastic(elastic)
    records = await CatalogIntelligenceService(settings).harvest(payload)
    return {
        "indexed": len(records),
        "records": records,
        "note": "Catalog records are source leads only; they are not textual evidence.",
    }


@router.get("/records")
async def catalog_records(
    query: str = Query(""),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    return {
        "records": CatalogIntelligenceService(settings).indexed_records(query or "*"),
        "evidence_status": "catalog_lead",
    }


def _require_live_elastic(elastic: ElasticService) -> None:
    if elastic.health() != "connected":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "preview_read_only",
                "message": "External catalog lookup and harvest require live Elastic. Backup preview is read-only.",
            },
        )
