import asyncio

from app.models import SourceDiscoverRequest, SourceIngestRequest
from app.services.source_discovery import SourceDiscoveryService
from app.settings import Settings
from app.services.ocr_quality import STRONG_OCR, USABLE_OCR, WEAK_OCR, classify_ocr_quality


def test_seed_source_discovery_finds_curated_source():
    service = SourceDiscoveryService(Settings())
    hits = service._seed_hits(SourceDiscoverRequest(query="Tahafut", work="Tahafut al-falasifa"))
    assert hits
    assert hits[0].provider == "OpenITI-style seed"
    assert hits[0].metadata["download_policy"] == "already_indexed_or_demo_controlled"


def test_ingest_returns_gcs_path():
    service = SourceDiscoveryService(Settings(gcs_bucket="demo-bucket"))
    async def fake_download(url: str) -> bytes:
        return b"test text layer"

    service._download_source = fake_download
    result = asyncio.run(
        service.ingest(
            SourceIngestRequest(
                provider="Internet Archive",
                source_id="abc",
                url="https://archive.org/download/demo/demo.txt",
                work_id="demo-work",
                title="Demo title",
                source_page_url="https://archive.org/details/demo",
                author_id="demo-author",
                author_name="Demo Author",
                work_title="Demo Work",
                source_role="containing_layer",
                source_role_group="where_phrase_may_be_found",
                resolution_queries=["Demo title archive.org"],
                source_resolution_query="Demo title archive.org",
                source_candidate_rank=2,
                relationship_reason="Demo relationship.",
                provenance="test_resolver",
            )
        )
    )
    assert result.gcs_raw_path == "gs://demo-bucket/raw/demo_kalam/internet_archive/abc/source.text"
    assert result.gcs_ocr_path == "gs://demo-bucket/ocr/demo_kalam/abc/ocr.json"
    assert result.gcs_normalized_path == "gs://demo-bucket/normalized/demo_kalam/abc/passages.jsonl"
    assert result.ingestion_status == "metadata_recorded"
    assert result.metadata["ocr_status"] == "ocr_pending"
    assert result.metadata["ocr_engine"] == "google_vision"
    assert result.metadata["work_id"] == "demo-work"
    assert result.metadata["title"] == "Demo title"
    assert result.metadata["source_page_url"] == "https://archive.org/details/demo"
    assert result.metadata["author_name"] == "Demo Author"
    assert result.metadata["work_title"] == "Demo Work"
    assert result.metadata["source_role"] == "containing_layer"
    assert result.metadata["source_resolution_query"] == "Demo title archive.org"
    assert result.metadata["source_candidate_rank"] == 2
    assert result.metadata["relationship_reason"] == "Demo relationship."
    assert result.metadata["provenance"] == "test_resolver"


def test_ingest_rejects_non_allowlisted_source():
    service = SourceDiscoveryService(Settings(gcs_bucket="demo-bucket"))
    try:
        asyncio.run(
            service.ingest(
                SourceIngestRequest(
                    provider="Unknown",
                    source_id="bad",
                    url="https://untrusted.invalid/book.html",
                )
            )
        )
    except ValueError as exc:
        assert "allowlist" in str(exc)
    else:
        raise AssertionError("Expected untrusted source URL to be rejected")


def test_ingest_requires_approval():
    service = SourceDiscoveryService(Settings(gcs_bucket="demo-bucket"))
    try:
        asyncio.run(
            service.ingest(
                SourceIngestRequest(
                    provider="Internet Archive",
                    source_id="abc",
                    url="https://example.com",
                    approved=False,
                )
            )
        )
    except ValueError as exc:
        assert "approval" in str(exc)
    else:
        raise AssertionError("Expected unapproved source ingest to be rejected")


def test_process_uses_ocr_processor_for_text_source():
    service = SourceDiscoveryService(Settings(gcs_bucket="demo-bucket"))
    result = asyncio.run(service.process("openiti-ghazali-tahafut"))

    assert result.metadata["ocr_status"] in {"ocr_completed", "ocr_completed_with_curated_fallback"}
    assert result.metadata["ocr_engine"] in {"google_vision", "curated_text_layer_fallback"}
    assert result.metadata["ocr_page_count"] >= 1
    assert result.metadata["lifecycle_status"] in {"searchable", "ocr_completed", "ocr_failed", "processed_no_text"}
    assert "passage" in result.note


def test_curated_seed_without_passages_does_not_generate_placeholder_text():
    service = SourceDiscoveryService(Settings(gcs_bucket="demo-bucket"))

    pages = service._extract_demo_pages({"source_id": "fake-seed", "work_id": "missing-work"})

    assert pages == []


def test_ocr_quality_demotes_high_confidence_gibberish():
    pages = [{"text": "abc@@@### xxxxx!!!!! qqqqq@@@@@ zzzzz%%%%% " * 8}]

    assert classify_ocr_quality(pages, 0.91) == WEAK_OCR


def test_ocr_quality_keeps_readable_arabic_strong():
    pages = [
        {
            "text": (
                "قوله حاصله دفع لما يترا أي من أن الشرطية المذكورة بقوله لما نبه مستدركة "
                "وهذا نص مقروء متصل يصلح للمراجعة العلمية"
            )
        }
    ]

    assert classify_ocr_quality(pages, 0.9) == STRONG_OCR


def test_ocr_quality_marks_mid_confidence_readable_text_usable():
    pages = [{"text": "هذا نص عربي مقروء وفيه كلمات كافية للمراجعة والتحقيق"}]

    assert classify_ocr_quality(pages, 0.7) == USABLE_OCR
