import asyncio
import json

from app.models import AgentRun, DetectedContext, RunCreate, RunMode, RunStatus, SourceIngestResult
from app.services.agent import ResearchAgent
from app.services.elastic_service import ElasticService
from app.services.job_queue import JobQueueService
from app.services.run_repository import RunRepository
from app.services.source_lifecycle import SourceLifecycleService
from app.services.web_research import WebResearchService
from app.settings import Settings


def test_run_job_executes_sync_runner_outside_active_event_loop(monkeypatch):
    from app.jobs import process_source

    observed = {}

    def fake_execute_run_job(run_id: str) -> int:
        observed["run_id"] = run_id
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            observed["inside_running_loop"] = False
        else:
            observed["inside_running_loop"] = True
        return 0

    monkeypatch.setenv("HERMENEUT_JOB_KIND", "run")
    monkeypatch.setenv("HERMENEUT_RUN_ID", "run-thread-test")
    monkeypatch.setattr(process_source, "_execute_run_job", fake_execute_run_job)

    assert asyncio.run(process_source._main()) == 0
    assert observed == {"run_id": "run-thread-test", "inside_running_loop": False}


def test_run_snapshot_stores_payload_and_worker_metadata():
    service = ElasticService(Settings())
    agent = ResearchAgent(ElasticService(Settings()), WebResearchService(Settings()))
    payload = RunCreate(mode=RunMode.library, passage="قيل إن زيدا ممكن")
    run = agent.initial_run(payload)

    class FakeIndices:
        def __init__(self):
            self.created = set()

        def exists_alias(self, **_kwargs):
            return False

        def exists(self, index):
            return index in self.created

        def create(self, index, **_kwargs):
            self.created.add(index)

        def put_settings(self, **_kwargs):
            return None

        def put_mapping(self, **_kwargs):
            return None

        def refresh(self, **_kwargs):
            return None

    class FakeElasticClient:
        def __init__(self):
            self.indices = FakeIndices()
            self.docs = {}

        def get(self, index, id):
            doc = self.docs.get((index, id))
            return {"found": bool(doc), "_source": doc or {}}

        def index(self, index, id, document):
            self.indices.created.add(index)
            self.docs[(index, id)] = document

    fake = FakeElasticClient()
    service.client = fake
    service.health = lambda: "connected"  # type: ignore[method-assign]

    repository = RunRepository(service)
    repository.save_initial(run, payload)
    repository.mark_enqueued(run, "operations/test-run")

    stored = next(doc for (_index, _id), doc in fake.docs.items() if _id == run.run_id)
    assert json.loads(stored["payload_json"])["passage"] == payload.passage
    assert stored["job_operation_name"] == "operations/test-run"
    assert stored["worker_status"] == "enqueued"
    assert stored["schema_version"]


def test_source_lifecycle_falls_back_to_archive_text_when_pdf_download_fails():
    payload = RunCreate(mode=RunMode.open_discovery, passage="قال في الشمسية", library_id="shamsiyya_hashiya_demo")
    agent = ResearchAgent(ElasticService(Settings()), WebResearchService(Settings()))
    run = agent.initial_run(payload).model_copy(
        update={
            "source_lifecycle_records": [
                {
                    "source_id": "ia-test",
                    "provider": "Internet Archive",
                    "provenance": "internet_archive_resolver",
                    "download_url": "https://archive.org/download/item/broken.pdf",
                    "source_page_url": "https://archive.org/details/item",
                    "file_type": "pdf",
                    "lifecycle_status": "download_approved",
                    "work_id": "qutb-razi-tahrir-shamsiyya",
                    "title": "Tahrir test",
                }
            ]
        }
    )

    class FakeAgent:
        def _rank_candidates(self, _evidence):
            return []

        def _write_report(self, *_args, **_kwargs):
            return "No claim-worthy evidence."

    class FakeElastic:
        def search_passages(self, *_args, **_kwargs):
            return []

        def semantic_passage_lookup(self, *_args, **_kwargs):
            return []

        def write_evidence_memory(self, _records):
            return 0

    class FakeRuns:
        def __init__(self):
            self.snapshots = []

        def save_snapshot(self, snapshot, *args, **kwargs):
            self.snapshots.append(snapshot)

        def get_payload(self, _run_id):
            return payload

    class FakeSources:
        def __init__(self):
            self.ingest_urls = []

        async def ingest(self, request):
            self.ingest_urls.append(request.url)
            if request.url.endswith("broken.pdf"):
                raise ValueError("download failed")
            return SourceIngestResult(
                source_id=request.source_id,
                gcs_raw_path="gs://bucket/raw.txt",
                indexed=True,
                ingestion_status="raw_stored",
                note="stored",
                metadata={
                    "download_url": request.url,
                    "file_type": "text",
                    "library_id": request.library_id,
                    "indexed_passage_count": 0,
                },
            )

        async def internet_archive_text_fallback_url(self, _source):
            return "https://archive.org/download/item/item_text.txt"

        async def process(self, source_id):
            return SourceIngestResult(
                source_id=source_id,
                gcs_raw_path="gs://bucket/raw.txt",
                gcs_ocr_path="gs://bucket/ocr.json",
                gcs_normalized_path="gs://bucket/passages.jsonl",
                indexed=True,
                ingestion_status="searchable",
                note="searchable",
                metadata={"indexed_passage_count": 1, "ocr_status": "ocr_completed"},
            )

    fake_sources = FakeSources()
    fake_runs = FakeRuns()
    lifecycle = SourceLifecycleService(FakeAgent(), FakeElastic(), fake_sources, fake_runs)

    result = lifecycle.ingest_process_refresh_sync(run, run.source_lifecycle_records[0], "Source auto-processed")

    assert fake_sources.ingest_urls == [
        "https://archive.org/download/item/broken.pdf",
        "https://archive.org/download/item/item_text.txt",
    ]
    processed = result.source_lifecycle_records[0]
    assert processed["lifecycle_status"] == "searchable"
    assert processed["fallback_from_url"] == "https://archive.org/download/item/broken.pdf"
    assert processed["download_url"] == "https://archive.org/download/item/item_text.txt"


def test_evidence_items_include_anchor_fields():
    agent = ResearchAgent(ElasticService(Settings()), WebResearchService(Settings()))
    run = agent.run(RunCreate(mode=RunMode.library, passage="قيل إن ما كان وجوده من غيره فهو ممكن"))

    item = run.evidence[0]

    assert item.verification_status in {"anchored_quote", "anchored_passage_only"}
    assert item.quote_start_char == 0
    assert item.quote_end_char == len(item.quote)
    assert item.source_locator_kind in {"page_ref", "source_page", "passage_id"}


def test_unanchored_quote_status_for_mismatched_quote():
    service = ElasticService(Settings())
    fields = service._evidence_anchor_fields({"passage_id": "p1", "text_raw": "abc def"}, "missing")

    assert fields["verification_status"] == "unanchored_quote"
    assert fields["quote_start_char"] is None


def test_passage_context_uses_passage_order_before_page_guessing():
    service = ElasticService(Settings())
    docs = [
        {
            "passage_id": "ordered-target",
            "text_raw": "target",
            "work_id": "ghazali-tahafut",
            "source_id": "openiti-ghazali-tahafut",
            "library_id": "demo_kalam",
            "source_page": "99",
            "page_ref": "ocr:99:1",
            "passage_order": 2,
        },
        {
            "passage_id": "ordered-prev",
            "text_raw": "previous",
            "work_id": "ghazali-tahafut",
            "source_id": "openiti-ghazali-tahafut",
            "library_id": "demo_kalam",
            "source_page": "100",
            "page_ref": "ocr:100:1",
            "passage_order": 1,
        },
        {
            "passage_id": "ordered-next",
            "text_raw": "next",
            "work_id": "ghazali-tahafut",
            "source_id": "openiti-ghazali-tahafut",
            "library_id": "demo_kalam",
            "source_page": "1",
            "page_ref": "ocr:1:1",
            "passage_order": 3,
        },
    ]

    class FakeElasticClient:
        def get(self, **_kwargs):
            raise KeyError("auto-generated Elasticsearch _id")

        def search(self, **kwargs):
            query = kwargs.get("query", {})
            if query.get("term") == {"passage_id": "ordered-target"}:
                return {"hits": {"hits": [{"_source": docs[0]}]}}
            return {"hits": {"hits": [{"_source": doc} for doc in docs]}}

    service.client = FakeElasticClient()
    service.health = lambda: "connected"  # type: ignore[method-assign]

    result = service.passage_context("ordered-target", window=1)

    assert [item["passage_id"] for item in result["items"]] == ["ordered-prev", "ordered-target", "ordered-next"]


def test_job_queue_run_execution_env(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "operations/run-123"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, headers, content):
            captured["url"] = url
            captured["headers"] = headers
            captured["content"] = json.loads(content)
            return FakeResponse()

    monkeypatch.setattr("app.services.job_queue._google_access_token", lambda: "token")
    monkeypatch.setattr("app.services.job_queue.httpx.AsyncClient", FakeClient)
    settings = Settings(
        job_backend="cloud_run_jobs",
        google_cloud_project="project",
        cloud_run_job_location="region",
        cloud_run_job_name="worker",
    )

    result = asyncio.run(JobQueueService(settings).enqueue_run_execution("run-1", attempt=3))

    env = captured["content"]["overrides"]["containerOverrides"][0]["env"]
    assert result["operation_name"] == "operations/run-123"
    assert {"name": "HERMENEUT_JOB_KIND", "value": "run"} in env
    assert {"name": "HERMENEUT_RUN_ID", "value": "run-1"} in env
    assert {"name": "HERMENEUT_JOB_ATTEMPT", "value": "3"} in env


def test_open_discovery_report_describes_processed_searchable_no_match():
    service = SourceLifecycleService(
        ResearchAgent(ElasticService(Settings()), WebResearchService(Settings())),
        ElasticService(Settings()),
        object(),
        object(),
    )
    run = AgentRun(
        run_id="run-open-report",
        mode=RunMode.open_discovery,
        input_passage="(قوله حاصله الخ) دفع لما يترا",
        status=RunStatus.completed,
        detected_context=DetectedContext(
            language="ar",
            domain="Arabic logic/commentary",
            period_hint="post-classical",
            citation_type="commentary",
            key_terms=[],
        ),
        hypotheses=[],
        search_plan=[],
        candidates=[],
        evidence=[],
        timeline=[],
        source_lifecycle_records=[
            {
                "source_id": "ia-demo",
                "source_role": "containing_layer",
                "lifecycle_status": "searchable",
                "indexed_passage_count": 99,
                "ocr_quality_status": "weak_ocr_needs_manual_review",
            },
            {
                "source_id": "ia-chain",
                "source_role": "citation_chain",
                "lifecycle_status": "download_candidate",
            },
        ],
        final_report="",
    )

    report = service._open_discovery_source_processing_report(
        run,
        tier="weak_lead",
        evidence_count=0,
        active_source_processing=False,
    )

    assert "1 source(s) became searchable" in report
    assert "99 passage(s) were indexed" in report
    assert "found no textual match" in report
    assert "weak/needs-review OCR" in report
    assert "no candidate source has been downloaded" not in report
