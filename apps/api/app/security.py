from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from fastapi import Depends, HTTPException, Request

from app.dependencies import get_app_settings, get_elastic_service
from app.models import AgentRun
from app.settings import Settings


def require_admin(request: Request, settings: Settings = Depends(get_app_settings)) -> None:
    if not settings.admin_api_token:
        raise HTTPException(status_code=503, detail="Admin API token is not configured.")
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="Invalid admin API token.")


def require_jury_or_admin(request: Request, settings: Settings = Depends(get_app_settings)) -> None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if settings.admin_api_token and scheme.lower() == "bearer" and token == settings.admin_api_token:
        return

    jury_token = request.headers.get("x-hermeneut-jury-token", "")
    if settings.jury_access_enabled and settings.jury_proxy_token and jury_token == settings.jury_proxy_token:
        return

    raise HTTPException(
        status_code=403,
        detail={
            "code": "jury_access_required",
            "message": "This demo action is available through the jury access link.",
        },
    )


def require_live_elastic(elastic=Depends(get_elastic_service)) -> None:
    if elastic.health() != "connected":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "preview_read_only",
                "message": "This operator action requires live Elastic. Backup preview is read-only.",
            },
        )


SENSITIVE_KEYS = {
    "prompt",
    "prompt_excerpt",
    "raw_prompt",
    "full_prompt",
    "credentials",
    "credential",
    "api_key",
    "token",
    "authorization",
    "elastic_query",
    "raw_payload",
    "model_trace",
    "gcs_raw_path",
    "gcs_ocr_path",
    "gcs_normalized_path",
    "raw_object",
    "raw_path",
    "signed_url",
    "service_account",
    "bucket_path",
    "local_path",
}


def sanitize_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                continue
            sanitized[key_text] = sanitize_mapping(child)
        return sanitized
    if isinstance(value, list):
        return [sanitize_mapping(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("gs://", "gcs://")):
        return None
    return value


def sanitize_library_search(result: dict[str, Any]) -> dict[str, Any]:
    public_fields = {
        "libraries": {
            "library_id",
            "name",
            "description",
            "passage_count",
            "source_count",
            "work_count",
            "author_count",
            "edge_count",
            "evidence_count",
            "searchable_source_count",
        },
        "authors": {
            "author_id",
            "name",
            "name_ar",
            "aliases",
            "death_year",
            "period",
            "tradition",
            "library_id",
            "elastic_score",
        },
        "works": {
            "work_id",
            "title",
            "title_ar",
            "author_id",
            "author_name",
            "domain",
            "language",
            "source_status",
            "library_id",
            "visibility",
            "license_status",
            "ingestion_status",
            "elastic_score",
            "author_name_ar",
            "layer_type",
            "layer_rank",
            "source_count",
            "passage_count",
            "ocr_status_summary",
            "searchable_source_count",
            "relationship_count",
            "catalog_review_status",
            "catalog_review_reasons",
            "metadata_quality_score",
        },
        "sources": {
            "source_id",
            "work_id",
            "work_title",
            "author_id",
            "author_name",
            "provider",
            "title",
            "url",
            "source_page_url",
            "file_type",
            "license_note",
            "license_status",
            "ingestion_status",
            "ocr_status",
            "ocr_quality_status",
            "ocr_avg_confidence",
            "indexed_passage_count",
            "relationship_edge_count",
            "library_id",
            "visibility",
            "quality",
            "download_policy",
            "elastic_score",
            "title_ar",
            "author_name_ar",
            "lifecycle_status",
            "verification_status",
            "ocr_page_count",
            "source_role",
            "text_layer",
            "layer_rank",
            "depends_on_work_ids",
            "catalog_review_status",
            "catalog_review_reasons",
            "metadata_quality_score",
        },
        "edges": {
            "edge_id",
            "from",
            "to",
            "type",
            "from_type",
            "from_id",
            "relation",
            "to_type",
            "to_id",
            "provenance",
            "confidence",
            "verification_status",
            "reasoning_summary",
            "evidence_snippet",
            "elastic_score",
            "library_id",
        },
        "passages": {
            "passage_id",
            "text_raw",
            "translation_hint",
            "concepts",
            "source_id",
            "source_title",
            "source_url",
            "source_page_url",
            "work_id",
            "work_title",
            "work_title_ar",
            "author_id",
            "author_name",
            "domain",
            "library_id",
            "section_ref",
            "page_ref",
            "source_page",
            "ocr_confidence",
            "quote_start_char",
            "quote_end_char",
            "anchor_text_before",
            "anchor_text_after",
            "page_image_url",
            "page_image_available",
            "source_locator_kind",
            "verification_status",
            "passage_order",
            "chunk_index",
            "extraction_method",
            "source_role",
            "layer_rank",
            "depends_on_work_ids",
            "source_quality",
            "semantic_model",
            "location_label",
            "citation_hint",
            "elastic_score",
        },
    }
    sanitized: dict[str, Any] = {"meta": sanitize_mapping(result.get("meta", {}))}
    for section, allowed in public_fields.items():
        rows = result.get(section, [])
        if isinstance(rows, list):
            sanitized[section] = [
                sanitize_mapping({key: row.get(key) for key in allowed if isinstance(row, Mapping) and key in row})
                for row in rows
            ]
    return sanitized


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SENSITIVE_KEYS:
        return True
    if lowered.startswith("gcs_"):
        return True
    return any(fragment in lowered for fragment in ("credential", "secret", "signed_url"))


def sanitize_run(run: AgentRun, *, debug: bool = False) -> AgentRun:
    if debug:
        return run
    sanitized = deepcopy(run)
    sanitized.trace_events = []
    sanitized.elastic_evidence = [
        sanitize_mapping(item) if isinstance(item, dict) else item for item in sanitized.elastic_evidence[:8]
    ]
    sanitized.evidence = [
        item.model_copy(
            update={
                "tool_trace": _public_tool_trace(item.tool_trace),
                "model_trace": _public_model_trace(item.model_trace),
            }
        )
        for item in sanitized.evidence[:12]
    ]
    sanitized.timeline = [
        event.model_copy(update={"payload": sanitize_mapping(event.payload)})
        for event in sanitized.timeline
    ]
    sanitized.relationship_graph = [
        sanitize_mapping(edge) for edge in sanitized.relationship_graph[:24]
    ]
    sanitized.context_profile = sanitize_mapping(sanitized.context_profile)
    sanitized.author_candidates = [sanitize_mapping(item) for item in sanitized.author_candidates[:12]]
    sanitized.phrase_variants = [sanitize_mapping(item) for item in sanitized.phrase_variants[:12]]
    sanitized.candidate_web_searches = [sanitize_mapping(item) for item in sanitized.candidate_web_searches[:12]]
    sanitized.work_candidates = [sanitize_mapping(item) for item in sanitized.work_candidates[:12]]
    sanitized.top_pdf_targets = [sanitize_mapping(item) for item in sanitized.top_pdf_targets[:6]]
    sanitized.ocr_jobs = [sanitize_mapping(item) for item in sanitized.ocr_jobs[:8]]
    sanitized.rejected_candidates = []
    sanitized.source_lifecycle_records = [
        _public_source_record(item) for item in sanitized.source_lifecycle_records[:12]
    ]
    return sanitized


def _public_tool_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        key: trace.get(key)
        for key in ("backend", "tool", "index", "document_id", "query_type", "elastic_score", "max_score", "relationship_fit")
        if key in trace
    }


def _public_model_trace(trace: dict[str, Any]) -> dict[str, Any]:
    return {
        key: trace.get(key)
        for key in ("research_model", "report_model", "embedding_model", "model", "prompt_profile", "fallback_reason")
        if key in trace
    }


def _public_source_record(source: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "source_id",
        "provider",
        "title",
        "url",
        "source_page_url",
        "file_type",
        "license_status",
        "library_id",
        "download_policy",
        "lifecycle_status",
        "ingestion_status",
        "ocr_status",
        "ocr_quality_status",
        "ocr_avg_confidence",
        "indexed_passage_count",
        "source_role",
        "source_role_group",
        "resolution_queries",
        "source_resolution_query",
        "source_candidate_rank",
        "failure_reason_public",
        "relationship_reason",
        "counts_as_evidence",
    }
    return {key: sanitize_mapping(value) for key, value in source.items() if key in allowed}
