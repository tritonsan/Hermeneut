"use client";

import { useEffect, useState } from "react";
import { Bot, ChevronLeft, ChevronRight, Loader2, Lock, Save, SearchCode } from "lucide-react";
import { auditSourcePage, getHealth, getJuryStatus, getSourcePage, getSourceStatus, saveSourcePageCorrection, searchCatalog } from "@/lib/api";
import type { HealthStatus, JuryStatus, SourcePageData, SourceStatus } from "@/lib/types";

export function SourceEditor({sourceId}: {sourceId: string}) {
  const [pageNumber, setPageNumber] = useState(1);
  const [page, setPage] = useState<SourcePageData | null>(null);
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [audit, setAudit] = useState<Record<string, unknown> | null>(null);
  const [catalogQuery, setCatalogQuery] = useState(sourceId);
  const [catalogRecords, setCatalogRecords] = useState<Record<string, unknown>[]>([]);
  const [status, setStatus] = useState<SourceStatus | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [juryStatus, setJuryStatus] = useState<JuryStatus>({access: "public", research_enabled: false, operator_enabled: false});
  const [hasAdminToken, setHasAdminToken] = useState(false);
  const [tokenDraft, setTokenDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const auditSuggestions = normalizeAuditSuggestions(audit);

  useEffect(() => {
    const token = window.sessionStorage.getItem("hermeneut_admin_token") ?? "";
    setTokenDraft(token);
    setHasAdminToken(Boolean(token));
    getHealth().then(setHealth).catch(() => setHealth(null));
    getJuryStatus().then(setJuryStatus).catch(() => setJuryStatus({access: "public", research_enabled: false, operator_enabled: false}));
  }, []);

  const liveElastic = health?.elastic === "connected";
  const operator = hasAdminToken || juryStatus.operator_enabled;
  const canMutate = operator && liveElastic;

  useEffect(() => {
    if (operator) return;
    let cancelled = false;
    async function loadStatus() {
      setLoading(true);
      setMessage(null);
      try {
        const data = await getSourceStatus(sourceId);
        if (!cancelled) setStatus(data);
      } catch (err) {
        if (!cancelled) setMessage(err instanceof Error ? err.message : "Could not load source status.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadStatus();
    return () => {
      cancelled = true;
    };
  }, [operator, sourceId]);

  useEffect(() => {
    if (!operator) {
      setPage(null);
      setText("");
      return;
    }
    let cancelled = false;
    async function load() {
      setLoading(true);
      setMessage(null);
      try {
        const data = await getSourcePage(sourceId, pageNumber);
        if (!cancelled) {
          setPage(data);
          setText(data.ocr_text || "");
        }
      } catch (err) {
        if (!cancelled) setMessage(err instanceof Error ? err.message : "Could not load OCR page.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [operator, sourceId, pageNumber]);

  function saveToken() {
    const token = tokenDraft.trim();
    if (token) {
      window.sessionStorage.setItem("hermeneut_admin_token", token);
      setHasAdminToken(true);
      setMessage("Operator token saved for this browser session.");
    }
  }

  function clearToken() {
    window.sessionStorage.removeItem("hermeneut_admin_token");
    setTokenDraft("");
    setHasAdminToken(false);
    setMessage("Operator token cleared.");
  }

  async function saveCorrection() {
    setLoading(true);
    setMessage(null);
    try {
      const result = await saveSourcePageCorrection(sourceId, pageNumber, text, reason || "Human OCR correction from editor.");
      setMessage(`Correction saved and reindexed: ${String(result.reindexed_passage_count ?? 0)} passage(s).`);
      const refreshed = await getSourcePage(sourceId, pageNumber);
      setPage(refreshed);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Correction failed.");
    } finally {
      setLoading(false);
    }
  }

  async function runAudit() {
    setLoading(true);
    setMessage(null);
    try {
      const result = await auditSourcePage(sourceId, pageNumber);
      setAudit(result);
      if (typeof result.apply_patch_text === "string" && result.apply_patch_text.trim()) {
        setText(result.apply_patch_text);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Gemini audit failed.");
    } finally {
      setLoading(false);
    }
  }

  async function runCatalogSearch() {
    setLoading(true);
    setMessage(null);
    try {
      setCatalogRecords(await searchCatalog({query: catalogQuery, protocol: "demo"}));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Catalog search failed.");
    } finally {
      setLoading(false);
    }
  }

  if (!operator) {
    return (
      <main className="mx-auto max-w-7xl px-5 py-8">
        <section className="rounded-md border border-line bg-white p-5 shadow-soft">
          <div className="flex items-center gap-2 text-sm font-semibold text-sage">
            <Lock size={17} />
            Operator source workspace
          </div>
          <h1 className="mt-2 safe-text text-2xl font-semibold">{sourceId}</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-ink/70">
            Source correction, Gemini page audit, and reindex actions open from the jury access link. Public users can see the source status but cannot edit OCR or mutate Elastic indexes from this surface.
          </p>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <input
              className="min-h-10 flex-1 rounded-md border border-line px-3 text-sm"
              onChange={(event) => setTokenDraft(event.target.value)}
              placeholder="Operator admin token"
              type="password"
              value={tokenDraft}
            />
            <button className="rounded-md bg-umber px-4 py-2 text-sm font-semibold text-paper" onClick={saveToken} type="button">
              Unlock editor
            </button>
          </div>
          {message ? <div className="mt-4 rounded-md bg-paper px-3 py-2 text-sm text-ink/70">{message}</div> : null}
        </section>
        <section className="mt-5 rounded-md border border-line bg-white p-5 shadow-soft">
          <div className="mb-4 text-sm font-semibold text-sage">Readonly source status</div>
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-ink/60">
              <Loader2 className="animate-spin" size={18} />
              Loading source status
            </div>
          ) : (
            <SourceStatusSummary status={status} sourceId={sourceId} />
          )}
        </section>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-7xl px-5 py-8">
      <section className="rounded-md border border-line bg-white p-5 shadow-soft">
        <p className="text-sm font-semibold text-sage">Human-in-the-Loop OCR Editor</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">{sourceId}</h1>
            <p className="mt-1 text-sm text-ink/65">Correct OCR, reindex Elastic passages, and store ground truth for future HTR training.</p>
          </div>
          <label className="text-sm font-medium text-ink/70">
            Page
            <span className="ml-2 inline-flex overflow-hidden rounded-md border border-line">
              <button className="px-3 py-2" type="button" onClick={() => setPageNumber((current) => Math.max(1, current - 1))}>
                <ChevronLeft size={16} />
              </button>
              <input className="w-20 border-x border-line px-3 py-2" type="number" min={1} value={pageNumber} onChange={(event) => setPageNumber(Number(event.target.value || 1))} />
              <button className="px-3 py-2" type="button" onClick={() => setPageNumber((current) => current + 1)}>
                <ChevronRight size={16} />
              </button>
            </span>
          </label>
        </div>
        {message ? <div className="mt-4 rounded-md bg-paper px-3 py-2 text-sm text-ink/70">{message}</div> : null}
        {!liveElastic ? (
          <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            Correction and Gemini audit require Live Elastic. Page viewing remains available with an operator token.
          </div>
        ) : null}
        <button className="mt-3 rounded-md border border-line bg-white px-3 py-1 text-xs font-semibold text-ink/70" type="button" onClick={clearToken}>
          Clear token
        </button>
      </section>

      <section className="mt-5 grid gap-5 lg:grid-cols-2">
        <div className="rounded-md border border-line bg-white p-4 shadow-soft">
          <div className="mb-3 text-sm font-semibold text-sage">PDF page preview</div>
          <div className="flex min-h-[560px] items-center justify-center overflow-auto rounded-md bg-paper">
            {page?.page_image ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={page.page_image} alt={`Page ${pageNumber}`} className="max-h-[780px] w-auto" />
            ) : (
              <div className="px-5 text-center text-sm text-ink/55">No rendered page image is available. GCS credentials and raw PDF are required for visual page preview.</div>
            )}
          </div>
        </div>
        <div className="rounded-md border border-line bg-white p-4 shadow-soft">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-semibold text-sage">OCR / transcription text</div>
            <div className="flex gap-2">
              <button className="flex items-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold disabled:opacity-45" onClick={runAudit} disabled={loading || !canMutate}>
                {loading ? <Loader2 className="animate-spin" size={16} /> : <Bot size={16} />}
                Gemini page audit
              </button>
              <button className="flex items-center gap-2 rounded-md bg-umber px-3 py-2 text-sm font-semibold text-paper disabled:opacity-45" onClick={saveCorrection} disabled={loading || !canMutate || !text.trim()}>
                {loading ? <Loader2 className="animate-spin" size={16} /> : <Save size={16} />}
                Save + reindex
              </button>
            </div>
          </div>
          <textarea className="min-h-[430px] w-full rounded-md border border-line px-3 py-2 font-serif text-lg leading-8 outline-none focus:border-copper" dir="rtl" value={text} onChange={(event) => setText(event.target.value)} />
          <input className="mt-3 w-full rounded-md border border-line px-3 py-2 text-sm" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Correction reason or note" />
          <div className="mt-3 grid gap-2 text-xs text-ink/60 md:grid-cols-2">
            <span className="rounded-md bg-paper px-2 py-1">method: {page?.extraction_method ?? "unknown"}</span>
            <span className={(page?.ocr_confidence ?? 1) < 0.72 ? "rounded-md bg-amber-50 px-2 py-1 text-amber-800" : "rounded-md bg-paper px-2 py-1"}>confidence: {String(page?.ocr_confidence ?? "n/a")}</span>
            <span className="rounded-md bg-paper px-2 py-1">corrections: {String(page?.corrections?.length ?? 0)}</span>
            <span className="rounded-md bg-paper px-2 py-1">normalized chars: {page?.normalized_preview?.length ?? 0}</span>
          </div>
          {page?.corrections?.length ? (
            <div className="mt-3 rounded-md bg-paper p-3 text-xs leading-5 text-ink/65">
              <div className="font-semibold text-sage">Correction history</div>
              {page.corrections.slice(0, 3).map((correction, index) => (
                <div key={index} className="mt-1 safe-text">{String(correction.reason ?? correction.correction_reason ?? "Human correction")}</div>
              ))}
            </div>
          ) : null}
        </div>
      </section>

      <section className="mt-5 grid gap-5 lg:grid-cols-2">
        <div className="rounded-md border border-line bg-white p-4 shadow-soft">
          <div className="text-sm font-semibold text-sage">Gemini audit suggestions</div>
          <div className="mt-3 space-y-3">
            {auditSuggestions.length ? auditSuggestions.map((suggestion, index) => (
              <article key={index} className="rounded-md border border-line bg-paper p-3 text-xs leading-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold text-sage">Page {String(suggestion.page ?? pageNumber)}</span>
                  <span className="rounded-md bg-white px-2 py-1">confidence: {String(suggestion.confidence ?? "n/a")}</span>
                </div>
                {suggestion.current ? <div className="mt-2 rounded-md bg-white p-2">Current: {String(suggestion.current)}</div> : null}
                {suggestion.suggested ? <div className="mt-2 rounded-md bg-white p-2">Suggested: {String(suggestion.suggested)}</div> : null}
                <p className="mt-2 text-ink/65">{String(suggestion.reason ?? "Gemini OCR audit suggestion.")}</p>
                {suggestion.suggested ? (
                  <button className="mt-3 rounded-md bg-umber px-3 py-2 font-semibold text-paper" onClick={() => setText(String(suggestion.suggested))}>
                    Apply suggestion
                  </button>
                ) : null}
              </article>
            )) : <div className="rounded-md bg-paper p-3 text-xs text-ink/65">No page audit yet.</div>}
          </div>
          {audit && !auditSuggestions.length ? (
            <details className="mt-3">
              <summary className="cursor-pointer text-xs font-semibold text-copper">Audit details</summary>
              <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-paper p-3 text-xs leading-5 text-ink/70">{JSON.stringify(audit, null, 2)}</pre>
            </details>
          ) : null}
        </div>
        <div className="rounded-md border border-line bg-white p-4 shadow-soft">
          <div className="text-sm font-semibold text-sage">Catalog / manuscript leads</div>
          <div className="mt-3 flex gap-2">
            <input className="min-h-10 flex-1 rounded-md border border-line px-3 text-sm" value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} />
            <button className="flex items-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-semibold" onClick={runCatalogSearch} disabled={loading}>
              <SearchCode size={16} />
              Search
            </button>
          </div>
          <div className="mt-3 max-h-80 space-y-3 overflow-auto">
            {catalogRecords.length ? catalogRecords.map((record, index) => (
              <article key={index} className="rounded-md border border-line bg-paper p-3 text-xs leading-5">
                <div className="font-semibold text-ink">{String(record.title ?? record.label ?? record.source_id ?? "Catalog lead")}</div>
                <div className="mt-1 text-copper">{String(record.author ?? record.author_name ?? record.provider ?? "")}</div>
                {record.url ? <a className="mt-2 block break-words text-copper" href={String(record.url)} target="_blank">{String(record.url)}</a> : null}
                <div className="mt-2 flex flex-wrap gap-2 text-ink/60">
                  {["work_id", "source_id", "file_type", "license_status", "quality"].map((key) => record[key] ? <span key={key} className="rounded-md bg-white px-2 py-1">{key}: {String(record[key])}</span> : null)}
                </div>
              </article>
            )) : <div className="rounded-md bg-paper p-3 text-xs text-ink/65">No catalog leads yet.</div>}
          </div>
        </div>
      </section>
    </main>
  );
}

function normalizeAuditSuggestions(audit: Record<string, unknown> | null) {
  if (!audit) return [];
  const candidates = [
    audit.suggestions,
    audit.corrections,
    audit.ocr_corrections,
    audit.issues,
  ].find(Array.isArray) as Record<string, unknown>[] | undefined;
  if (candidates?.length) {
    return candidates.map((item) => ({
      page: item.page ?? item.page_number,
      current: item.current ?? item.before ?? item.original_text,
      suggested: item.suggested ?? item.after ?? item.corrected_text ?? item.replacement,
      confidence: item.confidence ?? item.score,
      reason: item.reason ?? item.explanation ?? item.issue,
    }));
  }
  if (typeof audit.apply_patch_text === "string") {
    return [{
      page: audit.page ?? audit.page_number,
      current: audit.current ?? audit.original_text,
      suggested: audit.apply_patch_text,
      confidence: audit.confidence,
      reason: audit.reason ?? "Gemini returned a corrected page text.",
    }];
  }
  return [];
}

function SourceStatusSummary({status, sourceId}: {status: SourceStatus | null; sourceId: string}) {
  if (!status) {
    return <p className="rounded-md bg-paper p-3 text-sm text-ink/65">No public status is available for {sourceId}.</p>;
  }
  const metadata = status.metadata ?? {};
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div className="rounded-md bg-paper p-3">
        <div className="text-xs text-ink/55">Source</div>
        <div className="mt-1 safe-text font-semibold">{String(metadata.title ?? metadata.source_title ?? status.source_id)}</div>
        <div className="mt-2 safe-text text-xs text-ink/60">{String(metadata.provider ?? "provider not recorded")}</div>
      </div>
      <div className="rounded-md bg-paper p-3">
        <div className="text-xs text-ink/55">Searchability</div>
        <div className="mt-1 font-semibold">{status.indexed_passage_count} indexed passage(s)</div>
        <div className="mt-2 text-xs text-ink/60">OCR: {status.ocr_status} · quality: {status.ocr_quality_status ?? "unknown"}</div>
      </div>
      <div className="rounded-md bg-paper p-3">
        <div className="text-xs text-ink/55">Lifecycle</div>
        <div className="mt-1 font-semibold">{status.lifecycle_status || status.lifecycle}</div>
        <div className="mt-2 text-xs text-ink/60">Progress: {status.progress_percent}%</div>
      </div>
      <div className="rounded-md bg-paper p-3">
        <div className="text-xs text-ink/55">Graph</div>
        <div className="mt-1 font-semibold">{status.relationship_summary?.total_count ?? status.relationship_edge_count} relationship support(s)</div>
        <div className="mt-2 text-xs text-ink/60">Direct: {status.relationship_summary?.direct_count ?? 0} · Work-level: {status.relationship_summary?.work_count ?? 0}</div>
        <div className="mt-1 text-xs text-ink/60">Status: {status.relationship_summary?.status ?? status.graph_status}</div>
      </div>
    </div>
  );
}
