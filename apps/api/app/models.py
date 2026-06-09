from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SearchType(str, Enum):
    lexical = "lexical"
    semantic = "semantic"
    hybrid = "hybrid"
    metadata = "metadata"


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    waiting_source = "waiting_source"
    waiting_for_approval = "waiting_for_approval"
    completed = "completed"
    failed = "failed"


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    waiting_for_approval = "waiting_for_approval"
    failed = "failed"
    skipped = "skipped"


class OcrMode(str, Enum):
    full = "full"
    text_layer_first = "text_layer_first"
    skip = "skip"


class RunMode(str, Enum):
    library = "library"
    open_discovery = "open_discovery"


class RunActionType(str, Enum):
    approve_download = "approve_download"
    reject_source = "reject_source"
    continue_without_source = "continue_without_source"
    retry_ocr = "retry_ocr"


class DecisionTier(str, Enum):
    confirmed = "confirmed"
    probable = "probable"
    strong_lead = "strong_lead"
    weak_lead = "weak_lead"
    no_result = "no_result"


class DetectedContext(BaseModel):
    language: str
    domain: str
    period_hint: str
    citation_type: str
    key_terms: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    author: str
    work: str
    reason: str
    work_id: str | None = None


class SearchPlanItem(BaseModel):
    query: str
    type: SearchType
    purpose: str


class Candidate(BaseModel):
    work_id: str
    work_title: str
    author: str
    confidence: float
    why: str


class EvidenceItem(BaseModel):
    evidence_id: str
    passage_id: str
    library_id: str | None = None
    source_id: str | None = None
    source_title: str | None = None
    source_url: str | None = None
    source_page_url: str | None = None
    work_id: str
    work_title: str | None = None
    author_id: str | None = None
    author_name: str | None = None
    page_ref: str | None = None
    source_page: int | str | None = None
    location_label: str | None = None
    citation_hint: str | None = None
    ocr_confidence: float | None = None
    source_role: str | None = None
    source_resolution_query: str | None = None
    source_candidate_rank: int | None = None
    ocr_quality_status: str | None = None
    quote_start_char: int | None = None
    quote_end_char: int | None = None
    anchor_text_before: str | None = None
    anchor_text_after: str | None = None
    page_image_url: str | None = None
    page_image_available: bool = False
    source_locator_kind: str | None = None
    verification_status: str = "unverified"
    match_type: str
    quote: str
    translation_hint: str | None = None
    lexical_score: float
    semantic_score: float
    metadata_score: float
    citation_context_score: float
    source_quality_score: float
    relationship_fit_score: float = 0.0
    confidence: float
    explanation: str
    retrieval_backend: str = "seed-memory"
    retrieval_mode: str = "hybrid"
    elastic_index: str | None = None
    elastic_score: float | None = None
    model_trace: dict[str, Any] = Field(default_factory=dict)
    tool_trace: dict[str, Any] = Field(default_factory=dict)


class EvidenceMemoryRecord(BaseModel):
    run_id: str
    query: str
    tool_used: str
    passage_id: str
    candidate_work: str
    confidence: float
    verification_note: str


class TimelineEvent(BaseModel):
    label: str
    detail: str
    tool: str | None = None
    status: StepStatus = StepStatus.completed
    started_at: str | None = None
    completed_at: str | None = None
    estimated_seconds: int | None = None
    progress_message: str | None = None
    requires_action: bool = False
    action_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(BaseModel):
    phase: str
    step: str
    status: StepStatus = StepStatus.completed
    mode: RunMode
    provider: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output_summary: str = ""
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    decision: str | None = None
    rejection_reason: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RunCreate(BaseModel):
    mode: RunMode = RunMode.library
    passage: str = Field(min_length=3)
    context: str | None = None
    containing_author: str | None = None
    containing_work: str | None = None
    suspected_author: str | None = None
    suspected_work: str | None = None
    period_hint: str | None = None
    domain_hint: str | None = None
    language_hint: str | None = None
    library_id: str = "demo_kalam"
    enable_web_research: bool = True
    allow_source_download_suggestions: bool = True
    auto_download_sources: bool = True
    max_source_candidates: int = 5
    max_pdf_downloads: int = 3
    max_containing_source_downloads: int = 3
    max_citation_source_downloads: int = 3
    ocr_mode: OcrMode = OcrMode.full


class AgentRun(BaseModel):
    run_id: str
    mode: RunMode = RunMode.library
    input_passage: str
    status: RunStatus = RunStatus.completed
    current_step: str = "Completed"
    current_phase: str = "completed"
    blocked_reason: str | None = None
    progress_percent: int = 100
    estimated_remaining_seconds: int = 0
    execution_status: str = "completed"
    elapsed_seconds: int = 0
    delayed: bool = False
    retryable: bool = False
    detected_context: DetectedContext
    hypotheses: list[Hypothesis]
    search_plan: list[SearchPlanItem]
    candidates: list[Candidate]
    evidence: list[EvidenceItem]
    timeline: list[TimelineEvent]
    trace_events: list[TraceEvent] = Field(default_factory=list)
    source_lifecycle_records: list[dict[str, Any]] = Field(default_factory=list)
    context_profile: dict[str, Any] = Field(default_factory=dict)
    relationship_graph: list[dict[str, Any]] = Field(default_factory=list)
    author_candidates: list[dict[str, Any]] = Field(default_factory=list)
    phrase_variants: list[dict[str, Any]] = Field(default_factory=list)
    candidate_web_searches: list[dict[str, Any]] = Field(default_factory=list)
    work_candidates: list[dict[str, Any]] = Field(default_factory=list)
    top_pdf_targets: list[dict[str, Any]] = Field(default_factory=list)
    ocr_jobs: list[dict[str, Any]] = Field(default_factory=list)
    elastic_evidence: list[dict[str, Any]] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    decision_tier: DecisionTier = DecisionTier.no_result
    final_report: str


class SourceDiscoverRequest(BaseModel):
    author: str | None = None
    work: str | None = None
    query: str
    concepts: list[str] = Field(default_factory=list)
    period_hint: str | None = None
    domain_hint: str | None = None
    candidate_author_ids: list[str] = Field(default_factory=list)
    candidate_work_ids: list[str] = Field(default_factory=list)


class SourceHit(BaseModel):
    provider: str
    source_id: str
    title: str
    url: str
    file_type: str
    license_note: str
    quality: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceIngestRequest(BaseModel):
    provider: str
    source_id: str
    url: str
    work_id: str | None = None
    title: str | None = None
    source_page_url: str | None = None
    author_id: str | None = None
    author_name: str | None = None
    work_title: str | None = None
    source_role: str | None = None
    source_role_group: str | None = None
    resolution_queries: list[str] = Field(default_factory=list)
    source_resolution_query: str | None = None
    source_candidate_rank: int | None = None
    relationship_reason: str | None = None
    provenance: str | None = None
    library_id: str = "demo_kalam"
    approved: bool = True


class SourceIngestResult(BaseModel):
    source_id: str
    gcs_raw_path: str
    gcs_ocr_path: str | None = None
    gcs_normalized_path: str | None = None
    indexed: bool
    ingestion_status: str = "raw_stored"
    note: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CatalogSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    author: str | None = None
    work: str | None = None
    protocol: str = "auto"
    endpoint_url: str | None = None
    limit: int = 10


class CatalogEvidenceRef(BaseModel):
    passage_id: str | None = None
    source_id: str | None = None
    page_ref: str | None = None
    quote: str = ""
    evidence_kind: str = "text_sample"


class CatalogDecisionAudit(BaseModel):
    action: str
    actor: str = "operator"
    note: str | None = None
    decided_at: str
    applied_changes: dict[str, Any] = Field(default_factory=dict)


class CatalogProposal(BaseModel):
    proposal_id: str
    analysis_job_id: str
    library_id: str
    source_id: str | None = None
    work_id: str | None = None
    proposal_type: str
    status: str = "pending"
    risk_level: str = "medium"
    confidence: float
    current_value: dict[str, Any] = Field(default_factory=dict)
    proposed_value: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    evidence: list[CatalogEvidenceRef] = Field(default_factory=list)
    affected_records: list[str] = Field(default_factory=list)
    model_used: str
    model_route: str = "flash"
    prompt_profile: str = "catalog_curator_v1"
    analysis_version: str = "catalog-curator-v1"
    suppression_key: str
    created_at: str
    updated_at: str
    decision_audit: list[CatalogDecisionAudit] = Field(default_factory=list)


class CatalogAnalysisJob(BaseModel):
    analysis_job_id: str
    job_kind: str
    library_id: str
    source_id: str | None = None
    status: str = "queued"
    flash_model: str
    pro_model: str
    proposal_count: int = 0
    error: str | None = None
    created_at: str
    updated_at: str


class CatalogProposalDecisionRequest(BaseModel):
    note: str | None = None
    edited_proposed_value: dict[str, Any] | None = None


class CatalogBulkApproveRequest(BaseModel):
    proposal_ids: list[str] = Field(min_length=1)
    note: str | None = None


class CatalogHealthSummary(BaseModel):
    library_id: str | None = None
    backend: str
    read_only: bool = False
    score: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)


class OcrCorrectionRequest(BaseModel):
    corrected_text: str = Field(min_length=1)
    correction_reason: str | None = None
    editor_id: str = "demo-scholar"


class OcrCorrectionResult(BaseModel):
    source_id: str
    page_number: int
    reindexed_passage_count: int
    ground_truth_path: str | None = None
    correction_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class LibraryUploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1)
    content_type: str = "application/pdf"
    file_size: int | None = None
    source_id: str | None = None
    work_id: str | None = None
    title: str | None = None
    author_name: str | None = None
    domain: str | None = None
    notes: str | None = None


class LibraryUploadUrlResult(BaseModel):
    source_id: str
    upload_url: str
    gcs_raw_path: str
    raw_object: str
    method: str = "PUT"
    headers: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LibraryUploadCompleteRequest(BaseModel):
    source_id: str
    raw_object: str
    work_id: str | None = None
    title: str | None = None
    author_name: str | None = None
    domain: str | None = None
    notes: str | None = None
    content_type: str = "application/pdf"


class RunActionRequest(BaseModel):
    action: RunActionType
    source_id: str | None = None


class RunActionResult(BaseModel):
    run_id: str
    status: RunStatus
    note: str


class HealthStatus(BaseModel):
    status: str
    elastic: str
    elastic_mcp: str
    google_agent: str
    gcs: str
    gemini_grounding: str = "not_configured"
    job_queue: str = "not_configured"
    elastic_schema_version: str = "unknown"
    run_worker: str = "not_configured"
    run_snapshot_store: str = "not_configured"
    index_aliases: dict[str, str] = Field(default_factory=dict)
    public_demo: bool = False


class ElasticBootstrapResult(BaseModel):
    mode: str
    indexed: bool
    indices: dict[str, int]
    note: str
