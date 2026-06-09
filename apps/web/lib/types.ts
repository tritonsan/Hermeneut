export type SearchType = "lexical" | "semantic" | "hybrid" | "metadata";

export type DetectedContext = {
  language: string;
  domain: string;
  period_hint: string;
  citation_type: string;
  key_terms: string[];
};

export type Hypothesis = {
  author: string;
  work: string;
  reason: string;
  work_id?: string;
};

export type SearchPlanItem = {
  query: string;
  type: SearchType;
  purpose: string;
};

export type Candidate = {
  work_id: string;
  work_title: string;
  author: string;
  confidence: number;
  why: string;
};

export type EvidenceItem = {
  evidence_id: string;
  passage_id: string;
  library_id?: string;
  source_id?: string;
  source_title?: string;
  source_url?: string;
  source_page_url?: string;
  work_id: string;
  work_title?: string;
  author_id?: string;
  author_name?: string;
  page_ref?: string;
  source_page?: number | string;
  location_label?: string;
  citation_hint?: string;
  ocr_confidence?: number;
  source_role?: string;
  source_resolution_query?: string;
  source_candidate_rank?: number;
  ocr_quality_status?: string;
  quote_start_char?: number;
  quote_end_char?: number;
  anchor_text_before?: string;
  anchor_text_after?: string;
  page_image_url?: string;
  page_image_available?: boolean;
  source_locator_kind?: string;
  verification_status?: string;
  match_type: string;
  quote: string;
  translation_hint?: string;
  lexical_score: number;
  semantic_score: number;
  metadata_score: number;
  citation_context_score: number;
  source_quality_score: number;
  relationship_fit_score: number;
  retrieval_mode: string;
  confidence: number;
  explanation: string;
  retrieval_backend: string;
  elastic_index?: string;
  elastic_score?: number;
  tool_trace: Record<string, unknown>;
  model_trace: Record<string, unknown>;
};

export type TimelineEvent = {
  label: string;
  detail: string;
  tool?: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  estimated_seconds?: number;
  progress_message?: string;
  requires_action?: boolean;
  action_type?: string;
  payload: Record<string, unknown>;
};

export type AgentRun = {
  run_id: string;
  mode: "library" | "open_discovery";
  input_passage: string;
  status: "queued" | "running" | "waiting_source" | "waiting_for_approval" | "completed" | "failed";
  current_step: string;
  current_phase: string;
  blocked_reason?: string;
  progress_percent: number;
  estimated_remaining_seconds: number;
  execution_status?: string;
  elapsed_seconds?: number;
  delayed?: boolean;
  retryable?: boolean;
  detected_context: DetectedContext;
  hypotheses: Hypothesis[];
  search_plan: SearchPlanItem[];
  candidates: Candidate[];
  evidence: EvidenceItem[];
  timeline: TimelineEvent[];
  trace_events: TraceEvent[];
  source_lifecycle_records: Record<string, unknown>[];
  context_profile: Record<string, unknown>;
  relationship_graph: Record<string, unknown>[];
  author_candidates: Record<string, unknown>[];
  phrase_variants: Record<string, unknown>[];
  candidate_web_searches: Record<string, unknown>[];
  work_candidates: Record<string, unknown>[];
  top_pdf_targets: Record<string, unknown>[];
  ocr_jobs: Record<string, unknown>[];
  elastic_evidence: Record<string, unknown>[];
  rejected_candidates: Record<string, unknown>[];
  decision_tier: "confirmed" | "probable" | "strong_lead" | "weak_lead" | "no_result";
  final_report: string;
};

export type TraceEvent = {
  phase: string;
  step: string;
  status: string;
  mode: "library" | "open_discovery";
  provider?: string;
  input: Record<string, unknown>;
  output_summary: string;
  raw_payload: Record<string, unknown>;
  decision?: string;
  rejection_reason?: string;
  started_at?: string;
  completed_at?: string;
};

export type RunCreateInput = {
  mode: "library" | "open_discovery";
  passage: string;
  context?: string;
  containing_author?: string;
  containing_work?: string;
  suspected_author?: string;
  suspected_work?: string;
  period_hint?: string;
  domain_hint?: string;
  language_hint?: string;
  library_id?: string;
  enable_web_research?: boolean;
  allow_source_download_suggestions?: boolean;
  auto_download_sources?: boolean;
  max_source_candidates?: number;
  max_pdf_downloads?: number;
  max_containing_source_downloads?: number;
  max_citation_source_downloads?: number;
  ocr_mode?: "full" | "text_layer_first" | "skip";
};

export type SourceHit = {
  provider: string;
  source_id: string;
  title: string;
  url: string;
  file_type: string;
  license_note: string;
  quality: number;
  metadata: Record<string, unknown>;
};

export type SourceIngestResult = {
  source_id: string;
  gcs_raw_path: string;
  gcs_ocr_path?: string;
  gcs_normalized_path?: string;
  indexed: boolean;
  ingestion_status: string;
  note: string;
  metadata: Record<string, unknown>;
};

export type LibraryUploadUrlResult = {
  source_id: string;
  upload_url: string;
  gcs_raw_path: string;
  raw_object: string;
  method: "PUT";
  headers: Record<string, string>;
  metadata: Record<string, unknown>;
};

export type SourceStatus = {
  source_id: string;
  lifecycle: string;
  lifecycle_status: string;
  processing_job_id?: string;
  processing_job?: Record<string, unknown>;
  job_events?: Record<string, unknown>[];
  progress_percent: number;
  graph_status: string;
  relationship_edge_count: number;
  relationship_summary?: {
    direct_count: number;
    work_count: number;
    total_count: number;
    status: string;
  };
  gcs_raw_path?: string;
  gcs_ocr_path?: string;
  gcs_normalized_path?: string;
  ocr_status: string;
  ocr_page_count: number;
  ocr_total_pages?: number;
  ocr_processed_pages?: number;
  ocr_next_page?: number | null;
  ocr_batch_size?: number;
  ocr_resume_available?: boolean;
  ocr_full_document_max_pages?: number;
  ocr_avg_confidence?: number;
  ocr_quality_status?: string;
  indexed_passage_count: number;
  metadata: Record<string, unknown>;
};

export type SourcePageData = {
  source_id: string;
  page_number: number;
  page_image?: string | null;
  ocr_text: string;
  text_layer: string;
  vision_text: string;
  ocr_confidence: number;
  extraction_method: string;
  normalized_preview: string;
  corrections: Record<string, unknown>[];
  source: Record<string, unknown>;
};

export type LibrarySearchResponse = {
  meta?: {
    backend?: string;
    health?: string;
    query?: string;
    counts?: Record<string, number>;
    read_only?: boolean;
    data_timestamp?: string;
    limitations?: string;
  };
  libraries?: LibrarySummary[];
  authors?: Record<string, unknown>[];
  works?: WorkRecord[];
  sources?: SourceWitness[];
  edges?: RelationshipLineage[];
  passages?: Record<string, unknown>[];
};

export type LibrarySummary = {
  library_id: string;
  name?: string;
  description?: string;
  passage_count?: number;
  source_count?: number;
  work_count?: number;
  author_count?: number;
  edge_count?: number;
  searchable_source_count?: number;
};

export type WorkRecord = {
  work_id: string;
  title?: string;
  title_ar?: string;
  author_id?: string;
  author_name?: string;
  author_name_ar?: string;
  domain?: string;
  library_id?: string;
  layer_type?: string;
  layer_rank?: number;
  source_count?: number;
  passage_count?: number;
  searchable_source_count?: number;
  ocr_status_summary?: string;
  relationship_count?: number;
  catalog_review_status?: "verified" | "needs_review";
  catalog_review_reasons?: string[];
  metadata_quality_score?: number;
};

export type SourceWitness = {
  source_id: string;
  work_id?: string;
  title?: string;
  title_ar?: string;
  author_name?: string;
  provider?: string;
  url?: string;
  source_page_url?: string;
  file_type?: string;
  library_id?: string;
  ingestion_status?: string;
  lifecycle_status?: string;
  verification_status?: string;
  ocr_status?: string;
  ocr_quality_status?: string;
  ocr_avg_confidence?: number;
  ocr_page_count?: number;
  indexed_passage_count?: number;
  relationship_edge_count?: number;
  source_role?: string;
  text_layer?: string;
  layer_rank?: number;
  catalog_review_status?: "verified" | "needs_review";
  catalog_review_reasons?: string[];
  metadata_quality_score?: number;
};

export type RelationshipLineage = {
  edge_id?: string;
  from?: string;
  to?: string;
  from_type?: string;
  from_id?: string;
  to_type?: string;
  to_id?: string;
  relation?: string;
  library_id?: string;
  provenance?: string;
  confidence?: number;
  verification_status?: string;
  reasoning_summary?: string;
};

export type EvidenceContextResponse = {
  passage_id: string;
  library_id?: string;
  source_id?: string;
  items: Record<string, unknown>[];
};

export type HealthStatus = {
  status: string;
  elastic?: string;
  elastic_mcp?: string;
  google_agent?: string;
  gcs?: string;
  gemini_grounding?: string;
  job_queue?: string;
  elastic_schema_version?: string;
  run_worker?: string;
  run_snapshot_store?: string;
  index_aliases?: Record<string, string>;
  public_demo?: boolean;
};

export type JuryStatus = {
  access: "public" | "jury";
  research_enabled: boolean;
  operator_enabled: boolean;
};

export type CatalogEvidenceRef = {
  passage_id?: string;
  source_id?: string;
  page_ref?: string;
  quote?: string;
  evidence_kind?: string;
};

export type CatalogProposal = {
  proposal_id: string;
  analysis_job_id: string;
  library_id: string;
  source_id?: string;
  work_id?: string;
  proposal_type: string;
  status: string;
  risk_level: string;
  confidence: number;
  current_value: Record<string, unknown>;
  proposed_value: Record<string, unknown>;
  reasoning: string;
  evidence: CatalogEvidenceRef[];
  affected_records: string[];
  model_used: string;
  model_route: string;
  created_at: string;
  updated_at: string;
};

export type CatalogInboxResponse = {
  backend: string;
  read_only: boolean;
  proposals: CatalogProposal[];
};

export type CatalogHealthSummary = {
  library_id?: string;
  backend: string;
  read_only: boolean;
  score: number;
  counts: Record<string, number>;
  issues: {issue_type: string; record_id?: string; title?: string; library_id?: string}[];
};
