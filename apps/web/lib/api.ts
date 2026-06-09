import type {
  AgentRun,
  EvidenceContextResponse,
  HealthStatus,
  LibrarySearchResponse,
  LibraryUploadUrlResult,
  RunCreateInput,
  SourceHit,
  SourceIngestResult,
  SourcePageData,
  SourceStatus,
  CatalogHealthSummary,
  CatalogInboxResponse,
  JuryStatus,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function apiUrl(path: string): string {
  if (typeof window !== "undefined") {
    return path;
  }
  return `${API_BASE_URL}${path}`;
}

function adminHeaders(base?: HeadersInit): HeadersInit {
  const headers = new Headers(base);
  if (typeof window !== "undefined") {
    const token = window.sessionStorage.getItem("hermeneut_admin_token");
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function createRun(input: RunCreateInput): Promise<AgentRun> {
  const response = await fetch(apiUrl("/api/runs"), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(input),
    cache: "no-store",
  });
  return parseResponse<AgentRun>(response);
}

export async function getRun(runId: string): Promise<AgentRun> {
  const response = await fetch(apiUrl(`/api/runs/${runId}`), {cache: "no-store"});
  return parseResponse<AgentRun>(response);
}

export async function retryRun(runId: string): Promise<AgentRun> {
  const response = await fetch(apiUrl(`/api/runs/${runId}/retry`), {method: "POST", cache: "no-store"});
  return parseResponse<AgentRun>(response);
}

export async function getEvidenceContext(passageId: string, window = 2): Promise<EvidenceContextResponse> {
  const response = await fetch(apiUrl(`/api/evidence/${encodeURIComponent(passageId)}/context?window=${window}`), {
    cache: "no-store",
  });
  return parseResponse<EvidenceContextResponse>(response);
}

export async function getHealth(): Promise<HealthStatus> {
  const response = await fetch(apiUrl("/api/health"), {cache: "no-store"});
  return parseResponse<HealthStatus>(response);
}

export async function getJuryStatus(): Promise<JuryStatus> {
  const response = await fetch(apiUrl("/api/jury/status"), {cache: "no-store"});
  return parseResponse<JuryStatus>(response);
}

export async function runAction(
  runId: string,
  action: "approve_download" | "reject_source" | "continue_without_source" | "retry_ocr",
  sourceId?: string,
): Promise<{run_id: string; status: string; note: string}> {
  const response = await fetch(apiUrl(`/api/runs/${runId}/actions`), {
    method: "POST",
    headers: adminHeaders({"Content-Type": "application/json"}),
    body: JSON.stringify({action, source_id: sourceId}),
    cache: "no-store",
  });
  return parseResponse<{run_id: string; status: string; note: string}>(response);
}

export async function searchLibrary(query = ""): Promise<LibrarySearchResponse> {
  const params = query ? `?q=${encodeURIComponent(query)}` : "";
  const response = await fetch(apiUrl(`/api/library/search${params}`), {cache: "no-store"});
  return parseResponse<LibrarySearchResponse>(response);
}

export async function getCatalogInbox(libraryId?: string): Promise<CatalogInboxResponse> {
  const params = libraryId ? `?library_id=${encodeURIComponent(libraryId)}` : "";
  const response = await fetch(apiUrl(`/api/catalog-curator/inbox${params}`), {
    headers: adminHeaders(),
    cache: "no-store",
  });
  return parseResponse<CatalogInboxResponse>(response);
}

export async function getCatalogHealth(libraryId?: string): Promise<CatalogHealthSummary> {
  const params = libraryId ? `?library_id=${encodeURIComponent(libraryId)}` : "";
  const response = await fetch(apiUrl(`/api/catalog-curator/health${params}`), {
    headers: adminHeaders(),
    cache: "no-store",
  });
  return parseResponse<CatalogHealthSummary>(response);
}

export async function analyzeCatalogLibrary(libraryId: string): Promise<Record<string, unknown>> {
  const response = await fetch(apiUrl(`/api/catalog-curator/libraries/${encodeURIComponent(libraryId)}/analyze`), {
    method: "POST",
    headers: adminHeaders(),
    cache: "no-store",
  });
  return parseResponse<Record<string, unknown>>(response);
}

export async function decideCatalogProposal(proposalId: string, action: "approve" | "reject"): Promise<Record<string, unknown>> {
  const response = await fetch(apiUrl(`/api/catalog-curator/proposals/${encodeURIComponent(proposalId)}/${action}`), {
    method: "POST",
    headers: adminHeaders({"Content-Type": "application/json"}),
    body: JSON.stringify({}),
    cache: "no-store",
  });
  return parseResponse<Record<string, unknown>>(response);
}

export async function discoverSources(query: string, author?: string, work?: string): Promise<SourceHit[]> {
  const response = await fetch(apiUrl("/api/sources/discover"), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({query, author, work}),
    cache: "no-store",
  });
  return parseResponse<SourceHit[]>(response);
}

export async function ingestSource(hit: SourceHit): Promise<SourceIngestResult> {
  const response = await fetch(apiUrl("/api/sources/ingest"), {
    method: "POST",
    headers: adminHeaders({"Content-Type": "application/json"}),
    body: JSON.stringify({
      provider: hit.provider,
      source_id: hit.source_id,
      url: typeof hit.metadata.download_url === "string" ? hit.metadata.download_url : hit.url,
      work_id: typeof hit.metadata.work_id === "string" ? hit.metadata.work_id : undefined,
      library_id: typeof hit.metadata.library_id === "string" ? hit.metadata.library_id : "demo_kalam",
      approved: true,
    }),
    cache: "no-store",
  });
  return parseResponse<SourceIngestResult>(response);
}

export async function processSource(sourceId: string): Promise<SourceIngestResult> {
  const response = await fetch(apiUrl(`/api/sources/${encodeURIComponent(sourceId)}/process`), {
    method: "POST",
    headers: adminHeaders(),
    cache: "no-store",
  });
  return parseResponse<SourceIngestResult>(response);
}

export async function getSourceStatus(sourceId: string): Promise<SourceStatus> {
  const response = await fetch(apiUrl(`/api/sources/${encodeURIComponent(sourceId)}/status`), {
    cache: "no-store",
  });
  return parseResponse<SourceStatus>(response);
}

export async function getSourcePage(sourceId: string, pageNumber: number): Promise<SourcePageData> {
  const response = await fetch(apiUrl(`/api/sources/${encodeURIComponent(sourceId)}/pages/${pageNumber}`), {
    headers: adminHeaders(),
    cache: "no-store",
  });
  return parseResponse<SourcePageData>(response);
}

export async function saveSourcePageCorrection(
  sourceId: string,
  pageNumber: number,
  correctedText: string,
  correctionReason?: string,
): Promise<Record<string, unknown>> {
  const response = await fetch(apiUrl(`/api/sources/${encodeURIComponent(sourceId)}/pages/${pageNumber}/corrections`), {
    method: "POST",
    headers: adminHeaders({"Content-Type": "application/json"}),
    body: JSON.stringify({corrected_text: correctedText, correction_reason: correctionReason}),
    cache: "no-store",
  });
  return parseResponse<Record<string, unknown>>(response);
}

export async function auditSourcePage(sourceId: string, pageNumber: number): Promise<Record<string, unknown>> {
  const response = await fetch(apiUrl(`/api/sources/${encodeURIComponent(sourceId)}/pages/${pageNumber}/gemini-audit`), {
    method: "POST",
    headers: adminHeaders(),
    cache: "no-store",
  });
  return parseResponse<Record<string, unknown>>(response);
}

export async function searchCatalog(input: {
  query: string;
  author?: string;
  work?: string;
  protocol?: string;
  endpoint_url?: string;
}): Promise<Record<string, unknown>[]> {
  const response = await fetch(apiUrl("/api/catalog/search"), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(input),
    cache: "no-store",
  });
  return parseResponse<Record<string, unknown>[]>(response);
}

export async function addLibrarySource(input: {
  libraryId: string;
  provider: string;
  sourceId?: string;
  workId?: string;
  title?: string;
  authorName?: string;
  domain?: string;
  notes?: string;
  url?: string;
  file?: File | null;
}): Promise<SourceIngestResult> {
  const form = new FormData();
  form.set("provider", input.provider);
  if (input.sourceId) form.set("source_id", input.sourceId);
  if (input.workId) form.set("work_id", input.workId);
  if (input.title) form.set("title", input.title);
  if (input.authorName) form.set("author_name", input.authorName);
  if (input.domain) form.set("domain", input.domain);
  if (input.notes) form.set("notes", input.notes);
  if (input.url) form.set("url", input.url);
  if (input.file) form.set("file", input.file);
  const response = await fetch(apiUrl(`/api/libraries/${encodeURIComponent(input.libraryId)}/sources`), {
    method: "POST",
    headers: adminHeaders(),
    body: form,
    cache: "no-store",
  });
  return parseResponse<SourceIngestResult>(response);
}

export async function createLibraryUploadUrl(input: {
  libraryId: string;
  filename: string;
  contentType: string;
  fileSize?: number;
  sourceId?: string;
  workId?: string;
  title?: string;
  authorName?: string;
  domain?: string;
  notes?: string;
}): Promise<LibraryUploadUrlResult> {
  const response = await fetch(apiUrl(`/api/libraries/${encodeURIComponent(input.libraryId)}/sources/upload-url`), {
    method: "POST",
    headers: adminHeaders({"Content-Type": "application/json"}),
    body: JSON.stringify({
      filename: input.filename,
      content_type: input.contentType,
      file_size: input.fileSize,
      source_id: input.sourceId,
      work_id: input.workId,
      title: input.title,
      author_name: input.authorName,
      domain: input.domain,
      notes: input.notes,
    }),
    cache: "no-store",
  });
  return parseResponse<LibraryUploadUrlResult>(response);
}

export async function completeLibraryUpload(input: {
  libraryId: string;
  sourceId: string;
  rawObject: string;
  contentType: string;
  workId?: string;
  title?: string;
  authorName?: string;
  domain?: string;
  notes?: string;
}): Promise<SourceIngestResult> {
  const response = await fetch(apiUrl(`/api/libraries/${encodeURIComponent(input.libraryId)}/sources/complete-upload`), {
    method: "POST",
    headers: adminHeaders({"Content-Type": "application/json"}),
    body: JSON.stringify({
      source_id: input.sourceId,
      raw_object: input.rawObject,
      content_type: input.contentType,
      work_id: input.workId,
      title: input.title,
      author_name: input.authorName,
      domain: input.domain,
      notes: input.notes,
    }),
    cache: "no-store",
  });
  return parseResponse<SourceIngestResult>(response);
}

export async function importShamsiyyaDocx(files: File[]): Promise<Record<string, unknown>> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  const response = await fetch(apiUrl("/api/libraries/shamsiyya_hashiya_demo/shamsiyya-docx-import"), {
    method: "POST",
    headers: adminHeaders(),
    body: form,
    cache: "no-store",
  });
  return parseResponse<Record<string, unknown>>(response);
}

export async function analyzeLibraryRelationships(libraryId: string): Promise<Record<string, unknown>> {
  const response = await fetch(apiUrl(`/api/libraries/${encodeURIComponent(libraryId)}/relationships/analyze`), {
    method: "POST",
    headers: adminHeaders(),
    cache: "no-store",
  });
  return parseResponse<Record<string, unknown>>(response);
}

export { API_BASE_URL };
