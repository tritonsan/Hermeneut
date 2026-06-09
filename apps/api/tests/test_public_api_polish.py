import json

from fastapi.testclient import TestClient

from app.main import app
from app.models import RunCreate, RunMode
from app.security import sanitize_library_search, sanitize_mapping, sanitize_run
from app.services.agent import ResearchAgent
from app.services.elastic_service import ElasticService
from app.services.web_research import WebResearchService
from app.settings import Settings


def test_library_search_marks_preview_backend_when_elastic_is_offline():
    service = ElasticService(Settings())

    result = service.search_library("")

    assert result["meta"]["backend"] == "elastic_backup_preview"
    assert result["meta"]["read_only"] is True
    assert result["meta"]["counts"]["passages"] > 0


def test_library_run_report_uses_same_decision_tier():
    agent = ResearchAgent(ElasticService(Settings()), WebResearchService(Settings()))

    run = agent.run(
        RunCreate(
            mode=RunMode.library,
            passage="ذكر بعضهم أن العالم لا أول لوجوده وأنه صدر عن الأول بالضرورة.",
            domain_hint="kalam/philosophy",
        )
    )

    assert run.final_report.startswith(f"Decision tier: {run.decision_tier}")


def test_sanitize_run_removes_debug_trace_and_raw_prompt_payloads():
    agent = ResearchAgent(ElasticService(Settings()), WebResearchService(Settings()))
    run = agent.run(RunCreate(mode=RunMode.library, passage="قيل إن ما كان وجوده من غيره فهو ممكن"))
    run.trace_events[0].raw_payload["prompt_excerpt"] = "secret prompt"
    run.evidence[0].tool_trace["elastic_query"] = {"raw": "query"}

    public_run = sanitize_run(run)

    assert public_run.trace_events == []
    assert "elastic_query" not in public_run.evidence[0].tool_trace
    assert not str(public_run.evidence[0].source_url or "").startswith("gs://")


def test_evidence_items_include_public_location_fields():
    agent = ResearchAgent(ElasticService(Settings()), WebResearchService(Settings()))
    run = agent.run(RunCreate(mode=RunMode.library, passage="قيل إن ما كان وجوده من غيره فهو ممكن"))

    item = run.evidence[0]

    assert item.library_id == "demo_kalam"
    assert item.source_id
    assert item.location_label
    assert item.citation_hint
    assert item.page_ref


def test_passage_context_is_library_scoped_and_public():
    service = ElasticService(Settings())

    result = service.passage_context("p-ghazali-001", window=2)

    assert result["library_id"] == "demo_kalam"
    assert result["items"]
    assert {item["library_id"] for item in result["items"]} == {"demo_kalam"}
    assert all("gcs_raw_path" not in item for item in result["items"])


def test_passage_context_uses_passage_id_field_when_elastic_id_differs():
    service = ElasticService(Settings())
    docs = [
        {
            "passage_id": "elastic-auto-prev",
            "text_raw": "previous",
            "work_id": "ghazali-tahafut",
            "source_id": "openiti-ghazali-tahafut",
            "library_id": "demo_kalam",
            "source_page": "9",
            "page_ref": "ocr:9:1",
        },
        {
            "passage_id": "elastic-auto-target",
            "text_raw": "target",
            "work_id": "ghazali-tahafut",
            "source_id": "openiti-ghazali-tahafut",
            "library_id": "demo_kalam",
            "source_page": "10",
            "page_ref": "ocr:10:1",
        },
        {
            "passage_id": "elastic-auto-next",
            "text_raw": "next",
            "work_id": "ghazali-tahafut",
            "source_id": "openiti-ghazali-tahafut",
            "library_id": "demo_kalam",
            "source_page": "11",
            "page_ref": "ocr:11:1",
        },
    ]

    class FakeElasticClient:
        def get(self, **_kwargs):
            raise KeyError("auto-generated Elasticsearch _id")

        def search(self, **kwargs):
            query = kwargs.get("query", {})
            if query.get("term") == {"passage_id": "elastic-auto-target"}:
                return {"hits": {"hits": [{"_source": docs[1]}]}}
            return {"hits": {"hits": [{"_source": doc} for doc in docs]}}

    service.client = FakeElasticClient()
    service.health = lambda: "connected"  # type: ignore[method-assign]

    result = service.passage_context("elastic-auto-target", window=1)

    assert [item["passage_id"] for item in result["items"]] == [
        "elastic-auto-prev",
        "elastic-auto-target",
        "elastic-auto-next",
    ]
    assert result["items"][1]["citation_hint"]
    assert result["items"][1]["library_id"] == "demo_kalam"


def test_run_snapshot_restores_from_json_fallback():
    service = ElasticService(Settings())
    agent = ResearchAgent(ElasticService(Settings()), WebResearchService(Settings()))
    run = agent.initial_run(RunCreate(mode=RunMode.library, passage="قيل إن زيدا ممكن"))
    snapshot = json.dumps(run.model_dump(mode="json"), ensure_ascii=False)

    class FakeElasticClient:
        def get(self, **_kwargs):
            return {"found": True, "_source": {"run_doc_json": snapshot}}

    service.client = FakeElasticClient()
    service.health = lambda: "connected"  # type: ignore[method-assign]

    restored = service.get_run_snapshot(run.run_id)

    assert restored is not None
    assert restored.run_id == run.run_id
    assert restored.input_passage == run.input_passage


def test_run_snapshot_failure_does_not_raise():
    service = ElasticService(Settings())
    agent = ResearchAgent(ElasticService(Settings()), WebResearchService(Settings()))
    run = agent.initial_run(RunCreate(mode=RunMode.library, passage="قيل إن زيدا ممكن"))

    class FakeIndices:
        def exists(self, **_kwargs):
            return True

        def put_settings(self, **_kwargs):
            return None

        def put_mapping(self, **_kwargs):
            return None

    class FailingElasticClient:
        indices = FakeIndices()

        def index(self, **_kwargs):
            raise RuntimeError("snapshot index is unavailable")

    service.client = FailingElasticClient()
    service.health = lambda: "connected"  # type: ignore[method-assign]

    assert service.write_run_snapshot(run) is False


def test_sanitize_mapping_removes_nested_prompt_excerpts():
    payload = {
        "edges": [
            {
                "model_trace": {
                    "prompt_excerpt": "secret",
                    "model": "gemini",
                    "gcs_raw_path": "gs://bucket/raw.pdf",
                }
            }
        ]
    }

    assert sanitize_mapping(payload) == {"edges": [{}]}


def test_library_search_public_response_whitelists_private_paths():
    payload = {
        "meta": {"backend": "elasticsearch", "counts": {"passages": 1}},
        "passages": [
            {
                "passage_id": "p1",
                "text_raw": "quote",
                "work_id": "w1",
                "work_title": "Readable Work",
                "source_id": "s1",
                "source_title": "Readable Source",
                "page_ref": "12",
                "location_label": "Author · Work · Source · 12",
                "citation_hint": "Author; Work; source: Source; loc. 12",
                "gcs_raw_path": "gs://bucket/raw.pdf",
                "gcs_ocr_path": "gs://bucket/ocr.json",
                "signed_url": "https://signed.example",
                "raw_object": "raw/private",
            }
        ],
        "sources": [
            {
                "source_id": "s1",
                "title": "Readable Source",
                "url": "gcs://private/source.pdf",
                "gcs_normalized_path": "gs://bucket/passages.jsonl",
            }
        ],
    }

    public = sanitize_library_search(payload)
    serialized = str(public)

    assert public["passages"][0]["citation_hint"].startswith("Author")
    assert "gcs_" not in serialized
    assert "signed_url" not in serialized
    assert "raw_object" not in serialized
    assert "gcs://" not in serialized


def test_admin_mutation_requires_token_when_configured():
    client = TestClient(app)

    response = client.post("/api/library/bootstrap-elastic")

    assert response.status_code in {401, 503}


def test_source_status_is_public_safe_and_pages_are_operator_gated():
    client = TestClient(app)

    status = client.get("/api/sources/openiti-ghazali-tahafut/status")
    page = client.get("/api/sources/openiti-ghazali-tahafut/pages/1")

    assert status.status_code == 200
    serialized = status.text
    assert "gcs_raw_path" not in serialized
    assert "gcs_ocr_path" not in serialized
    assert "gcs_normalized_path" not in serialized
    assert page.status_code in {401, 403, 503}


def test_public_research_and_nonempty_library_search_require_jury_access():
    client = TestClient(app)

    run_response = client.post("/api/runs", json={"mode": "library", "passage": "قيل إن زيدا ممكن"})
    search_response = client.get("/api/library/search?q=%D8%B4%D9%85%D8%B3%D9%8A%D8%A9")
    catalog_response = client.get("/api/library/search")

    assert run_response.status_code == 403
    assert run_response.json()["detail"]["code"] == "jury_access_required"
    assert search_response.status_code == 403
    assert catalog_response.status_code == 200
