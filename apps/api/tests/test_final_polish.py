from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.dependencies import get_app_settings, get_research_agent
from app.main import app
from app.models import RunCreate, RunMode
from app.services.agent import ResearchAgent
from app.services.catalog_quality import classify_catalog_record
from app.services.elastic_service import ElasticService
from app.services.run_repository import RunRepository
from app.settings import Settings


def test_catalog_quality_quarantines_filename_and_unknown_author():
    record = classify_catalog_record({"work_id": "w1", "title": "juz1.pdf", "author_name": "Unknown"}, "work")

    assert record["catalog_review_status"] == "needs_review"
    assert {"filename_like_title", "unresolved_author"} <= set(record["catalog_review_reasons"])
    assert record["metadata_quality_score"] < 100


def test_stale_active_run_becomes_retryable():
    service = ElasticService(Settings())
    agent = ResearchAgent(service)
    run = agent.initial_run(RunCreate(mode=RunMode.library, passage="قيل إن زيدا ممكن"))
    repository = RunRepository(service)
    repository.get_metadata = lambda _run_id: {  # type: ignore[method-assign]
        "queued_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    }

    view = repository.execution_view(run)

    assert view.execution_status == "stalled"
    assert view.status.value == "failed"
    assert view.retryable is True
    assert view.delayed is True


def test_production_preview_rejects_run_creation():
    settings = Settings(environment="production", jury_access_enabled=True, jury_proxy_token="jury-token")
    agent = ResearchAgent(ElasticService(settings))
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_research_agent] = lambda: agent
    try:
        response = TestClient(app).post(
            "/api/runs",
            json={"mode": "library", "passage": "قيل إن زيدا ممكن"},
            headers={"X-Hermeneut-Jury-Token": "jury-token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "live_elastic_required"
