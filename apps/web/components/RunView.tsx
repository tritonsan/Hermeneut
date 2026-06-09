"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Braces, Check, CheckCircle2, Clipboard, Download, ExternalLink, FileText, Loader2, MapPin, Network, Search } from "lucide-react";
import { getEvidenceContext, getJuryStatus, getRun, retryRun, runAction } from "@/lib/api";
import type { AgentRun, EvidenceItem, JuryStatus } from "@/lib/types";
import { ConfidenceBar } from "./ConfidenceBar";

export function RunView({run}: {run: AgentRun}) {
  const [liveRun, setLiveRun] = useState(run);
  const [juryStatus, setJuryStatus] = useState<JuryStatus>({access: "public", research_enabled: false, operator_enabled: false});
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"scholar" | "debug">("scholar");
  const [contextByPassage, setContextByPassage] = useState<Record<string, Record<string, unknown>[]>>({});
  useEffect(() => {
    getJuryStatus().then(setJuryStatus).catch(() => setJuryStatus({access: "public", research_enabled: false, operator_enabled: false}));
  }, []);
  const isDebug = viewMode === "debug";
  const isActive = liveRun.status === "queued" || liveRun.status === "running" || liveRun.status === "waiting_source" || liveRun.status === "waiting_for_approval";
  const webIntel = liveRun.timeline.find((event) => event.label === "Bibliographic web intelligence gathered")?.payload;
  const sourceCandidates = liveRun.timeline.find((event) => event.label === "Source candidates discovered")?.payload;
  const memory = liveRun.timeline.find((event) => event.label === "Elastic evidence memory consulted")?.payload;
  const libraryScope = liveRun.timeline.find((event) => event.label === "Elastic library scope checked")?.payload;
  const retrievalEvent = liveRun.timeline.find((event) => event.label === "Elastic library retrieval");
  const memoryWriteEvent = liveRun.timeline.find((event) => event.label === "Evidence memory written");
  const academicIntelligence = (webIntel?.academic_intelligence ?? {}) as Record<string, unknown>;
  const academicSubagents = Array.isArray(academicIntelligence.subagents) ? academicIntelligence.subagents as Record<string, unknown>[] : [];
  const candidateDossiers = Array.isArray(academicIntelligence.candidate_dossiers) ? academicIntelligence.candidate_dossiers as Record<string, unknown>[] : [];
  const webHitAssessments = Array.isArray(academicIntelligence.web_hit_assessments) ? academicIntelligence.web_hit_assessments as Record<string, unknown>[] : [];
  const sourceSelection = (academicIntelligence.source_selection ?? {}) as Record<string, unknown>;
  const runningEvent = useMemo(
    () => [...liveRun.timeline].reverse().find((event) => event.status === "running") ?? liveRun.timeline.at(-1),
    [liveRun.timeline],
  );
  const bestEvidence = liveRun.evidence[0];
  const bestCandidate = liveRun.candidates[0];
  const containingSources = liveRun.source_lifecycle_records.filter((source) => String(source.source_role ?? "") === "containing_layer");
  const citationSources = liveRun.source_lifecycle_records.filter((source) => String(source.source_role ?? "citation_chain") !== "containing_layer");
  const searchableOpenSources = liveRun.source_lifecycle_records.filter((source) => String(source.lifecycle_status ?? "") === "searchable");
  const processedOpenSources = liveRun.source_lifecycle_records.filter((source) => Number(source.indexed_passage_count ?? 0) > 0 || String(source.lifecycle_status ?? "") === "searchable");
  const weakOcrSources = liveRun.source_lifecycle_records.filter((source) => String(source.ocr_quality_status ?? "").includes("weak"));
  const activeSourceRecords = liveRun.source_lifecycle_records.filter((source) => isActiveSourceStatus(String(source.lifecycle_status ?? "")));
  const selectedSourceRecords = liveRun.source_lifecycle_records.filter((source) => isSelectedSourceStatus(String(source.lifecycle_status ?? "")));
  const sourceProcessingVisible = liveRun.mode === "open_discovery" && !liveRun.evidence.length && (isActive || activeSourceRecords.length > 0 || selectedSourceRecords.length > 0);
  const showVerdictBoard = Boolean(bestEvidence) && !sourceProcessingVisible;
  const packetText = bestEvidence ? researchPacket(liveRun, bestEvidence) : "";
  const verification = {
    quote: Boolean(bestEvidence?.quote),
    location: Boolean(bestEvidence?.location_label || bestEvidence?.source_id || bestEvidence?.page_ref),
    metadata: Boolean(bestEvidence?.work_title && bestEvidence?.author_name),
    relationship: (bestEvidence?.relationship_fit_score ?? 0) > 0.25 || liveRun.relationship_graph.length > 0,
    anchor: bestEvidence?.verification_status === "anchored_quote" || bestEvidence?.verification_status === "anchored_passage_only",
    ocrQuality: !bestEvidence?.ocr_quality_status || !bestEvidence.ocr_quality_status.includes("weak"),
  };

  useEffect(() => {
    if (!isActive) return;
    const timer = window.setInterval(async () => {
      try {
        setLiveRun(await getRun(liveRun.run_id));
      } catch {
        // Keep the last known snapshot visible; the next poll may recover.
      }
    }, liveRun.status === "waiting_for_approval" ? 3500 : 1500);
    return () => window.clearInterval(timer);
  }, [isActive, liveRun.run_id, liveRun.status]);

  async function submitAction(
    action: "approve_download" | "reject_source" | "continue_without_source" | "retry_ocr",
    sourceId?: string,
  ) {
    const result = await runAction(liveRun.run_id, action, sourceId);
    setActionNote(result.note);
    setLiveRun(await getRun(liveRun.run_id));
  }

  async function retry() {
    setActionNote("Starting a new durable attempt...");
    setLiveRun(await retryRun(liveRun.run_id));
  }

  async function copyText(text: string, note: string) {
    await navigator.clipboard.writeText(text);
    setActionNote(note);
  }

  async function loadContext(passageId: string) {
    if (contextByPassage[passageId]) return;
    const result = await getEvidenceContext(passageId, 2);
    setContextByPassage((current) => ({...current, [passageId]: result.items}));
  }

  function downloadPacket() {
    if (!packetText) return;
    const blob = new Blob([packetText], {type: "text/markdown"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${liveRun.run_id}-research-packet.md`;
    link.click();
    URL.revokeObjectURL(url);
    setActionNote("Research packet downloaded.");
  }

  return (
    <main className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-5 sm:py-8 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.25fr)]">
      <section className="min-w-0 xl:col-span-2">
        <div className="sticky top-3 z-10 rounded-md border border-line bg-white/95 p-4 shadow-soft backdrop-blur">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-sage">
                {isActive ? <Loader2 className="animate-spin" size={17} /> : <CheckCircle2 size={17} />}
                Current Operation
              </div>
              <h2 className="mt-1 text-xl font-semibold">{liveRun.current_step}</h2>
              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                <span className="rounded-md bg-paper px-2 py-1">{liveRun.mode === "library" ? "Library Mode" : "Open Discovery Mode"}</span>
                <span className="rounded-md bg-paper px-2 py-1">phase: {liveRun.current_phase}</span>
                <span className={`rounded-md px-2 py-1 ${tierClass(liveRun.decision_tier)}`}>tier: {liveRun.decision_tier}</span>
                {liveRun.delayed ? <span className="rounded-md bg-amber-50 px-2 py-1 text-amber-700">delayed</span> : null}
                {liveRun.blocked_reason ? <span className="rounded-md bg-amber-50 px-2 py-1 text-amber-700">{liveRun.blocked_reason}</span> : null}
              </div>
              <p className="mt-1 text-sm leading-6 text-ink/65">
                {runningEvent?.progress_message ?? runningEvent?.detail ?? "Preparing the next research step."}
              </p>
            </div>
            <div className="min-w-64">
              <div className="mb-3 grid grid-cols-2 rounded-md border border-line bg-paper p-1 text-xs font-semibold">
                <button
                  className={viewMode === "scholar" ? "rounded bg-white px-3 py-2 shadow-sm" : "rounded px-3 py-2 text-ink/65"}
                  onClick={() => setViewMode("scholar")}
                  type="button"
                >
                  Scholar View
                </button>
                <button
                  className={viewMode === "debug" ? "rounded bg-white px-3 py-2 shadow-sm" : "rounded px-3 py-2 text-ink/65"}
                  onClick={() => setViewMode("debug")}
                  type="button"
                >
                  Debug Trace
                </button>
              </div>
              <div className="mb-2 flex justify-between text-xs text-ink/60">
                <span>{liveRun.execution_status ?? liveRun.status}</span>
                <span>{formatElapsed(liveRun.elapsed_seconds ?? 0)} elapsed</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-paper">
                <div className="h-full bg-umber transition-all" style={{width: `${liveRun.progress_percent}%`}} />
              </div>
              <div className="mt-2 text-right text-xs font-semibold text-copper">{liveRun.progress_percent}%</div>
              {liveRun.retryable ? (
                <button className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-md bg-night px-3 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45" disabled={!juryStatus.operator_enabled} onClick={retry} type="button">
                  <Activity size={15} /> Retry durable attempt
                </button>
              ) : null}
            </div>
          </div>
          {actionNote ? <p className="mt-3 rounded-md bg-paper px-3 py-2 text-xs text-ink/70">{actionNote}</p> : null}
        </div>
      </section>
      <section className="min-w-0 space-y-5 xl:col-span-2">
        {isActive || sourceProcessingVisible ? (
          <Panel title="Research in progress" icon={<Loader2 className="animate-spin" size={18} />}>
            <div className="grid gap-4 rounded-md border border-line bg-white p-4 md:grid-cols-[minmax(0,1fr)_220px]">
              <div>
                <div className="text-lg font-semibold text-night">{sourceProcessingVisible ? "Processing selected sources" : liveRun.current_step}</div>
                <p className="mt-2 text-sm leading-6 text-ink/65">{runningEvent?.progress_message ?? runningEvent?.detail ?? "Preparing the next research step."}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  <Badge label="phase" value={sourceProcessingVisible && !isActive ? "evidence refresh" : liveRun.current_phase} />
                  <Badge label="elapsed" value={formatElapsed(liveRun.elapsed_seconds ?? 0)} />
                  {liveRun.mode === "open_discovery" ? <Badge label="processed" value={processedOpenSources.length} /> : null}
                  {liveRun.mode === "open_discovery" ? <Badge label="searchable" value={searchableOpenSources.length} /> : null}
                  {liveRun.mode === "open_discovery" ? <Badge label="weak OCR" value={weakOcrSources.length} /> : null}
                </div>
              </div>
              <div className="rounded-md bg-paper p-4"><div className="text-3xl font-semibold text-night">{liveRun.progress_percent}%</div><div className="mt-1 text-xs text-ink/50">durable progress</div></div>
            </div>
          </Panel>
        ) : !showVerdictBoard ? (
          <Panel title="No textual evidence found" icon={<AlertTriangle size={18} />}>
            <div className="rounded-md border border-amber-200 bg-amber-50 p-5">
              <h2 className="text-xl font-semibold text-night">The investigation completed without claim-worthy textual evidence.</h2>
              <p className="mt-2 text-sm leading-6 text-ink/65">{liveRun.blocked_reason ?? "Candidate and graph leads remain visible below, but they were not promoted to evidence."}</p>
            </div>
          </Panel>
        ) : (
        <Panel title="Evidence Verdict Board" icon={<MapPin size={18} />}>
          <div className="overflow-hidden rounded-md border border-night/15 bg-night text-white shadow-board">
            <div className="grid gap-px bg-white/10 xl:grid-cols-[minmax(240px,0.78fr)_minmax(420px,1.35fr)_minmax(280px,0.9fr)]">
              <div className="bg-night p-4">
                <div className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ${tierClass(liveRun.decision_tier)}`}>
                  {liveRun.decision_tier}
                </div>
                <h2 className="mt-4 text-2xl font-semibold leading-tight text-white">{bestCandidate?.work_title ?? bestEvidence?.work_title ?? "No candidate yet"}</h2>
                <p className="mt-2 text-sm leading-6 text-white/70">{bestCandidate?.author ?? bestEvidence?.author_name ?? "Author metadata unresolved"}</p>
                <div className="mt-4 rounded-md bg-white/8 p-3">
                  <div className="text-xs uppercase text-white/55">confidence</div>
                  <div className="mt-1 text-3xl font-semibold text-white">{Math.round((bestEvidence?.confidence ?? bestCandidate?.confidence ?? 0) * 100)}%</div>
                </div>
              </div>
              <div className="subtle-grid bg-mist p-4 text-ink">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="text-xs font-semibold uppercase text-sage">Primary textual evidence</div>
                  {bestEvidence?.match_type ? <Badge tone="night">{bestEvidence.match_type}</Badge> : null}
                </div>
                {bestEvidence ? (
                  <EvidenceQuote quote={bestEvidence.quote} />
                ) : (
                  <div className="rounded-md border border-dashed border-line bg-white p-4 text-sm text-ink/65">
                    No Elastic evidence has been retrieved yet.
                  </div>
                )}
                {bestEvidence ? (
                  <div className="mt-3 rounded-md border border-line bg-white/95 px-3 py-2 text-sm leading-6 text-ink/75">
                    <span className="font-semibold text-sage">Why this answer? </span>
                    {answerReason(bestCandidate, bestEvidence)}
                  </div>
                ) : null}
              </div>
              <div className="bg-white p-4 text-ink">
                <div className="flex items-center gap-2 text-sm font-semibold text-sage">
                  <MapPin size={16} />
                  Where found
                </div>
                <div className="mt-3 rounded-md border border-line bg-paper px-3 py-2 text-sm leading-6">
                  {bestEvidence?.location_label ?? bestEvidence?.passage_id ?? "Location not resolved yet"}
                </div>
                <div className="mt-3 grid gap-2 text-xs text-ink/65">
                  <Badge label="source" value={bestEvidence?.source_title ?? bestEvidence?.source_id} />
                  <Badge label="page/ref" value={bestEvidence?.page_ref ?? bestEvidence?.source_page} />
                  <Badge label="role" value={bestEvidence?.source_role ? sourceRoleLabel(bestEvidence.source_role) : undefined} />
                  <Badge label="OCR" value={bestEvidence ? ocrQualityLabel(bestEvidence.ocr_quality_status) : undefined} />
                  <Badge label="verification" value={bestEvidence ? verificationLabel(bestEvidence) : undefined} />
                  <Badge label="anchor" value={bestEvidence ? anchorRange(bestEvidence) : undefined} />
                </div>
                <div className="mt-4 grid gap-2">
                  {bestEvidence?.citation_hint ? (
                    <ActionButton icon={<Clipboard size={15} />} onClick={() => copyText(bestEvidence.citation_hint ?? "", "Citation copied.")}>
                      Copy citation
                    </ActionButton>
                  ) : null}
                  {bestEvidence ? (
                    <ActionButton icon={<Clipboard size={15} />} onClick={() => copyText(packetText, "Research packet copied.")}>
                      Copy packet
                    </ActionButton>
                  ) : null}
                  {bestEvidence ? (
                    <ActionButton icon={<Download size={15} />} onClick={downloadPacket}>
                      Download .md
                    </ActionButton>
                  ) : null}
                  {bestEvidence?.source_page_url ? (
                    <ActionLink icon={<ExternalLink size={15} />} href={bestEvidence.source_page_url}>
                      Open source
                    </ActionLink>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
          {bestEvidence && (bestEvidence.anchor_text_before || bestEvidence.anchor_text_after) ? (
            <div className="mt-4 rounded-md border border-line bg-white p-3 text-xs leading-5 text-ink/65">
              <div className="font-semibold text-sage">Anchor context</div>
              <div className="mt-1 safe-text">{bestEvidence.anchor_text_before ? `...${bestEvidence.anchor_text_before}` : ""}<span className="font-semibold text-copper"> [quote] </span>{bestEvidence.anchor_text_after ? `${bestEvidence.anchor_text_after}...` : ""}</div>
            </div>
          ) : null}
          <div className="mt-4 flex items-center gap-2 text-sm font-semibold text-sage">
            <CheckCircle2 size={16} />
            Verification checklist
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <CheckItem label="Exact quote" ok={verification.quote} />
            <CheckItem label="Location" ok={verification.location} />
            <CheckItem label="Metadata" ok={verification.metadata} />
            <CheckItem label="Anchor" ok={verification.anchor} />
            <CheckItem label="Relationship" ok={verification.relationship} />
            <CheckItem label="OCR quality" ok={verification.ocrQuality} />
          </div>
          <div className="mt-4 grid gap-2 rounded-md border border-line bg-paper p-3 text-xs text-ink/70 sm:grid-cols-4">
            <div>
              <div className="font-semibold text-sage">Library scope</div>
              <div className="mt-1 safe-text">{String(bestEvidence?.library_id ?? libraryScope?.library_id ?? "not recorded")}</div>
            </div>
            <div>
              <div className="font-semibold text-sage">Retrieval</div>
              <div className="mt-1">{bestEvidence?.retrieval_backend ?? retrievalEvent?.tool ?? "pending"} · {bestEvidence?.retrieval_mode ?? "n/a"}</div>
            </div>
            <div>
              <div className="font-semibold text-sage">Evidence memory</div>
              <div className="mt-1">{memory ? "checked" : "not checked"} / {memoryWriteEvent ? "written" : "not written yet"}</div>
            </div>
            <div>
              <div className="font-semibold text-sage">Graph support</div>
              <div className="mt-1">{liveRun.relationship_graph.length} relation(s)</div>
            </div>
          </div>
          {liveRun.candidates.length ? (
            <div className="mt-4 grid gap-3 md:hidden">
              {liveRun.candidates.slice(0, 3).map((candidate) => {
                const candidateEvidence = liveRun.evidence.filter((item) => item.work_id === candidate.work_id);
                const strongest = candidateEvidence[0];
                return (
                  <article key={candidate.work_id} className="rounded-md border border-line bg-white p-3 text-xs shadow-insetLine">
                    <div className="safe-text font-semibold text-ink">{candidate.work_title}</div>
                    <div className="safe-text mt-1 text-ink/55">{candidate.author}</div>
                    <div className="mt-3 grid grid-cols-2 gap-2">
                      <Badge label="confidence" value={`${Math.round(candidate.confidence * 100)}%`} />
                      <Badge label="evidence" value={`${candidateEvidence.length} hit(s)`} />
                    </div>
                    <div className="mt-3 rounded-md bg-mist p-3">
                      <div className="text-[11px] font-semibold uppercase text-sage">strongest quote</div>
                      <div className="evidence-quote mt-1 max-h-32 overflow-auto border-0 bg-transparent p-0 text-base leading-8 shadow-none">
                        {strongest?.quote ? `${strongest.quote.slice(0, 180)}${strongest.quote.length > 180 ? "..." : ""}` : "No indexed quote yet"}
                      </div>
                    </div>
                    <p className="mt-3 safe-text leading-5 text-ink/65">{candidate.why}</p>
                    <p className="mt-2 safe-text leading-5 text-ink/55">Weakness: {candidateWeakness(candidateEvidence)}</p>
                  </article>
                );
              })}
            </div>
          ) : null}
          {liveRun.candidates.length ? (
            <div className="mt-4 hidden overflow-auto rounded-md border border-line md:block">
              <table className="w-full min-w-[760px] text-left text-xs">
                <thead className="bg-paper text-ink/60">
                  <tr>
                    <th className="px-3 py-2">Candidate</th>
                    <th className="px-3 py-2">Confidence</th>
                    <th className="px-3 py-2">Evidence</th>
                    <th className="px-3 py-2">Strongest quote</th>
                    <th className="px-3 py-2">Why</th>
                    <th className="px-3 py-2">Weakness</th>
                  </tr>
                </thead>
                <tbody>
                  {liveRun.candidates.slice(0, 3).map((candidate) => {
                    const candidateEvidence = liveRun.evidence.filter((item) => item.work_id === candidate.work_id);
                    const strongest = candidateEvidence[0];
                    return (
                      <tr key={candidate.work_id} className="border-t border-line align-top">
                        <td className="px-3 py-2 font-semibold">{candidate.work_title}<div className="text-ink/55">{candidate.author}</div></td>
                        <td className="px-3 py-2">{Math.round(candidate.confidence * 100)}%</td>
                        <td className="px-3 py-2">{candidateEvidence.length} hit(s)</td>
                        <td className="arabic max-w-52 px-3 py-2 leading-7 text-ink/70">{strongest?.quote ? `${strongest.quote.slice(0, 150)}${strongest.quote.length > 150 ? "..." : ""}` : "No indexed quote yet"}</td>
                        <td className="px-3 py-2 text-ink/65">{candidate.why}</td>
                        <td className="px-3 py-2 text-ink/60">{candidateWeakness(candidateEvidence)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </Panel>
        )}
        <Panel title="Input" icon={<FileText size={18} />}>
          <div className="arabic rounded-md bg-paper p-4 text-lg">{liveRun.input_passage}</div>
        </Panel>
        <Panel title="Detected Context" icon={<Activity size={18} />}>
          <dl className="space-y-3 text-sm">
            <Metric label="Language" value={liveRun.detected_context.language} />
            <Metric label="Domain" value={liveRun.detected_context.domain} />
            <Metric label="Period" value={liveRun.detected_context.period_hint} />
            <Metric label="Citation" value={liveRun.detected_context.citation_type} />
          </dl>
          <div className="mt-3 flex flex-wrap gap-2">
            {liveRun.detected_context.key_terms.map((term) => (
              <span key={term} className="rounded-md bg-paper px-2 py-1 text-xs text-ink/70">
                {term}
              </span>
            ))}
          </div>
        </Panel>
        {liveRun.mode === "open_discovery" ? <Panel title="Candidate Graph" icon={<Network size={18} />}>
          <div className="space-y-2">
            {liveRun.hypotheses.map((item) => (
              <div key={`${item.author}-${item.work}`} className="rounded-md border border-line p-3">
                <div className="text-sm font-semibold">{item.author}</div>
                <div className="text-sm text-copper">{item.work}</div>
                <p className="mt-2 text-xs leading-5 text-ink/65">{item.reason}</p>
              </div>
            ))}
          </div>
        </Panel> : null}
        {liveRun.relationship_graph.length ? <Panel title="Library Relationship Intelligence" icon={<Network size={18} />}>
          <p className="mb-3 text-sm leading-6 text-ink/70">
            Hermeneut uses source/work relations to keep flat passage search from confusing base texts, commentaries, hashiyas, polemics, and parallel debate layers.
          </p>
          <div className="space-y-2">
            {liveRun.relationship_graph.slice(0, isDebug ? 20 : 6).map((edge, index) => (
              <div key={`${String(edge.edge_id ?? edge.from_id)}-${index}`} className="rounded-md border border-line bg-paper/60 p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">{String(edge.relation ?? "relation")}</span>
                  <span className="rounded-md bg-white px-2 py-1 text-xs">confidence: {String(edge.confidence ?? "n/a")}</span>
                </div>
                <div className="mt-2 break-words text-xs text-ink/70">
                  {String(edge.from_id ?? edge.from)} → {String(edge.to_id ?? edge.to)}
                </div>
                <p className="mt-2 text-xs leading-5 text-ink/60">{String(edge.reasoning_summary ?? edge.reason ?? "")}</p>
                {isDebug ? (
                  <div className="mt-3 grid gap-2 text-xs text-ink/60">
                    <span className="rounded-md bg-white px-2 py-1">family: {String(edge.relation_family ?? "n/a")}</span>
                    <span className="rounded-md bg-white px-2 py-1">ranking effect: {String(edge.candidate_ranking_effect ?? "context_only")}</span>
                    <span className="rounded-md bg-white px-2 py-1">chronology: {String(edge.chronology_basis ?? edge.chronology_status ?? "uncertain")}</span>
                    {edge.counter_evidence ? (
                      <span className="rounded-md bg-amber-50 px-2 py-1 text-amber-800">counter: {String(edge.counter_evidence)}</span>
                    ) : null}
                    <details>
                      <summary className="cursor-pointer font-semibold text-copper">Edge audit JSON</summary>
                      <pre className="mt-2 max-h-52 overflow-auto rounded-md bg-white p-3 text-xs leading-5 text-ink/70">
                        {JSON.stringify(edge, null, 2)}
                      </pre>
                    </details>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Context Profile" icon={<Activity size={18} />}>
          <p className="mb-3 text-sm leading-6 text-ink/70">
            This profile is generated before source discovery and is used only to steer candidate research.
          </p>
          <pre className="max-h-72 overflow-auto rounded-md bg-paper p-3 text-xs leading-5 text-ink/70">
            {JSON.stringify(liveRun.context_profile ?? {}, null, 2)}
          </pre>
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Professor-Grade Subagents" icon={<Braces size={18} />}>
          <p className="mb-3 text-sm leading-6 text-ink/70">
            Each sub-agent has a separate prompt, JSON output, decision, and uncertainty trail.
          </p>
          <div className="space-y-3">
            {academicSubagents.map((agent, index) => (
              <details key={`${String(agent.agent_id ?? "agent")}-${index}`} className="rounded-md border border-line bg-paper/60 p-3">
                <summary className="cursor-pointer text-sm font-semibold">
                  {String(agent.title ?? agent.agent_id)} · {String(agent.decision ?? "decision pending")}
                </summary>
                {agent.rejection_reason ? (
                  <p className="mt-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800">{String(agent.rejection_reason)}</p>
                ) : null}
                <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-white p-3 text-xs leading-5 text-ink/70">
                  {JSON.stringify(agent.output ?? agent, null, 2)}
                </pre>
              </details>
            ))}
          </div>
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Candidate Dossiers" icon={<CheckCircle2 size={18} />}>
          <div className="space-y-3">
            {candidateDossiers.length ? candidateDossiers.map((dossier, index) => (
              <div key={`${String(dossier.candidate ?? "candidate")}-${index}`} className="rounded-md border border-line p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold">{String(dossier.candidate ?? "candidate")}</div>
                    <div className="text-xs text-copper">{String(dossier.role ?? "role not recorded")}</div>
                  </div>
                  <span className="rounded-md bg-paper px-2 py-1 text-xs">{String(dossier.relationship_type ?? "relationship")}</span>
                </div>
                <p className="mt-2 text-xs leading-5 text-ink/65">{String(dossier.why_candidate ?? "No candidate reason recorded.")}</p>
                <div className="mt-2 grid gap-2 text-xs text-ink/60">
                  <span className="rounded-md bg-paper px-2 py-1">confirm: {String(dossier.what_would_confirm ?? "not recorded")}</span>
                  <span className="rounded-md bg-paper px-2 py-1">disconfirm: {String(dossier.what_would_disconfirm ?? "not recorded")}</span>
                  <span className="rounded-md bg-paper px-2 py-1">uncertainty: {String(dossier.uncertainty ?? "not recorded")}</span>
                </div>
              </div>
            )) : <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">No model-generated candidate dossiers were returned for this run.</p>}
          </div>
        </Panel> : null}
        {liveRun.mode === "open_discovery" ? <Panel title="Relationship Reasoning" icon={<Network size={18} />}>
          <div className="space-y-2">
            {liveRun.relationship_graph.slice(0, 8).map((edge, index) => (
              <div key={`${String(edge.to_id)}-${index}`} className="rounded-md border border-line bg-paper/60 p-3 text-sm">
                <div className="font-semibold">{String(edge.relation ?? "candidate relation")}</div>
                <p className="mt-1 text-xs leading-5 text-ink/65">{String(edge.reason ?? "No reason recorded.")}</p>
                <div className="mt-2 text-xs text-copper">confidence: {String(edge.confidence ?? "n/a")}</div>
              </div>
            ))}
          </div>
        </Panel> : null}
        {liveRun.mode === "open_discovery" ? <Panel title="Author Candidates" icon={<CheckCircle2 size={18} />}>
          <div className="space-y-3">
            {liveRun.author_candidates.slice(0, 8).map((candidate) => (
              <div key={String(candidate.author_id)} className="rounded-md border border-line p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold">{String(candidate.name)}</div>
                    <div className="text-xs text-ink/60">{String(candidate.tradition ?? "unknown tradition")}</div>
                  </div>
                  <span className="rounded-md bg-paper px-2 py-1 text-xs">{String(candidate.score ?? "n/a")}</span>
                </div>
                <p className="mt-2 text-xs leading-5 text-ink/65">{String(candidate.relationship_reason ?? "Candidate reasoning.")}</p>
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-semibold text-copper">Score breakdown</summary>
                  <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-paper p-3 text-xs leading-5 text-ink/70">
                    {JSON.stringify(candidate.score_breakdown ?? {}, null, 2)}
                  </pre>
                </details>
              </div>
            ))}
          </div>
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Bibliographic Web Intelligence" icon={<Search size={18} />}>
          <p className="mb-3 text-sm leading-6 text-ink/70">
            These findings narrow the candidate graph, but they do not count as final textual evidence.
          </p>
          <pre className="max-h-72 overflow-auto rounded-md bg-paper p-3 text-xs leading-5 text-ink/70">
            {JSON.stringify(webIntel ?? {}, null, 2)}
          </pre>
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Phrase Variants" icon={<Search size={18} />}>
          <div className="space-y-2">
            {liveRun.phrase_variants.map((variant, index) => (
              <div key={`${String(variant.kind)}-${index}`} className="rounded-md bg-paper p-3">
                <div className="text-xs font-semibold text-sage">{String(variant.kind)}</div>
                <div className="mt-1 break-words text-sm">{String(variant.query)}</div>
                <p className="mt-1 text-xs leading-5 text-ink/60">{String(variant.purpose ?? "")}</p>
              </div>
            ))}
          </div>
        </Panel> : null}
        {isDebug ? <Panel title="Elastic Context And Memory" icon={<Network size={18} />}>
          <div className="mb-3 grid gap-2 text-xs text-ink/65">
            <div className="rounded-md bg-paper p-2">
              <div className="font-semibold text-sage">Library scope</div>
              <pre className="mt-1 overflow-auto">{JSON.stringify(libraryScope ?? {}, null, 2)}</pre>
            </div>
            <div className="rounded-md bg-paper p-2">
              <div className="font-semibold text-sage">Evidence memory lookup</div>
              <pre className="mt-1 max-h-56 overflow-auto">{JSON.stringify(memory ?? {}, null, 2)}</pre>
            </div>
          </div>
        </Panel> : null}
      </section>

      <section className="min-w-0 space-y-5">
        {isDebug ? <Panel title="Agent Timeline" icon={<CheckCircle2 size={18} />}>
          <div className="space-y-3">
            {liveRun.timeline.map((event) => (
              <div key={event.label} className="rounded-md border border-line bg-paper/60 p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 font-semibold">
                    <StatusDot status={event.status} />
                    {event.label}
                  </div>
                  <span className="rounded-md bg-white px-2 py-1 text-xs text-sage">{event.tool}</span>
                </div>
                <p className="mt-2 text-sm leading-6 text-ink/70">{event.detail}</p>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-ink/55">
                  <span className="rounded-md bg-white px-2 py-1">{event.status}</span>
                  {event.estimated_seconds ? (
                    <span className="rounded-md bg-white px-2 py-1">{event.estimated_seconds}s estimate</span>
                  ) : null}
                  {event.requires_action ? (
                    <span className="rounded-md bg-amber-50 px-2 py-1 text-amber-700">action required</span>
                  ) : null}
                </div>
                <details className="mt-3">
                  <summary className="cursor-pointer text-xs font-semibold text-copper">Tool payload</summary>
                  <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-white p-3 text-xs leading-5 text-ink/70">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                </details>
              </div>
            ))}
          </div>
        </Panel> : null}
        {isDebug ? <Panel title="Research Trace" icon={<Braces size={18} />}>
          <div className="space-y-3">
            {liveRun.trace_events.map((event) => (
              <details key={`${event.phase}-${event.step}`} className="rounded-md border border-line bg-paper/60 p-3">
                <summary className="cursor-pointer text-sm font-semibold">
                  {event.phase} / {event.step} · {event.decision ?? event.status}
                </summary>
                <p className="mt-2 text-xs leading-5 text-ink/65">{event.output_summary}</p>
                {event.rejection_reason ? (
                  <p className="mt-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800">{event.rejection_reason}</p>
                ) : null}
                <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-white p-3 text-xs leading-5 text-ink/70">
                  {JSON.stringify(event, null, 2)}
                </pre>
              </details>
            ))}
          </div>
        </Panel> : null}
        <Panel title="Elastic Evidence Trail" icon={<Search size={18} />}>
          <div className="space-y-4">
            {liveRun.evidence.map((item) => (
              <article key={item.evidence_id} className="rounded-md border border-line bg-white p-4 shadow-insetLine">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <Badge tone="night">{item.match_type}</Badge>
                  <span className="text-xs text-ink/60">{item.passage_id}</span>
                </div>
                <div className="mb-3 rounded-md border border-line bg-mist px-3 py-2 text-xs leading-5 text-ink/70">
                  <div className="font-semibold text-sage">{item.location_label ?? `${item.work_title ?? item.work_id} · ${item.author_name ?? ""}`}</div>
                  <div className="mt-1 safe-text">
                    source: {item.source_title ?? item.source_id ?? "unknown"} · page/ref: {item.page_ref ?? item.source_page ?? "not recorded"}
                  </div>
                </div>
                <EvidenceQuote quote={item.quote} />
                {item.translation_hint ? (
                  <p className="mt-2 text-sm leading-6 text-ink/70">{item.translation_hint}</p>
                ) : null}
                <div className="mb-3 mt-3 grid gap-2 text-xs text-ink/60 sm:grid-cols-3">
                  <span className="rounded-md bg-paper px-2 py-1">backend: {item.retrieval_backend}</span>
                  <span className="rounded-md bg-paper px-2 py-1">
                    index: {item.elastic_index ?? "not used"}
                  </span>
                  <span className="rounded-md bg-paper px-2 py-1">
                    elastic score: {item.elastic_score ?? "n/a"}
                  </span>
                  <span className="rounded-md bg-paper px-2 py-1">verification: {verificationLabel(item)}</span>
                  <span className="rounded-md bg-paper px-2 py-1">anchor: {anchorRange(item)}</span>
                  <span className="rounded-md bg-paper px-2 py-1">locator: {item.source_locator_kind ?? "passage_id"}</span>
                </div>
                <div className="mb-3 flex flex-wrap gap-2">
                  {item.citation_hint ? (
                    <ActionButton icon={<Clipboard size={15} />} onClick={() => copyText(item.citation_hint ?? "", "Citation copied.")}>
                      Copy citation
                    </ActionButton>
                  ) : null}
                  {item.source_page_url ? (
                    <ActionLink icon={<ExternalLink size={15} />} href={item.source_page_url}>
                      Open source page
                    </ActionLink>
                  ) : null}
                  {item.source_id ? (
                    <a className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink shadow-insetLine hover:border-sage" href={`/sources/${encodeURIComponent(item.source_id)}`}>
                      <FileText size={15} />
                      OCR editor
                    </a>
                  ) : null}
                  <ActionButton icon={<Search size={15} />} onClick={() => loadContext(item.passage_id)}>
                    View neighbors
                  </ActionButton>
                </div>
                <div className="mb-3 rounded-md border border-line bg-paper px-3 py-2 text-xs leading-5 text-ink/70">
                  <div className="font-semibold text-sage">Claim guardrail</div>
                  This candidate can appear in the report only because an Elastic evidence row exists for{" "}
                  <span className="font-semibold">{item.passage_id}</span>.
                </div>
                {(item.anchor_text_before || item.anchor_text_after) ? (
                  <div className="mb-3 rounded-md border border-line bg-paper px-3 py-2 text-xs leading-5 text-ink/70">
                    <div className="font-semibold text-sage">Quote anchor</div>
                    <div className="mt-1 safe-text">{item.anchor_text_before ? `...${item.anchor_text_before}` : ""}<span className="font-semibold text-copper"> [quote] </span>{item.anchor_text_after ? `${item.anchor_text_after}...` : ""}</div>
                  </div>
                ) : null}
                {contextByPassage[item.passage_id]?.length ? (
                  <div className="mt-3 space-y-2 rounded-md border border-line bg-paper p-3">
                    <div className="text-xs font-semibold text-sage">Neighboring passages in the same source</div>
                    {contextByPassage[item.passage_id].map((neighbor) => (
                      <div key={String(neighbor.passage_id)} className="rounded-md bg-white p-2 text-xs leading-5">
                        <div className="mb-1 font-semibold text-ink/65">{String(neighbor.location_label ?? neighbor.passage_id)}</div>
                        <div className="arabic text-base leading-8">{String(neighbor.text_raw ?? "")}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <Score label="Lexical" value={item.lexical_score} />
                  <Score label="Semantic" value={item.semantic_score} />
                  <Score label="Metadata" value={item.metadata_score} />
                  <Score label="Source" value={item.source_quality_score} />
                  <Score label="Relationship" value={item.relationship_fit_score} />
                </div>
                <p className="mt-3 text-sm leading-6 text-ink/70">{item.explanation}</p>
                {isDebug ? (
                  <>
                    <details className="mt-3">
                      <summary className="cursor-pointer text-xs font-semibold text-copper">Evidence tool trace</summary>
                      <pre className="mt-2 max-h-60 overflow-auto rounded-md bg-ink p-3 text-xs leading-5 text-paper">
                        {JSON.stringify(item.tool_trace, null, 2)}
                      </pre>
                    </details>
                    <details className="mt-3">
                      <summary className="cursor-pointer text-xs font-semibold text-copper">Model routing trace</summary>
                      <pre className="mt-2 max-h-52 overflow-auto rounded-md bg-white p-3 text-xs leading-5 text-ink/70">
                        {JSON.stringify(item.model_trace, null, 2)}
                      </pre>
                    </details>
                  </>
                ) : null}
              </article>
            ))}
          </div>
        </Panel>
      </section>

      <section className="min-w-0 space-y-5 xl:col-span-2 xl:grid xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] xl:gap-5 xl:space-y-0">
        <Panel title="Ranked Candidates" icon={<CheckCircle2 size={18} />}>
          <div className="space-y-4">
            {liveRun.candidates.map((candidate) => (
              <div key={candidate.work_id} className="rounded-md border border-line p-3">
                <div className="text-sm text-ink/60">{candidate.author}</div>
                <div className="mt-1 font-semibold">{candidate.work_title}</div>
                <div className="mt-3">
                  <ConfidenceBar value={candidate.confidence} />
                </div>
                <p className="mt-3 text-sm leading-6 text-ink/70">{candidate.why}</p>
              </div>
            ))}
          </div>
        </Panel>
        {isDebug ? <Panel title="Elastic Queries" icon={<Braces size={18} />}>
          <div className="mb-3 rounded-md border border-line bg-paper p-3 text-xs leading-5 text-ink/70">
            Agent Builder tool mirror: <span className="font-semibold">hermeneut.passage_lookup</span>{" "}
            runs ES|QL over <span className="font-semibold">hermeneut_passages</span>. Backend runs add
            lexical, semantic, hybrid, and metadata retrieval traces for the web evidence view.
          </div>
          <div className="space-y-3">
            {liveRun.search_plan.map((item, index) => (
              <div key={`${item.type}-${index}`} className="rounded-md bg-ink p-3 text-paper">
                <div className="mb-2 text-xs uppercase text-bronze">{item.type}</div>
                <div className="break-words text-sm">{item.query}</div>
                <p className="mt-2 text-xs leading-5 text-paper/70">{item.purpose}</p>
              </div>
            ))}
          </div>
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Candidate Web Searches" icon={<Search size={18} />}>
          <div className="space-y-3">
            {liveRun.candidate_web_searches.slice(0, 10).map((searchItem, index) => (
              <details key={`${String(searchItem.query)}-${index}`} className="rounded-md border border-line bg-paper/60 p-3">
                <summary className="cursor-pointer text-sm font-semibold">
                  {String(searchItem.decision ?? "candidate search")} · {String(searchItem.hit_count ?? 0)} hit(s) ·{" "}
                  {String(searchItem.resolver_result_count ?? 0)} resolver lead(s)
                </summary>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-ink/60">
                  <span className="rounded-md bg-white px-2 py-1">
                    status: {String(searchItem.execution_status ?? "not executed")}
                  </span>
                  <span className="rounded-md bg-white px-2 py-1">
                    targets: {Array.isArray(searchItem.executed_targets) ? searchItem.executed_targets.join(", ") : "none"}
                  </span>
                </div>
                <div className="mt-2 break-words text-xs text-ink/70">{String(searchItem.query)}</div>
                <pre className="mt-2 max-h-52 overflow-auto rounded-md bg-white p-3 text-xs leading-5 text-ink/70">
                  {JSON.stringify(searchItem, null, 2)}
                </pre>
              </details>
            ))}
          </div>
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Web Hit Critic Decisions" icon={<Search size={18} />}>
          <p className="mb-3 text-sm leading-6 text-ink/70">
            Web hits are reviewed as candidate intelligence only; none count as final textual evidence.
          </p>
          <div className="space-y-3">
            {webHitAssessments.length ? webHitAssessments.map((assessment, index) => (
              <div key={`${String(assessment.url ?? "hit")}-${index}`} className="rounded-md border border-line p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="rounded-md bg-paper px-2 py-1 text-xs font-semibold">{String(assessment.decision ?? "unassessed")}</span>
                  <span className="text-xs text-ink/55">{String(assessment.source_quality ?? "unknown quality")}</span>
                </div>
                <a className="mt-2 block break-words text-xs text-copper" href={String(assessment.url ?? "#")} target="_blank">
                  {String(assessment.url ?? "no url")}
                </a>
                <p className="mt-2 text-xs leading-5 text-ink/65">{String(assessment.reason ?? "No reason recorded.")}</p>
                <div className="mt-2 text-xs text-sage">candidate: {String(assessment.related_candidate ?? "unknown")}</div>
              </div>
            )) : <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">No web hit critic decisions were returned.</p>}
          </div>
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Source Selection Judge" icon={<Network size={18} />}>
          <p className="mb-3 text-sm leading-6 text-ink/70">
            Source selection weighs metadata match, source quality, file availability, license status, and OCR eligibility.
          </p>
          <pre className="max-h-72 overflow-auto rounded-md bg-paper p-3 text-xs leading-5 text-ink/70">
            {JSON.stringify(sourceSelection, null, 2)}
          </pre>
        </Panel> : null}
        {liveRun.mode === "open_discovery" ? <Panel title="Role-Aware PDF/OCR Targets" icon={<FileText size={18} />}>
          <p className="mb-3 text-sm leading-6 text-ink/70">
            Open Discovery now balances containing-layer sources with citation-chain sources. They still do not count as evidence until searchable Elastic passages are retrieved.
          </p>
          <div className="space-y-3">
            {liveRun.top_pdf_targets.length ? liveRun.top_pdf_targets.map((target) => (
              <div key={String(target.source_id)} className="safe-text rounded-md border border-line p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="safe-text min-w-0 flex-1 font-semibold">{String(target.title ?? target.source_id)}</div>
                  <span className="rounded-md bg-paper px-2 py-1 text-xs">{sourceRoleLabel(String(target.source_role ?? "citation_chain"))}</span>
                </div>
                <a className="safe-link mt-1 block text-xs text-copper" href={String(target.download_url ?? target.url ?? "#")} target="_blank">
                  {String(target.download_url ?? target.url ?? "no direct URL")}
                </a>
                <p className="mt-2 safe-text text-xs leading-5 text-ink/65">{String(target.selection_reason ?? target.relationship_reason ?? "")}</p>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-ink/60">
                  <span className="rounded-md bg-paper px-2 py-1">file: {String(target.file_type ?? "unknown")}</span>
                  <span className="rounded-md bg-paper px-2 py-1">policy: {String(target.download_policy ?? "review")}</span>
                  <span className="rounded-md bg-paper px-2 py-1">quota: {String(target.quota_reason ?? target.selection_bucket ?? "selected")}</span>
                </div>
              </div>
            )) : <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">No direct PDF/text target passed the current download policy.</p>}
          </div>
        </Panel> : null}
        {liveRun.mode === "open_discovery" ? <Panel title="Where The Phrase May Be Found" icon={<Search size={18} />}>
          <SourceRoleSection
            emptyText="No containing-layer source was resolved directly. The unused quota can be transferred, and the run should explain why."
            sources={containingSources}
            onAction={submitAction}
            canAct={juryStatus.operator_enabled}
          />
        </Panel> : null}
        {liveRun.mode === "open_discovery" ? <Panel title="Possible Citation/Source Chain" icon={<Network size={18} />}>
          <SourceRoleSection
            emptyText="No citation-chain source was resolved directly."
            sources={citationSources}
            onAction={submitAction}
            canAct={juryStatus.operator_enabled}
          />
          {!liveRun.evidence.length && searchableOpenSources.length ? (
            <NoEvidenceSummary processed={processedOpenSources.length} searchable={searchableOpenSources.length} weakOcr={weakOcrSources.length} blockedReason={liveRun.blocked_reason} />
          ) : null}
        </Panel> : null}
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="OCR Progress" icon={<Activity size={18} />}>
          <div className="space-y-2">
            {liveRun.ocr_jobs.map((job) => (
              <div key={String(job.source_id)} className="rounded-md bg-paper p-3 text-xs leading-5 text-ink/70">
                <div className="font-semibold text-sage">{String(job.status)}</div>
                <div>{String(job.title ?? job.source_id)}</div>
                <div className="mt-1">engine: {String(job.ocr_engine)} · mode: {String(job.ocr_mode)}</div>
              </div>
            ))}
          </div>
        </Panel> : null}
        <Panel title="Source Candidates" icon={<Network size={18} />}>
          <p className="mb-3 text-sm leading-6 text-ink/70">
            Suggested source objects can be approved into the PDF vault before OCR or searchable indexing.
          </p>
          <div className="mb-4 space-y-3">
            {liveRun.source_lifecycle_records.map((source) => (
              <SourceLifecycleCard
                key={String(source.source_id)}
                source={source}
                onAction={submitAction}
                canAct={juryStatus.operator_enabled}
              />
            ))}
          </div>
          {isDebug ? <pre className="max-h-72 overflow-auto rounded-md bg-paper p-3 text-xs leading-5 text-ink/70">
            {JSON.stringify(sourceCandidates ?? {}, null, 2)}
          </pre> : null}
        </Panel>
        {isDebug && liveRun.mode === "open_discovery" ? <Panel title="Rejected Candidates" icon={<Braces size={18} />}>
          <pre className="max-h-72 overflow-auto rounded-md bg-paper p-3 text-xs leading-5 text-ink/70">
            {JSON.stringify(liveRun.rejected_candidates ?? [], null, 2)}
          </pre>
        </Panel> : null}
        <Panel title="Scholarly Report" icon={<FileText size={18} />}>
          <div className={`mb-3 inline-flex rounded-md px-2 py-1 text-xs font-semibold ${tierClass(liveRun.decision_tier)}`}>
            Evidence tier: {liveRun.decision_tier}
          </div>
          <p className="text-sm leading-6 text-ink/75">{liveRun.final_report}</p>
        </Panel>
      </section>
    </main>
  );
}

function Panel({title, icon, children}: {title: string; icon: React.ReactNode; children: React.ReactNode}) {
  return (
    <div className="safe-text overflow-hidden rounded-md border border-line/90 bg-white/95 p-4 shadow-soft sm:p-5">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-sage">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

function Badge({label, value, tone = "paper", children}: {label?: string; value?: unknown; tone?: "paper" | "night" | "success" | "warn"; children?: React.ReactNode}) {
  if (!children && (value === undefined || value === null || value === "")) return null;
  const classes =
    tone === "night"
      ? "bg-night text-white"
      : tone === "success"
        ? "bg-sage text-white"
        : tone === "warn"
          ? "bg-amber-50 text-amber-800"
          : "bg-paper text-ink/70";
  const content = children ?? (label ? `${label}: ${String(value)}` : String(value));
  return (
    <span className={`safe-text inline-flex items-center rounded-md px-2 py-1 text-xs font-semibold ${classes}`}>
      {content}
    </span>
  );
}

function ActionButton({icon, children, onClick}: {icon: React.ReactNode; children: React.ReactNode; onClick: () => void}) {
  return (
    <button className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink shadow-insetLine hover:border-sage" onClick={onClick}>
      {icon}
      {children}
    </button>
  );
}

function ActionLink({icon, children, href}: {icon: React.ReactNode; children: React.ReactNode; href: string}) {
  return (
    <a className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink shadow-insetLine hover:border-sage" href={href} target="_blank">
      {icon}
      {children}
    </a>
  );
}

function EvidenceQuote({quote}: {quote: string}) {
  return (
    <blockquote className="evidence-quote max-h-[360px] overflow-auto rounded-md border border-line bg-white p-4 text-xl leading-10 shadow-insetLine">
      {quote}
    </blockquote>
  );
}

function Metric({label, value}: {label: string; value: string}) {
  return (
    <div className="flex min-w-0 justify-between gap-3 border-b border-line pb-2">
      <dt className="text-ink/55">{label}</dt>
      <dd className="safe-text text-right font-medium">{value}</dd>
    </div>
  );
}

function Score({label, value}: {label: string; value: number}) {
  return (
    <div className="rounded-md bg-paper px-3 py-2">
      <div className="text-xs text-ink/55">{label}</div>
      <div className="mt-1 text-sm font-semibold text-umber">{Math.round(value * 100)}%</div>
    </div>
  );
}

function CheckItem({label, ok}: {label: string; ok: boolean}) {
  return (
    <div className={ok ? "flex items-center gap-2 rounded-md bg-sage px-3 py-2 text-xs font-semibold text-white" : "flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800"}>
      {ok ? <Check size={14} /> : <AlertTriangle size={14} />}
      <span>{label}: {ok ? "verified" : "needs review"}</span>
    </div>
  );
}

function answerReason(candidate: AgentRun["candidates"][number] | undefined, evidence: EvidenceItem) {
  const parts = [
    evidence.match_type ? `${evidence.match_type} retrieval found an indexed passage` : "Indexed evidence was retrieved",
    evidence.location_label ? `with source location ${evidence.location_label}` : "with source metadata",
    candidate?.confidence ? `and candidate confidence ${Math.round(candidate.confidence * 100)}%` : null,
  ].filter(Boolean);
  return `${parts.join(", ")}. Human verification should still compare the citation against the source page.`;
}

function candidateWeakness(items: EvidenceItem[]) {
  if (!items.length) return "No indexed quote retrieved for this candidate.";
  const best = items[0];
  if (best.verification_status === "unanchored_quote") return "Quote text was retrieved but not offset-anchored in the passage.";
  if (!best.page_ref && !best.source_page) return "Page/ref still needs verification.";
  if ((best.relationship_fit_score ?? 0) < 0.25) return "Relationship support is weak or contextual.";
  if ((best.ocr_confidence ?? 1) < 0.72) return "OCR confidence is low; verify against image/PDF.";
  return "Main residual risk is human source-page verification.";
}

function verificationLabel(evidence: EvidenceItem) {
  if (evidence.verification_status === "anchored_quote") return "anchored quote";
  if (evidence.verification_status === "anchored_passage_only") return "anchored passage";
  if (evidence.verification_status === "unanchored_quote") return "unanchored quote";
  return evidence.verification_status ?? "unverified";
}

function anchorRange(evidence: EvidenceItem) {
  if (typeof evidence.quote_start_char === "number" && typeof evidence.quote_end_char === "number") {
    return `${evidence.quote_start_char}-${evidence.quote_end_char}`;
  }
  return "not anchored";
}

function researchPacket(run: AgentRun, evidence: EvidenceItem) {
  const candidate = run.candidates[0];
  const comparison = run.candidates.slice(0, 3).map((item) => {
    const hits = run.evidence.filter((evidenceItem) => evidenceItem.work_id === item.work_id);
    return `- ${item.author} - ${item.work_title}: ${Math.round(item.confidence * 100)}%, ${hits.length} evidence hit(s)`;
  }).join("\n");
  return [
    `Hermeneut research packet`,
    `Run: ${run.run_id}`,
    `Decision tier: ${run.decision_tier}`,
    `Candidate: ${candidate?.author ?? evidence.author_name ?? "unknown"} - ${candidate?.work_title ?? evidence.work_title ?? evidence.work_id}`,
    `Location: ${evidence.location_label ?? evidence.passage_id}`,
    `Citation: ${evidence.citation_hint ?? "not available"}`,
    `Confidence: ${Math.round(evidence.confidence * 100)}%`,
    `Verification: ${verificationLabel(evidence)} (${anchorRange(evidence)})`,
    `Source role: ${evidence.source_role ?? "not recorded"}`,
    `OCR quality: ${ocrQualityLabel(evidence.ocr_quality_status)}`,
    `Source resolution query: ${evidence.source_resolution_query ?? "not recorded"}`,
    `Locator: ${evidence.source_locator_kind ?? "passage_id"} / page-ref ${evidence.page_ref ?? evidence.source_page ?? "not recorded"}`,
    `Anchor before: ${evidence.anchor_text_before ?? ""}`,
    `Anchor after: ${evidence.anchor_text_after ?? ""}`,
    `Scores: lexical ${evidence.lexical_score}, semantic ${evidence.semantic_score}, metadata ${evidence.metadata_score}, source ${evidence.source_quality_score}, relationship ${evidence.relationship_fit_score}`,
    `Quote: ${evidence.quote}`,
    `Verification checklist: exact quote ${evidence.quote ? "yes" : "no"}; location ${evidence.location_label ? "yes" : "no"}; anchor ${evidence.verification_status?.startsWith("anchored") ? "yes" : "needs review"}; metadata ${evidence.work_title && evidence.author_name ? "yes" : "no"}; relationship ${evidence.relationship_fit_score > 0.25 ? "yes" : "needs review"}`,
    `Candidate comparison:`,
    comparison || "- No ranked candidates returned.",
    `Human verification: compare the quoted passage against the cited source/page before final publication.`,
  ].join("\n");
}

function sourceRoleLabel(role: string) {
  if (role === "containing_layer") return "where phrase may be found";
  if (role === "parallel_witness") return "parallel witness";
  return "possible citation chain";
}

function ocrQualityLabel(status?: string) {
  if (!status) return "not recorded";
  if (status === "strong_text_layer_or_ocr") return "Strong OCR/text layer";
  if (status === "usable_but_needs_review") return "Usable, review needed";
  if (status === "weak_ocr_needs_manual_review") return "Weak OCR, manual review needed";
  if (status === "human_corrected") return "Human corrected";
  return status.replaceAll("_", " ");
}

function isActiveSourceStatus(status: string) {
  return ["download_approved", "raw_stored", "ocr_running", "indexing", "ocr_partial"].includes(status);
}

function isSelectedSourceStatus(status: string) {
  return ["selected", "download_candidate", "download_approved", "raw_stored", "ocr_running", "indexing", "ocr_partial"].includes(status);
}

function SourceRoleSection({
  sources,
  emptyText,
  onAction,
  canAct,
}: {
  sources: Record<string, unknown>[];
  emptyText: string;
  onAction: (
    action: "approve_download" | "reject_source" | "continue_without_source" | "retry_ocr",
    sourceId?: string,
  ) => void;
  canAct: boolean;
}) {
  if (!sources.length) {
    return <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">{emptyText}</p>;
  }
  return (
    <div className="space-y-3">
      {sources.map((source) => (
        <SourceLifecycleCard key={String(source.source_id)} source={source} onAction={onAction} canAct={canAct} compact />
      ))}
    </div>
  );
}

function NoEvidenceSummary({processed, searchable, weakOcr, blockedReason}: {processed: number; searchable: number; weakOcr: number; blockedReason?: string}) {
  const detail = blockedReason || (weakOcr ? "Some processed sources had weak OCR quality; textual evidence did not pass the claim threshold." : "Processed sources were searchable, but no matching textual evidence was retrieved.");
  return (
    <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
      <div className="flex items-center gap-2 font-semibold">
        <AlertTriangle size={16} />
        Processed sources, no claim-worthy textual match
      </div>
      <div className="mt-2 grid gap-2 text-xs sm:grid-cols-3">
        <span className="rounded-md bg-white px-2 py-1">processed: {processed}</span>
        <span className="rounded-md bg-white px-2 py-1">searchable: {searchable}</span>
        <span className="rounded-md bg-white px-2 py-1">weak OCR: {weakOcr}</span>
      </div>
      <p className="mt-2 leading-6">{detail}</p>
    </div>
  );
}

function StatusDot({status}: {status: string}) {
  const color =
    status === "completed"
      ? "bg-sage"
      : status === "running"
        ? "bg-copper"
        : status === "failed"
          ? "bg-red-600"
          : status === "waiting_for_approval" || status === "waiting_source"
            ? "bg-amber-500"
            : "bg-ink/25";
  return <span className={`h-2.5 w-2.5 rounded-full ${color}`} />;
}

function tierClass(tier: string) {
  if (tier === "confirmed") return "bg-sage text-white";
  if (tier === "probable") return "bg-umber text-paper";
  if (tier === "strong_lead") return "bg-copper/15 text-copper";
  if (tier === "weak_lead") return "bg-amber-50 text-amber-800";
  return "bg-paper text-ink/65";
}

function SourceLifecycleCard({
  source,
  onAction,
  canAct,
  compact = false,
}: {
  source: Record<string, unknown>;
  onAction: (
    action: "approve_download" | "reject_source" | "continue_without_source" | "retry_ocr",
    sourceId?: string,
  ) => void;
  canAct: boolean;
  compact?: boolean;
}) {
  const sourceId = String(source.source_id ?? "");
  const status = String(source.lifecycle_status ?? "web_discovered");
  const countsAsEvidence = Boolean(source.counts_as_evidence);
  const ocrQuality = String(source.ocr_quality_status ?? "not processed");
  const weakOcr = ocrQuality.includes("weak");
  const needsApproval = status === "download_candidate";
  const canRetryOcr = status === "raw_stored" || status === "ocr_failed";
  const canSkip = status === "requires_human_review" || status === "download_candidate";
  const hasAdminToken = canAct || (typeof window !== "undefined" && Boolean(window.sessionStorage.getItem("hermeneut_admin_token")));
  const indexedCount = Number(source.indexed_passage_count ?? 0);
  const libraryId = String(source.library_id ?? "demo_kalam");
  const title = String(source.title ?? sourceId);
  return (
    <article className="safe-text rounded-md border border-line bg-white p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold text-sage">{String(source.provider ?? "source")}</div>
          <div className="mt-1 safe-text font-semibold">{String(source.title ?? sourceId)}</div>
          <a className="safe-link mt-1 block text-xs text-copper" href={String(source.source_page_url ?? source.url ?? "#")} target="_blank">
            {String(source.source_page_url ?? source.url ?? "no url")}
          </a>
        </div>
        <div className="flex flex-wrap gap-2">
          <SourceStatusPill status={status} />
          <Badge value={sourceRoleLabel(String(source.source_role ?? "citation_chain"))} />
          <span className={countsAsEvidence ? "rounded-md bg-sage px-2 py-1 text-xs text-white" : "rounded-md bg-paper px-2 py-1 text-xs"}>
            {countsAsEvidence ? "counts as evidence" : "not evidence yet"}
          </span>
          {weakOcr ? <span className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800">weak OCR</span> : null}
          {source.ocr_quality_status ? <span className="rounded-md bg-paper px-2 py-1 text-xs">{ocrQualityLabel(String(source.ocr_quality_status))}</span> : null}
        </div>
      </div>
      <p className="mt-2 text-xs leading-5 text-ink/65">{String(source.relationship_reason ?? "Candidate source.")}</p>
      {source.failure_reason_public ? (
        <p className="mt-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800">
          {String(source.failure_reason_public)}
        </p>
      ) : null}
      <div className="mt-3 grid gap-2 text-xs text-ink/65 sm:grid-cols-5">
        <PipelineStep label="Selected" active={Boolean(sourceId)} />
        <PipelineStep label="Downloading" active={["download_approved", "raw_stored", "ocr_running", "indexing", "searchable"].includes(status)} />
        <PipelineStep label="Raw stored" active={["raw_stored", "ocr_running", "indexing", "searchable"].includes(status)} />
        <PipelineStep label="OCR/index" active={["ocr_running", "indexing", "searchable"].includes(status) || indexedCount > 0} attention={canRetryOcr} />
        <PipelineStep label={indexedCount > 0 && !countsAsEvidence ? "No match" : "Searchable"} active={countsAsEvidence || indexedCount > 0} />
      </div>
      {!compact && sourceId ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <a
            className="inline-flex min-h-9 items-center rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink"
            href={`/sources/${encodeURIComponent(sourceId)}`}
          >
            Open source workspace
          </a>
          {indexedCount > 0 ? (
            <a
              className="inline-flex min-h-9 items-center rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink"
              href={`/?passage=${encodeURIComponent(title)}&library_id=${encodeURIComponent(libraryId)}`}
            >
              Search this library
            </a>
          ) : null}
        </div>
      ) : null}
      <div className="mt-3 grid gap-2 text-xs text-ink/60">
        <span className="safe-text rounded-md bg-paper px-2 py-1">file: {String(source.file_type ?? "unknown")}</span>
        <span className="safe-text rounded-md bg-paper px-2 py-1">rank: {String(source.source_candidate_rank ?? "n/a")}</span>
        <span className="safe-text rounded-md bg-paper px-2 py-1">resolution: {String(source.source_resolution_query ?? "not recorded")}</span>
        <span className="safe-text rounded-md bg-paper px-2 py-1">
          OCR status: {String(source.ocr_status ?? "pending")} · passages: {String(source.indexed_passage_count ?? 0)}
        </span>
        <span className={weakOcr ? "safe-text rounded-md bg-amber-50 px-2 py-1 text-amber-800" : "safe-text rounded-md bg-paper px-2 py-1"}>
          OCR quality: {ocrQuality} · avg confidence: {String(source.ocr_avg_confidence ?? "n/a")}
        </span>
      </div>
      {needsApproval && hasAdminToken ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="rounded-md bg-umber px-3 py-2 text-xs font-semibold text-paper" onClick={() => onAction("approve_download", sourceId)}>
            Approve download
          </button>
          <button className="rounded-md border border-line bg-white px-3 py-2 text-xs font-semibold" onClick={() => onAction("reject_source", sourceId)}>
            Reject
          </button>
          <button className="rounded-md border border-line bg-white px-3 py-2 text-xs font-semibold" onClick={() => onAction("continue_without_source", sourceId)}>
            Continue without
          </button>
        </div>
      ) : null}
      {!needsApproval && hasAdminToken && (canRetryOcr || canSkip) ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {canRetryOcr ? (
            <button className="rounded-md bg-umber px-3 py-2 text-xs font-semibold text-paper" onClick={() => onAction("retry_ocr", sourceId)}>
              Retry OCR
            </button>
          ) : null}
          {canSkip ? (
            <button className="rounded-md border border-line bg-white px-3 py-2 text-xs font-semibold" onClick={() => onAction("continue_without_source", sourceId)}>
              Continue without
            </button>
          ) : null}
        </div>
      ) : null}
      {!hasAdminToken && (needsApproval || canRetryOcr || canSkip) ? (
        <p className="mt-3 rounded-md bg-paper px-3 py-2 text-xs text-ink/65">Jury access is required for source processing actions.</p>
      ) : null}
    </article>
  );
}

function SourceStatusPill({status}: {status: string}) {
  const tone = status === "searchable" ? "success" : status.includes("fail") || status === "rejected" ? "warn" : status === "ocr_running" || status === "indexing" ? "night" : "paper";
  return <Badge tone={tone}>{status}</Badge>;
}

function PipelineStep({label, active, attention = false}: {label: string; active: boolean; attention?: boolean}) {
  const classes = attention
    ? "bg-amber-50 text-amber-800"
    : active
      ? "bg-sage text-white"
      : "bg-paper text-ink/55";
  return <span className={`rounded-md px-2 py-1 text-center font-semibold ${classes}`}>{label}</span>;
}

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
