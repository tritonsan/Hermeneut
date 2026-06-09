import httpx
from fastapi import APIRouter, Depends

from app.dependencies import get_elastic_service
from app.models import HealthStatus
from app.services.elastic_service import ElasticService
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api", tags=["health"])


def elastic_mcp_status(settings: Settings) -> str:
    if not settings.elastic_mcp_endpoint:
        return "not-configured"
    if not settings.elastic_mcp_api_key:
        return "configured"

    headers = {"Authorization": f"ApiKey {settings.elastic_mcp_api_key}"}
    try:
        response = httpx.get(settings.elastic_mcp_endpoint, headers=headers, timeout=3.0)
    except httpx.HTTPError:
        return "unreachable"

    if response.status_code in {401, 403}:
        return "unreachable"
    if response.status_code < 500:
        return "reachable"
    return "unreachable"


def gemini_grounding_status(settings: Settings) -> str:
    if not settings.google_cloud_project:
        return "not_configured"
    if not settings.gemini_research_model:
        return "not_configured"
    return "available"


def job_queue_status(settings: Settings) -> str:
    if settings.job_backend != "cloud_run_jobs":
        return "not_configured"
    if not settings.google_cloud_project or not settings.cloud_run_job_location or not settings.cloud_run_job_name:
        return "misconfigured"
    return "configured"


def run_worker_status(settings: Settings) -> str:
    if settings.run_execution_mode.lower() != "async":
        return "sync_fallback"
    queue = job_queue_status(settings)
    if queue == "configured":
        return "cloud_run_jobs"
    return queue


@router.get("/health", response_model=HealthStatus)
def health(elastic: ElasticService = Depends(get_elastic_service)) -> HealthStatus:
    settings = get_settings()
    schema = elastic.schema_status()
    return HealthStatus(
        status="ok",
        elastic=elastic.health(),
        elastic_mcp=elastic_mcp_status(settings),
        google_agent="configured" if settings.agent_builder_agent_id else "not-configured",
        gcs="configured" if settings.google_cloud_project else "not-configured",
        gemini_grounding=gemini_grounding_status(settings),
        job_queue=job_queue_status(settings),
        elastic_schema_version=str(schema.get("version") or "unknown"),
        run_worker=run_worker_status(settings),
        run_snapshot_store=str(schema.get("run_snapshot_store") or "unknown"),
        index_aliases=dict(schema.get("aliases") or {}),
        public_demo=settings.public_demo_mode,
    )
