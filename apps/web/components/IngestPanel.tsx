"use client";

import { useEffect, useState } from "react";
import { Activity, Loader2, SearchCode } from "lucide-react";
import {
  addLibrarySource,
  analyzeLibraryRelationships,
  completeLibraryUpload,
  createLibraryUploadUrl,
  discoverSources,
  getSourceStatus,
  getHealth,
  getJuryStatus,
  ingestSource,
  importShamsiyyaDocx,
  processSource,
} from "@/lib/api";
import type { HealthStatus, JuryStatus, SourceHit, SourceIngestResult, SourceStatus } from "@/lib/types";

function uploadFileDirectly(
  uploadUrl: string,
  file: File,
  contentType: string,
  onProgress: (progress: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", uploadUrl);
    request.setRequestHeader("Content-Type", contentType);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress(100);
        resolve();
      } else {
        reject(new Error(`Direct GCS upload failed with status ${request.status}`));
      }
    };
    request.onerror = () => reject(new Error("Direct GCS upload failed."));
    request.send(file);
  });
}

export function IngestPanel() {
  const [query, setQuery] = useState("Tahafut al-falasifa");
  const [tab, setTab] = useState<"library" | "web">("library");
  const [libraryId, setLibraryId] = useState("demo_kalam");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceTitle, setSourceTitle] = useState("");
  const [authorName, setAuthorName] = useState("");
  const [workId, setWorkId] = useState("");
  const [domain, setDomain] = useState("logic/commentary tradition");
  const [notes, setNotes] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [shamsiyyaFiles, setShamsiyyaFiles] = useState<File[]>([]);
  const [hits, setHits] = useState<SourceHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [elasticStatus, setElasticStatus] = useState<Record<string, unknown> | null>(null);
  const [ingests, setIngests] = useState<Record<string, SourceIngestResult>>({});
  const [processed, setProcessed] = useState<Record<string, SourceIngestResult>>({});
  const [libraryResult, setLibraryResult] = useState<SourceIngestResult | null>(null);
  const [sourceStatus, setSourceStatus] = useState<SourceStatus | null>(null);
  const [shamsiyyaResult, setShamsiyyaResult] = useState<Record<string, unknown> | null>(null);
  const [relationshipResult, setRelationshipResult] = useState<Record<string, unknown> | null>(null);
  const [adminToken, setAdminToken] = useState("");
  const [uploadProgress, setUploadProgress] = useState(0);
  const [diagnostics, setDiagnostics] = useState<HealthStatus | null>(null);
  const [juryStatus, setJuryStatus] = useState<JuryStatus>({access: "public", research_enabled: false, operator_enabled: false});

  useEffect(() => {
    getHealth()
      .then((h) => {
        setDiagnostics(h);
        setAdminToken(window.sessionStorage.getItem("hermeneut_admin_token") ?? "");
      })
      .catch(() => {
        setDiagnostics(null);
        setAdminToken(window.sessionStorage.getItem("hermeneut_admin_token") ?? "");
      });
    getJuryStatus().then(setJuryStatus).catch(() => setJuryStatus({access: "public", research_enabled: false, operator_enabled: false}));
  }, []);

  useEffect(() => {
    const sourceId = libraryResult?.source_id;
    if (!sourceId) return;
    const activeSourceId = sourceId;
    let cancelled = false;
    async function poll() {
      try {
        const status = await getSourceStatus(activeSourceId);
        if (!cancelled) setSourceStatus(status);
      } catch {
        // Keep the last visible status; uploads can finish even if one poll misses.
      }
    }
    void poll();
    const timer = window.setInterval(() => {
      void poll();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [libraryResult?.source_id]);

  function saveAdminToken(value: string) {
    setAdminToken(value);
    if (value) {
      window.sessionStorage.setItem("hermeneut_admin_token", value);
    } else {
      window.sessionStorage.removeItem("hermeneut_admin_token");
    }
  }

  const liveElastic = diagnostics?.elastic === "connected";
  const operatorEnabled = (Boolean(adminToken) || juryStatus.operator_enabled) && liveElastic;
  const operatorDisabledReason = !liveElastic
    ? "Operator actions require Live Elastic; Backup Preview is read-only."
    : !adminToken && !juryStatus.operator_enabled
    ? "Open the jury access link or enter a local admin token to enable operator actions."
    : "";

  async function discover() {
    setLoading(true);
    setError(null);
    try {
      setHits(await discoverSources(query, undefined, query));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Discovery failed");
    } finally {
      setLoading(false);
    }
  }

  async function bootstrapElastic() {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/library/bootstrap-elastic", {
        method: "POST",
        headers: adminToken ? {Authorization: `Bearer ${adminToken}`} : undefined,
      });
      if (!response.ok) throw new Error(await response.text());
      setElasticStatus(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Elastic bootstrap failed");
    } finally {
      setLoading(false);
    }
  }

  async function uploadToLibrary() {
    setLoading(true);
    setError(null);
    setMessage(null);
    setUploadProgress(0);
    try {
      const shouldUseDirectGcsUpload =
        uploadFile && ((uploadFile.type || "").includes("pdf") || uploadFile.name.toLowerCase().endsWith(".pdf") || uploadFile.size > 1_000_000);
      if (shouldUseDirectGcsUpload) {
        const contentType = uploadFile.type || "application/pdf";
        const signed = await createLibraryUploadUrl({
          libraryId,
          filename: uploadFile.name,
          contentType,
          fileSize: uploadFile.size,
          title: sourceTitle || uploadFile.name,
          authorName: authorName || undefined,
          workId: workId || undefined,
          domain: domain || undefined,
          notes: notes || undefined,
        });
        await uploadFileDirectly(signed.upload_url, uploadFile, contentType, setUploadProgress);
        const result = await completeLibraryUpload({
          libraryId,
          sourceId: signed.source_id,
          rawObject: signed.raw_object,
          contentType,
          title: sourceTitle || uploadFile.name,
          authorName: authorName || undefined,
          workId: workId || undefined,
          domain: domain || undefined,
          notes: notes || undefined,
        });
        setLibraryResult(result);
        setSourceStatus(null);
        setMessage("Direct GCS upload completed. Source is registered; start OCR/indexing from the status panel when you are ready.");
        return;
      }
      const result = await addLibrarySource({
        libraryId,
        provider: "Institutional Upload",
        url: sourceUrl || undefined,
        file: uploadFile,
        title: sourceTitle || uploadFile?.name,
        authorName: authorName || undefined,
        workId: workId || undefined,
        domain: domain || undefined,
        notes: notes || undefined,
      });
      setLibraryResult(result);
      setSourceStatus(null);
      setMessage("Source registered in the library. Start OCR/indexing from the status panel when you are ready.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Library source upload failed");
    } finally {
      setLoading(false);
    }
  }

  async function importShamsiyyaLibrary() {
    setLoading(true);
    setError(null);
    try {
      setShamsiyyaResult(await importShamsiyyaDocx(shamsiyyaFiles));
      setLibraryId("shamsiyya_hashiya_demo");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Shamsiyya DOCX import failed");
    } finally {
      setLoading(false);
    }
  }

  async function runRelationshipAnalyst() {
    setLoading(true);
    setError(null);
    try {
      setRelationshipResult(await analyzeLibraryRelationships(libraryId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gemini relationship analysis failed");
    } finally {
      setLoading(false);
    }
  }

  async function ingest(hit: SourceHit) {
    setLoading(true);
    setError(null);
    try {
      const result = await ingestSource(hit);
      setIngests((current) => ({...current, [`${hit.provider}-${hit.source_id}`]: result}));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Controlled ingest failed");
    } finally {
      setLoading(false);
    }
  }

  async function process(hit: SourceHit) {
    setLoading(true);
    setError(null);
    try {
      const result = await processSource(hit.source_id);
      setProcessed((current) => ({...current, [`${hit.provider}-${hit.source_id}`]: result}));
    } catch (err) {
      setError(err instanceof Error ? err.message : "OCR/text processing failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-5 py-8">
      <section className="rounded-md border border-line bg-white/92 p-5 shadow-soft">
        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-sage">
          <SearchCode size={18} />
          Controlled source discovery and institutional library ingest
        </div>
        <label className="mb-4 block max-w-xl text-sm font-medium text-ink/70">
          Admin token
          <input
            className="mt-1 w-full rounded-md border border-line px-3 py-2"
            placeholder="Stored for this browser session only"
            type="password"
            value={adminToken}
            onChange={(event) => saveAdminToken(event.target.value)}
          />
          {adminToken ? (
            <button className="mt-2 rounded-md border border-line bg-white px-3 py-1 text-xs font-semibold text-ink/70" type="button" onClick={() => saveAdminToken("")}>
              Clear token
            </button>
          ) : null}
        </label>
        {diagnostics ? (
          <details className="mb-4 rounded-md border border-line bg-paper p-3">
            <summary className="cursor-pointer text-sm font-semibold text-sage">Operator diagnostics</summary>
            <div className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
              {[
                ["Elastic", diagnostics.elastic],
                ["MCP", diagnostics.elastic_mcp],
                ["Worker", diagnostics.run_worker],
                ["Job queue", diagnostics.job_queue],
                ["GCS", diagnostics.gcs],
                ["Snapshots", diagnostics.run_snapshot_store],
                ["Schema", diagnostics.elastic_schema_version],
              ].map(([label, value]) => <span key={label} className="safe-text rounded-md bg-white px-2 py-1">{label}: {value ?? "unknown"}</span>)}
            </div>
          </details>
        ) : null}
        {!operatorEnabled ? (
          <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            {operatorDisabledReason}
          </div>
        ) : null}
        <div className="mb-4 flex flex-wrap gap-2">
          <button className={tab === "library" ? "rounded-md bg-umber px-3 py-2 text-sm font-semibold text-paper" : "rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold"} onClick={() => setTab("library")}>
            Upload to Library
          </button>
          <button className={tab === "web" ? "rounded-md bg-umber px-3 py-2 text-sm font-semibold text-paper" : "rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold"} onClick={() => setTab("web")}>
            Discover from Web
          </button>
        </div>
        <div className="mb-4 grid gap-3 text-xs leading-5 text-ink/70 md:grid-cols-4">
          <div className="rounded-md bg-paper p-3">
            <div className="font-semibold text-sage">1. Discovered</div>
            Agent proposes OpenITI, Internet Archive, Wikidata, or demo-library candidates.
          </div>
          <div className="rounded-md bg-paper p-3">
            <div className="font-semibold text-sage">2. Approved</div>
            An admin approves rights, source scope, and institutional library visibility.
          </div>
          <div className="rounded-md bg-paper p-3">
            <div className="font-semibold text-sage">3. Raw stored</div>
            Admin-approved sources are stored under the GCS document vault.
          </div>
          <div className="rounded-md bg-paper p-3">
            <div className="font-semibold text-sage">4. OCR + searchable</div>
            Text/OCR passages are normalized and indexed in Elastic with evidence metadata.
          </div>
        </div>
        {tab === "library" ? (
          <div className="grid gap-3">
            <label className="text-sm font-medium text-ink/70">
              Library ID
              <input className="mt-1 w-full rounded-md border border-line px-3 py-2" value={libraryId} onChange={(event) => setLibraryId(event.target.value)} />
            </label>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="text-sm font-medium text-ink/70">
                Source title
                <input className="mt-1 w-full rounded-md border border-line px-3 py-2" value={sourceTitle} onChange={(event) => setSourceTitle(event.target.value)} placeholder="شرح المطالع" />
              </label>
              <label className="text-sm font-medium text-ink/70">
                Author
                <input className="mt-1 w-full rounded-md border border-line px-3 py-2" value={authorName} onChange={(event) => setAuthorName(event.target.value)} placeholder="Qutb al-Din al-Razi" />
              </label>
              <label className="text-sm font-medium text-ink/70">
                Work ID
                <input className="mt-1 w-full rounded-md border border-line px-3 py-2" value={workId} onChange={(event) => setWorkId(event.target.value)} placeholder="qutb-razi-sharh-matali" />
              </label>
              <label className="text-sm font-medium text-ink/70">
                Domain
                <input className="mt-1 w-full rounded-md border border-line px-3 py-2" value={domain} onChange={(event) => setDomain(event.target.value)} />
              </label>
            </div>
            <label className="text-sm font-medium text-ink/70">
              Scholar notes
              <textarea className="mt-1 min-h-20 w-full rounded-md border border-line px-3 py-2" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Relationship hints, edition notes, ownership notes, or expected references." />
            </label>
            <label className="text-sm font-medium text-ink/70">
              PDF/Text file
              <input className="mt-1 w-full rounded-md border border-line bg-white px-3 py-2" type="file" accept=".pdf,.txt,text/plain,application/pdf" onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)} />
            </label>
            <label className="text-sm font-medium text-ink/70">
              Or source URL
              <input className="mt-1 w-full rounded-md border border-line px-3 py-2" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://archive.org/download/.../source.pdf" />
            </label>
            {uploadFile && uploadFile.size > 500_000_000 ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                Files above 500 MB require manual GCS import before registration.
              </div>
            ) : null}
            <button className="flex min-h-11 items-center justify-center gap-2 rounded-md bg-umber px-4 text-sm font-semibold text-paper disabled:opacity-45" onClick={uploadToLibrary} disabled={loading || !operatorEnabled || Boolean(uploadFile && uploadFile.size > 500_000_000)}>
              {loading ? <Loader2 className="animate-spin" size={18} /> : <SearchCode size={18} />}
              Upload PDF to GCS vault
            </button>
            {uploadProgress ? (
              <div className="rounded-md bg-paper p-3 text-xs text-ink/70">
                Direct GCS upload: {uploadProgress}%
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-white">
                  <div className="h-full bg-sage" style={{width: `${uploadProgress}%`}} />
                </div>
              </div>
            ) : null}
            <button className="flex min-h-11 items-center justify-center gap-2 rounded-md border border-line bg-white px-4 text-sm font-semibold text-ink disabled:opacity-45" onClick={runRelationshipAnalyst} disabled={loading || !operatorEnabled}>
              {loading ? <Loader2 className="animate-spin" size={18} /> : <SearchCode size={18} />}
              Analyze library relationships with Gemini Pro
            </button>
            {libraryResult ? (
              <div className="rounded-md border border-line bg-paper p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-sage">
                  <Activity size={17} />
                  OCR job status
                </div>
                <div className="grid gap-2 text-xs leading-5 text-ink/70 md:grid-cols-2">
                  <span className="rounded-md bg-white px-2 py-1">source: {libraryResult.source_id}</span>
                  <span className="rounded-md bg-white px-2 py-1">job: {String(sourceStatus?.processing_job_id ?? libraryResult.metadata.processing_job_id ?? "queued")}</span>
                  <span className="rounded-md bg-white px-2 py-1">status: {String(sourceStatus?.lifecycle_status ?? libraryResult.ingestion_status)}</span>
                  <span className="rounded-md bg-white px-2 py-1">ocr: {String(sourceStatus?.ocr_status ?? libraryResult.metadata.ocr_status ?? "ocr_pending")}</span>
                  <span className="rounded-md bg-white px-2 py-1">pages: {String(sourceStatus?.ocr_page_count ?? 0)}</span>
                  <span className="rounded-md bg-white px-2 py-1">processed: {String(sourceStatus?.ocr_processed_pages ?? libraryResult.metadata.ocr_processed_pages ?? 0)} / {String(sourceStatus?.ocr_total_pages ?? libraryResult.metadata.ocr_total_pages ?? "unknown")}</span>
                  <span className="rounded-md bg-white px-2 py-1">next page: {String(sourceStatus?.ocr_next_page ?? libraryResult.metadata.ocr_next_page ?? "none")}</span>
                  <span className="rounded-md bg-white px-2 py-1">passages: {String(sourceStatus?.indexed_passage_count ?? 0)}</span>
                  <span className="rounded-md bg-white px-2 py-1">graph: {String(sourceStatus?.graph_status ?? libraryResult.metadata.graph_status ?? "pending_after_ocr")}</span>
                  <span className="rounded-md bg-white px-2 py-1">counts as evidence: {sourceStatus?.lifecycle_status === "searchable" ? "yes" : "not yet"}</span>
                </div>
                <div className="mt-3 h-2 overflow-hidden rounded-full bg-white">
                  <div className="h-full bg-umber transition-all" style={{width: `${Math.min(100, Math.max(5, sourceStatus?.progress_percent ?? 5))}%`}} />
                </div>
                <button
                  className="mt-3 flex min-h-10 items-center justify-center gap-2 rounded-md bg-umber px-3 text-sm font-semibold text-paper"
                  onClick={async () => {
                    if (!libraryResult?.source_id) return;
                    setLoading(true);
                    setError(null);
                    try {
                      const result = await processSource(libraryResult.source_id);
                      setLibraryResult(result);
                    } catch (err) {
                      setError(err instanceof Error ? err.message : "Could not start OCR/index job");
                    } finally {
                      setLoading(false);
                    }
                  }}
                  disabled={loading || !operatorEnabled || sourceStatus?.lifecycle_status === "searchable"}
                >
                  {loading ? <Loader2 className="animate-spin" size={16} /> : <Activity size={16} />}
                  Start OCR/index job
                </button>
                <a
                  className="ml-0 mt-3 inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-semibold text-ink sm:ml-2"
                  href={`/sources/${encodeURIComponent(libraryResult.source_id)}`}
                >
                  Open OCR page editor
                </a>
                <AdvancedDetails value={sourceStatus ?? libraryResult} />
              </div>
            ) : null}
            {relationshipResult ? (
              <ResultSummary title="Relationship analysis" value={relationshipResult} />
            ) : null}
            <div className="mt-2 rounded-md border border-line bg-paper p-3">
              <div className="text-sm font-semibold text-sage">Shamsiyya layered DOCX demo import</div>
              <p className="mt-1 text-xs leading-5 text-ink/65">
                Upload the Tasawwurat/Tasdiqat DOCX files together to split Katibi, Qutb al-Razi, Sayyid Sharif, Siyalkuti, and Isam into separate searchable sources.
              </p>
              <input
                className="mt-3 w-full rounded-md border border-line bg-white px-3 py-2 text-sm"
                type="file"
                accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                multiple
                onChange={(event) => setShamsiyyaFiles(Array.from(event.target.files ?? []))}
              />
              <button
                className="mt-3 flex min-h-10 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-semibold"
                onClick={importShamsiyyaLibrary}
                disabled={loading || !operatorEnabled || shamsiyyaFiles.length === 0}
              >
                {loading ? <Loader2 className="animate-spin" size={16} /> : <SearchCode size={16} />}
                Import layered Shamsiyya library
              </button>
              {shamsiyyaResult ? (
                <ResultSummary title="Layered import" value={shamsiyyaResult} />
              ) : null}
            </div>
          </div>
        ) : (
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            className="min-h-11 flex-1 rounded-md border border-line px-3 outline-none focus:border-copper"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            className="flex min-h-11 items-center justify-center gap-2 rounded-md bg-umber px-4 text-sm font-semibold text-paper"
            onClick={discover}
            disabled={loading}
          >
            {loading ? <Loader2 className="animate-spin" size={18} /> : <SearchCode size={18} />}
            Discover
          </button>
          <button
            className="flex min-h-11 items-center justify-center gap-2 rounded-md border border-line bg-white px-4 text-sm font-semibold text-ink disabled:opacity-45"
            onClick={bootstrapElastic}
            disabled={loading || !operatorEnabled}
          >
            Bootstrap Elastic
          </button>
        </div>
        )}
        {message ? <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</p> : null}
        {error ? <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}
        {elasticStatus ? (
          <ResultSummary title="Elastic bootstrap" value={elasticStatus} />
        ) : null}
      </section>
      <div className="mt-5 grid gap-4">
        {hits.map((hit) => (
          <article key={`${hit.provider}-${hit.source_id}`} className="rounded-md border border-line bg-white p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm text-sage">{hit.provider}</div>
                <h2 className="font-semibold">{hit.title}</h2>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-paper px-2 py-1 text-xs">
                  {String(hit.metadata.ingestion_status ?? "discovered")}
                </span>
                <span className="rounded-md bg-paper px-2 py-1 text-xs">{Math.round(hit.quality * 100)}%</span>
              </div>
            </div>
            <a className="mt-3 block break-words text-sm text-copper" href={hit.url} target="_blank">
              {hit.url}
            </a>
            <p className="mt-2 text-sm text-ink/65">{hit.license_note}</p>
            <div className="mt-3 grid gap-2 text-xs text-ink/60 md:grid-cols-2">
              <span className="rounded-md bg-paper px-2 py-1">
                raw: {String(hit.metadata.gcs_raw_path ?? "not stored")}
              </span>
                <span className="rounded-md bg-paper px-2 py-1">
                  normalized: {String(hit.metadata.gcs_normalized_path ?? "not indexed")}
                </span>
              <span className="rounded-md bg-paper px-2 py-1">
                OCR: {String(hit.metadata.gcs_ocr_path ?? "pending after raw storage")}
              </span>
              <span className="rounded-md bg-paper px-2 py-1">
                policy: {String(hit.metadata.download_policy ?? "admin approval required")}
              </span>
              <span className="rounded-md bg-paper px-2 py-1">
                verification: {String(hit.metadata.verification_status ?? "metadata only")}
              </span>
              <span className="rounded-md bg-paper px-2 py-1">
                reason: {String(hit.metadata.relationship_reason ?? "source discovery match")}
              </span>
            </div>
            <button
              className="mt-3 rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold text-ink disabled:opacity-45"
              onClick={() => ingest(hit)}
              disabled={loading || !operatorEnabled}
            >
              Approve controlled ingest
            </button>
            <button
              className="ml-2 mt-3 rounded-md bg-umber px-3 py-2 text-sm font-semibold text-paper disabled:opacity-45"
              onClick={() => process(hit)}
              disabled={loading || !operatorEnabled}
            >
              Run OCR/text processing
            </button>
            {ingests[`${hit.provider}-${hit.source_id}`] ? (
              <ResultSummary title="Source lifecycle" value={ingests[`${hit.provider}-${hit.source_id}`]} />
            ) : null}
            {processed[`${hit.provider}-${hit.source_id}`] ? (
              <ResultSummary title="OCR and index result" value={processed[`${hit.provider}-${hit.source_id}`]} />
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}

function ResultSummary({title, value}: {title: string; value: Record<string, unknown>}) {
  const rows = Object.entries(value).filter(([, entry]) => ["string", "number", "boolean"].includes(typeof entry)).slice(0, 8);
  return (
    <div className="mt-3 rounded-md border border-line bg-paper p-3">
      <div className="text-sm font-semibold text-sage">{title}</div>
      <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">{rows.map(([key, entry]) => <span key={key} className="safe-text rounded-md bg-white px-2 py-1">{key.replaceAll("_", " ")}: {String(entry)}</span>)}</div>
      <AdvancedDetails value={value} />
    </div>
  );
}

function AdvancedDetails({value}: {value: unknown}) {
  return <details className="mt-3"><summary className="cursor-pointer text-xs font-semibold text-copper">Advanced details</summary><pre className="mt-2 max-h-56 overflow-auto rounded-md bg-white p-3 text-xs leading-5 text-ink/70">{JSON.stringify(value, null, 2)}</pre></details>;
}
