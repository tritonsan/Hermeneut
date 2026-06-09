from fastapi.testclient import TestClient

from app.dependencies import get_app_settings, get_catalog_curator_service, get_elastic_service
from app.dependencies import get_source_discovery_service
from app.main import app
from app.services.catalog_curator import CatalogCuratorRepository, CatalogCuratorService
from app.services.elastic_service import ElasticService
from app.settings import Settings


def test_preview_catalog_health_is_read_only_and_detects_relationship_gaps():
    elastic = ElasticService(Settings())
    health = CatalogCuratorRepository(elastic).health("shamsiyya_hashiya_demo")

    assert health["backend"] == "elastic_backup_preview"
    assert health["read_only"] is True
    assert 0 <= health["score"] <= 100
    assert "work_without_relationships" in health["counts"]


def test_source_analysis_produces_proposals_without_mutating_preview_catalog():
    elastic = ElasticService(Settings())
    service = CatalogCuratorService(Settings(), elastic)
    before = elastic.preview.source("shamsiyya-katibi-matn").copy()

    result = service.analyze_source("shamsiyya-katibi-matn")

    assert result["analysis_job"]["status"] == "completed"
    assert result["stored_proposal_count"] == 0
    assert elastic.preview.source("shamsiyya-katibi-matn") == before


def test_relationship_analysis_is_converted_to_high_risk_proposal():
    service = CatalogCuratorService(Settings(), ElasticService(Settings()))
    job = service._job("catalog_relationships", "demo")

    proposals = service.relationship_proposals(
        job,
        [{"from_id": "work-a", "to_id": "work-b", "relation": "comments_on", "confidence": 0.9}],
    )

    assert proposals[0].proposal_type == "relationship"
    assert proposals[0].risk_level == "high"
    assert proposals[0].status == "needs_review"


def test_catalog_curator_mutation_is_blocked_in_preview_mode():
    settings = Settings(admin_api_token="curator-admin")
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_catalog_curator_service] = lambda: CatalogCuratorService(settings, ElasticService(settings))
    try:
        response = TestClient(app).post(
            "/api/catalog-curator/sources/shamsiyya-katibi-matn/analyze",
            headers={"Authorization": "Bearer curator-admin"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "preview_read_only"


def test_library_relationship_analysis_accepts_jury_access_without_admin_token():
    settings = Settings(
        elasticsearch_url="https://elastic.example",
        elasticsearch_api_key="key",
        jury_access_enabled=True,
        jury_proxy_token="jury-token",
    )

    class FakeIndices:
        def exists(self, index: str):
            return True

        def exists_alias(self, name: str):
            return True

        def create(self, *args, **kwargs):
            return {}

        def put_alias(self, *args, **kwargs):
            return {}

        def refresh(self, *args, **kwargs):
            return {}

    class FakeClient:
        indices = FakeIndices()

        def index(self, *args, **kwargs):
            return {"result": "created"}

        def search(self, *args, **kwargs):
            return {"hits": {"hits": []}}

    class FakeElastic:
        client = FakeClient()

        def library_sources(self, library_id: str):
            return [{"source_id": "source-a", "work_id": "work-a", "title": "Source A", "library_id": library_id}]

        def library_passage_samples(self, library_id: str):
            return [{"passage_id": "p1", "work_id": "work-a", "text_raw": "sample"}]

        def library_relationship_graph(self, library_id: str):
            return []

        def save_catalog_analysis_job(self, job):
            return True

        def save_catalog_proposal(self, proposal):
            return True

        def health(self):
            return "connected"

    class FakeAnalyst:
        def __init__(self):
            self.elastic = FakeElastic()
            self.settings = settings

    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_elastic_service] = FakeElastic
    app.dependency_overrides[get_source_discovery_service] = FakeAnalyst
    try:
        response = TestClient(app).post(
            "/api/libraries/demo/relationships/analyze",
            headers={"X-Hermeneut-Jury-Token": "jury-token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["library_id"] == "demo"
    assert "relationship_proposal_count" in response.json()
    assert "prompt_excerpt" not in response.text
    assert "model_trace" not in response.text
