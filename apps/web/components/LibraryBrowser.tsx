"use client";

import {useEffect, useMemo, useState} from "react";
import {
  ArrowRight,
  BookCopy,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clipboard,
  ExternalLink,
  FilePenLine,
  FileText,
  GitBranch,
  Library,
  Loader2,
  Lock,
  Inbox,
  ShieldCheck,
  Sparkles,
  Play,
  Plus,
  Search,
  Upload,
  X,
} from "lucide-react";
import {addLibrarySource, analyzeCatalogLibrary, analyzeLibraryRelationships, decideCatalogProposal, getCatalogHealth, getCatalogInbox, getJuryStatus, searchLibrary} from "@/lib/api";
import type {CatalogHealthSummary, CatalogInboxResponse, CatalogProposal, LibrarySearchResponse, LibrarySummary, RelationshipLineage, SourceWitness, WorkRecord} from "@/lib/types";

type UploadKind = "new_work" | "existing_work_source";
type LibraryView = "works" | "relationships" | "inbox" | "health";

export function LibraryBrowser() {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<LibrarySearchResponse>({});
  const [selectedLibrary, setSelectedLibrary] = useState("");
  const [selectedWorkId, setSelectedWorkId] = useState("");
  const [layerFilter, setLayerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [hasAdminToken, setHasAdminToken] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [view, setView] = useState<LibraryView>("works");
  const [inbox, setInbox] = useState<CatalogInboxResponse | null>(null);
  const [health, setHealth] = useState<CatalogHealthSummary | null>(null);
  const [curatorLoading, setCuratorLoading] = useState(false);
  const [curatorMessage, setCuratorMessage] = useState("");

  useEffect(() => {
    if (window.sessionStorage.getItem("hermeneut_admin_token")) {
      setHasAdminToken(true);
    }
    getJuryStatus()
      .then((h) => {
        if (h?.operator_enabled) setHasAdminToken(true);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(() => {
      if (query.trim() && !hasAdminToken) {
        setLoading(false);
        return;
      }
      searchLibrary(query)
        .then((result) => {
          if (cancelled) return;
          setData(result);
          setSelectedLibrary((current) => current || preferredLibrary(result.libraries ?? []));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [hasAdminToken, query]);

  const catalogWorks = useMemo(() => buildWorkRecords(data.works ?? [], data.sources ?? [], data.edges ?? []), [data.edges, data.sources, data.works]);
  const works = useMemo(
    () => catalogWorks.filter((work) => (
      (!selectedLibrary || work.library_id === selectedLibrary)
      && (!layerFilter || normalizedLayer(work.layer_type) === layerFilter)
      && (!statusFilter
        || (statusFilter === "searchable" && Number(work.searchable_source_count ?? 0) > 0)
        || (statusFilter === "needs metadata review" && work.catalog_review_status === "needs_review")
        || (statusFilter === "low ocr quality" && work.ocr_status_summary === "low_quality"))
    )),
    [catalogWorks, layerFilter, selectedLibrary, statusFilter],
  );
  const sources = useMemo(
    () => (data.sources ?? []).filter((source) => !selectedLibrary || source.library_id === selectedLibrary),
    [data.sources, selectedLibrary],
  );
  const selectedWork = catalogWorks.find((work) => work.work_id === selectedWorkId);
  const selectedSources = sources.filter((source) => source.work_id === selectedWorkId);
  const passages = (data.passages ?? []).filter((passage) => !selectedLibrary || String(passage.library_id ?? "") === selectedLibrary);
  const edges = (data.edges ?? []).filter((edge) => !selectedLibrary || edge.library_id === selectedLibrary);
  const readOnly = Boolean(data.meta?.read_only);
  const verifiedWorks = works.filter((work) => work.catalog_review_status !== "needs_review");
  const reviewWorks = works.filter((work) => work.catalog_review_status === "needs_review");

  useEffect(() => {
    if (!hasAdminToken || !selectedLibrary) return;
    if (view === "inbox" || view === "relationships") getCatalogInbox(selectedLibrary).then(setInbox).catch((error) => setCuratorMessage(error instanceof Error ? error.message : "Catalog Inbox unavailable."));
    if (view === "health") getCatalogHealth(selectedLibrary).then(setHealth).catch((error) => setCuratorMessage(error instanceof Error ? error.message : "Catalog Health unavailable."));
  }, [hasAdminToken, selectedLibrary, view]);

  return (
    <main className="mx-auto max-w-7xl px-4 py-7 sm:px-6">
      <BackendBanner meta={data.meta} />

      <section className="mt-5 border-y border-line bg-white/80 py-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="max-w-2xl">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase text-sage">
              <Library size={16} />
              Scholarly collections
            </div>
            <h1 className="mt-2 text-3xl font-semibold text-night">Library workspace</h1>
            <p className="mt-2 text-sm leading-6 text-ink/65">
              Browse independent works, inspect their witnesses, and follow commentary traditions without losing the source-level evidence.
            </p>
          </div>
          {hasAdminToken ? (
            <button
              className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-night px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45"
              disabled={readOnly}
              onClick={() => setShowUpload(true)}
              title={readOnly ? "Live Elastic is required for operator changes." : "Add a work or witness"}
              type="button"
            >
              {readOnly ? <Lock size={16} /> : <Plus size={16} />}
              Add to library
            </button>
          ) : null}
        </div>

        <div className="mt-5 flex min-h-12 items-center gap-3 rounded-md border border-line bg-white px-4 shadow-insetLine">
          {loading ? <Loader2 className="animate-spin text-sage" size={18} /> : <Search className="text-sage" size={18} />}
          <input
            className="w-full bg-transparent text-sm outline-none"
            disabled={!hasAdminToken}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={hasAdminToken ? "Search works, authors, witnesses, or Arabic passages" : "Jury access enables search; public preview shows the catalog dashboard."}
            value={query}
          />
        </div>
      </section>

      <LibraryRail
        libraries={data.libraries ?? []}
        selected={selectedLibrary}
        onSelect={(libraryId) => {
          setSelectedLibrary(libraryId);
          setSelectedWorkId("");
        }}
      />

      <section className="mt-6 flex flex-col gap-3 border-b border-line pb-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap gap-1 rounded-md border border-line bg-white p-1">
          <ViewTab active={view === "works"} icon={<BookOpen size={15} />} label="Works" onClick={() => setView("works")} />
          <ViewTab active={view === "relationships"} icon={<GitBranch size={15} />} label={`Relationships · ${edges.length}`} onClick={() => setView("relationships")} />
          <ViewTab active={view === "inbox"} icon={<Inbox size={15} />} label={`Catalog Inbox${inbox?.proposals.length ? ` · ${inbox.proposals.length}` : ""}`} onClick={() => setView("inbox")} />
          <ViewTab active={view === "health"} icon={<ShieldCheck size={15} />} label="Catalog Health" onClick={() => setView("health")} />
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="inline-flex items-center justify-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold text-night disabled:cursor-not-allowed disabled:opacity-45"
            disabled={readOnly || curatorLoading || !hasAdminToken}
            onClick={async () => {
              setCuratorLoading(true);
              setCuratorMessage("");
              try {
                await analyzeCatalogLibrary(selectedLibrary);
                setCuratorMessage("Catalog analysis completed. Review its proposals in Catalog Inbox.");
                setInbox(await getCatalogInbox(selectedLibrary));
                setView("inbox");
              } catch (error) {
                setCuratorMessage(error instanceof Error ? error.message : "Catalog analysis failed. Jury access is required for reanalysis.");
              } finally {
                setCuratorLoading(false);
              }
            }}
            title={!hasAdminToken ? "Jury access enables catalog analysis." : readOnly ? "Catalog analysis requires live Elastic." : "Reanalyze this collection"}
            type="button"
          >
            {curatorLoading ? <Loader2 className="animate-spin" size={15} /> : <Sparkles size={15} />}
            Analyze catalog
          </button>
          <button
            className="inline-flex items-center justify-center gap-2 rounded-md bg-night px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45"
            disabled={readOnly || curatorLoading || !hasAdminToken}
            onClick={async () => {
              setCuratorLoading(true);
              setCuratorMessage("");
              try {
                const result = await analyzeLibraryRelationships(selectedLibrary);
                const proposalCount = Number(result.relationship_proposal_count ?? 0);
                setCuratorMessage(`Relationship analysis completed. ${proposalCount} proposal(s) await review.`);
                setData(await searchLibrary(query));
                if (hasAdminToken) setInbox(await getCatalogInbox(selectedLibrary));
                setView("relationships");
              } catch (error) {
                setCuratorMessage(error instanceof Error ? error.message : "Relationship analysis failed.");
              } finally {
                setCuratorLoading(false);
              }
            }}
            title={!hasAdminToken ? "Jury access enables relationship reanalysis." : readOnly ? "Relationship analysis requires live Elastic." : "Reanalyze work/source relationships"}
            type="button"
          >
            {curatorLoading ? <Loader2 className="animate-spin" size={15} /> : <GitBranch size={15} />}
            Reanalyze relationships
          </button>
        </div>
      </section>
      {curatorMessage ? <div className="safe-text mt-3 rounded-md border border-line bg-white px-3 py-2 text-xs text-ink/65">{curatorMessage}</div> : null}

      {view === "relationships" ? (
        <RelationshipWorkspace
          works={catalogWorks.filter((work) => work.library_id === selectedLibrary)}
          edges={edges}
          backend={data.meta?.backend}
          hasAdminToken={hasAdminToken}
          readOnly={readOnly}
          proposals={(inbox?.proposals ?? []).filter((proposal) => proposal.proposal_type === "relationship")}
          onDecision={async (proposal, action) => {
            setCuratorLoading(true);
            try {
              await decideCatalogProposal(proposal.proposal_id, action);
              setInbox(await getCatalogInbox(selectedLibrary));
              setData(await searchLibrary(query));
            } catch (error) {
              setCuratorMessage(error instanceof Error ? error.message : "Decision failed.");
            } finally {
              setCuratorLoading(false);
            }
          }}
        />
      ) : view === "inbox" ? (
        <CatalogInbox
          data={inbox}
          readOnly={readOnly}
          hasAdminToken={hasAdminToken}
          onDecision={async (proposal, action) => {
            setCuratorLoading(true);
            try {
              await decideCatalogProposal(proposal.proposal_id, action);
              setInbox(await getCatalogInbox(selectedLibrary));
              setHealth(await getCatalogHealth(selectedLibrary));
            } catch (error) {
              setCuratorMessage(error instanceof Error ? error.message : "Decision failed.");
            } finally {
              setCuratorLoading(false);
            }
          }}
        />
      ) : view === "health" ? (
        <CatalogHealth health={health} hasAdminToken={hasAdminToken} />
      ) : <section className="mt-7 grid gap-7 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <div className="flex flex-col gap-3 border-b border-line pb-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase text-sage">Independent works</div>
              <h2 className="mt-1 text-2xl font-semibold text-night">{libraryTitle(data.libraries ?? [], selectedLibrary)}</h2>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Filter label="Layer" value={layerFilter} onChange={setLayerFilter} options={["matn", "sharh", "hashiya", "independent"]} />
              <Filter label="Status" value={statusFilter} onChange={setStatusFilter} options={["searchable", "needs metadata review", "low ocr quality"]} />
            </div>
          </div>

          {loading ? <LoadingState /> : verifiedWorks.length ? (
            <div className="mt-5 grid gap-4 md:grid-cols-2">
              {verifiedWorks.map((work) => (
                <WorkCard
                  key={work.work_id}
                  work={work}
                  witnesses={sources.filter((source) => source.work_id === work.work_id)}
                  selected={work.work_id === selectedWorkId}
                  onSelect={() => setSelectedWorkId(work.work_id)}
                />
              ))}
            </div>
          ) : !reviewWorks.length ? (
            <EmptyState title="No works match this view" detail="Change the collection or filters. Preview search only covers the latest Elastic backup." />
          ) : null}

          {reviewWorks.length ? (
            <details className="mt-7 rounded-md border border-amber-200 bg-amber-50/50 p-4" open={statusFilter === "needs metadata review"}>
              <summary className="cursor-pointer font-semibold text-night">Metadata Review · {reviewWorks.length} record(s)</summary>
              <p className="mt-2 text-sm text-ink/60">These records remain browsable, but their title, author, or catalog placement needs human review.</p>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                {reviewWorks.map((work) => <WorkCard key={work.work_id} work={work} witnesses={sources.filter((source) => source.work_id === work.work_id)} selected={work.work_id === selectedWorkId} onSelect={() => setSelectedWorkId(work.work_id)} />)}
              </div>
            </details>
          ) : null}

          {passages.length ? <PassageResults passages={passages} query={query} /> : null}
        </div>

        <aside className="min-w-0 border-l-0 border-line xl:border-l xl:pl-6">
          <CollectionSummary library={(data.libraries ?? []).find((library) => library.library_id === selectedLibrary)} />
          <LineagePanel works={catalogWorks.filter((work) => work.library_id === selectedLibrary)} edges={edges} backend={data.meta?.backend} />
        </aside>
      </section>}

      {selectedWork ? (
        <WorkDetail
          work={selectedWork}
          sources={selectedSources}
          proposals={(inbox?.proposals ?? []).filter((proposal) => proposal.work_id === selectedWork.work_id || selectedSources.some((source) => source.source_id === proposal.source_id))}
          readOnly={readOnly}
          onClose={() => setSelectedWorkId("")}
        />
      ) : null}
      {showUpload ? (
        <UploadDialog
          libraryId={selectedLibrary || "demo_kalam"}
          works={catalogWorks.filter((work) => work.library_id === selectedLibrary)}
          onClose={() => setShowUpload(false)}
          onComplete={() => {
            setShowUpload(false);
            searchLibrary(query).then(setData);
          }}
        />
      ) : null}
    </main>
  );
}

function ViewTab({active, icon, label, onClick}: {active: boolean; icon: React.ReactNode; label: string; onClick: () => void}) {
  return <button className={`inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-semibold ${active ? "bg-night text-white" : "text-ink/60 hover:bg-paper"}`} onClick={onClick} type="button">{icon}{label}</button>;
}

function CatalogInbox({data, readOnly, hasAdminToken, onDecision}: {data: CatalogInboxResponse | null; readOnly: boolean; hasAdminToken: boolean; onDecision: (proposal: CatalogProposal, action: "approve" | "reject") => void}) {
  if (!hasAdminToken) {
    return (
      <section className="mt-7 rounded-md border border-line bg-white p-5 shadow-soft">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase text-sage"><Inbox size={15} /> Human review queue</div>
        <h2 className="mt-1 text-2xl font-semibold text-night">Catalog Inbox</h2>
        <p className="mt-2 text-sm leading-6 text-ink/65">
          Catalog proposals and approvals open from the jury access link. Public visitors can browse the catalog but cannot mutate records.
        </p>
      </section>
    );
  }
  const proposals = data?.proposals ?? [];
  return (
    <section className="mt-7">
      <div className="flex flex-col gap-2 border-b border-line pb-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase text-sage"><Inbox size={15} /> Human review queue</div>
          <h2 className="mt-1 text-2xl font-semibold text-night">Catalog Inbox</h2>
          <p className="mt-2 text-sm text-ink/60">Gemini suggestions remain proposals until a curator approves them.</p>
        </div>
        <span className="rounded-md bg-paper px-3 py-2 text-xs font-semibold text-ink/60">{proposals.length} proposals</span>
      </div>
      {readOnly ? <div className="mt-4 flex items-center gap-2 border-l-4 border-copper bg-amber-50 px-4 py-3 text-sm text-copper"><Lock size={16} /> Backup preview is read-only. Decisions become available when live Elastic is restored.</div> : null}
      <div className="mt-5 grid gap-4">
        {proposals.map((proposal) => (
          <article key={proposal.proposal_id} className="rounded-md border border-line bg-white p-4 shadow-soft">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`rounded-md px-2 py-1 text-[11px] font-semibold uppercase ${proposal.risk_level === "high" ? "bg-rose-50 text-rose-700" : proposal.risk_level === "low" ? "bg-emerald-50 text-signal" : "bg-amber-50 text-copper"}`}>{proposal.risk_level} risk</span>
                  <span className="rounded-md bg-mist px-2 py-1 text-[11px] font-semibold uppercase text-sage">{humanize(proposal.proposal_type)}</span>
                  <span className="text-xs text-ink/45">{proposal.model_route === "pro" ? "Pro adjudication" : "Flash extraction"}</span>
                </div>
                <h3 className="safe-text mt-3 font-semibold text-night">{proposal.source_id ?? proposal.work_id ?? "Catalog record"}</h3>
                <p className="safe-text mt-2 text-sm leading-6 text-ink/65">{proposal.reasoning}</p>
              </div>
              <div className="rounded-md bg-paper px-3 py-2 text-right">
                <div className="text-lg font-semibold text-night">{Math.round(proposal.confidence * 100)}%</div>
                <div className="text-[11px] uppercase text-ink/45">confidence</div>
              </div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <ProposalValue label="Current" value={proposal.current_value} />
              <ProposalValue label="Suggested" value={proposal.proposed_value} emphasized />
            </div>
            {proposal.evidence?.[0]?.quote ? (
              <blockquote className="evidence-quote mt-4 max-h-32 overflow-auto border-l-4 border-copper bg-mist p-3 text-sm">
                {proposal.evidence[0].quote}
                <div className="mt-2 text-xs not-italic text-sage">{proposal.evidence[0].page_ref ?? "Location unresolved"}</div>
              </blockquote>
            ) : null}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
              <div className="safe-text text-xs text-ink/45">{proposal.model_used} · {proposal.status}</div>
              <div className="flex gap-2">
                <button className="inline-flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm font-semibold text-ink/65 disabled:opacity-40" disabled={readOnly} onClick={() => onDecision(proposal, "reject")} type="button"><X size={15} /> Reject</button>
                <button className="inline-flex items-center gap-2 rounded-md bg-night px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={readOnly} onClick={() => onDecision(proposal, "approve")} type="button"><CheckCircle2 size={15} /> Approve</button>
              </div>
            </div>
          </article>
        ))}
      </div>
      {!proposals.length ? <EmptyState title="No pending catalog proposals" detail="Run a collection analysis after live Elastic is restored, or wait for a newly indexed source to finish automatic analysis." /> : null}
    </section>
  );
}

function ProposalValue({label, value, emphasized = false}: {label: string; value: Record<string, unknown>; emphasized?: boolean}) {
  const rows = flattenRecord(value).slice(0, 8);
  return <div className={`rounded-md border p-3 ${emphasized ? "border-sage bg-emerald-50/35" : "border-line bg-paper/60"}`}><div className="text-[11px] font-semibold uppercase text-sage">{label}</div><div className="mt-2 space-y-1">{rows.map(([key, entry]) => <div key={key} className="grid grid-cols-[110px_minmax(0,1fr)] gap-2 text-xs"><span className="safe-text text-ink/45">{humanize(key)}</span><span className="safe-text font-medium text-night">{String(entry)}</span></div>)}</div></div>;
}

function CatalogHealth({health, hasAdminToken}: {health: CatalogHealthSummary | null; hasAdminToken: boolean}) {
  if (!hasAdminToken) {
    return (
      <section className="mt-7 rounded-md border border-line bg-white p-5 shadow-soft">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase text-sage"><ShieldCheck size={15} /> Catalog health</div>
        <h2 className="mt-1 text-2xl font-semibold text-night">Operator catalog health</h2>
        <p className="mt-2 text-sm leading-6 text-ink/65">
          Catalog health diagnostics open from the jury access link. Public visitors can browse the library without mutating records.
        </p>
      </section>
    );
  }
  if (!health) return <LoadingState />;
  const entries = Object.entries(health.counts);
  return (
    <section className="mt-7">
      <div className="grid gap-5 border-b border-line pb-5 lg:grid-cols-[220px_minmax(0,1fr)]">
        <div className="rounded-md bg-night p-5 text-white">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase text-white/60"><ShieldCheck size={15} /> Catalog health</div>
          <div className="mt-4 text-5xl font-semibold">{health.score}</div>
          <div className="mt-1 text-sm text-white/55">out of 100</div>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {entries.map(([key, value]) => <Metric key={key} label={humanize(key)} value={value} />)}
        </div>
      </div>
      <div className="mt-5 grid gap-3">
        {health.issues.map((issue, index) => (
          <article key={`${issue.issue_type}-${issue.record_id}-${index}`} className="flex flex-col gap-3 rounded-md border border-line bg-white p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0"><div className="flex items-center gap-2 text-xs font-semibold uppercase text-copper"><CircleAlert size={14} /> {humanize(issue.issue_type)}</div><div className="safe-text mt-2 font-semibold text-night">{issue.title ?? issue.record_id ?? "Catalog record"}</div></div>
            <div className="safe-text text-xs text-ink/45">{issue.record_id}</div>
          </article>
        ))}
      </div>
      {!health.issues.length ? <EmptyState title="Catalog is structurally healthy" detail="No unresolved metadata, orphan records, weak OCR sources, or relationship gaps were detected." /> : null}
    </section>
  );
}

function RelationshipWorkspace({
  works,
  edges,
  backend,
  hasAdminToken,
  readOnly,
  proposals,
  onDecision,
}: {
  works: WorkRecord[];
  edges: RelationshipLineage[];
  backend?: string;
  hasAdminToken: boolean;
  readOnly: boolean;
  proposals: CatalogProposal[];
  onDecision: (proposal: CatalogProposal, action: "approve" | "reject") => void;
}) {
  const workIds = new Set(works.map((work) => work.work_id));
  const workEdges = edges.filter((edge) => edge.from_type === "work" && edge.to_type === "work");
  const sourceEdges = edges.filter((edge) => edge.from_type === "source" || edge.to_type === "source");
  const unresolved = edges.filter((edge) => !workIds.has(String(edge.from_id)) && !workIds.has(String(edge.to_id)));
  return (
    <section className="mt-7 grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0 space-y-5">
        <div className="rounded-md border border-line bg-white p-5 shadow-soft">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase text-sage"><GitBranch size={15} /> Relationship graph</div>
              <h2 className="mt-1 text-2xl font-semibold text-night">Commentary and witness lineage</h2>
              <p className="mt-2 text-sm leading-6 text-ink/65">
                Canonical graph edges are visible to every reviewer. Gemini reanalysis creates proposals first; approved proposals enter the graph.
              </p>
            </div>
            <span className={`rounded-md px-3 py-2 text-xs font-semibold ${backend === "elasticsearch" ? "bg-emerald-50 text-signal" : "bg-amber-50 text-copper"}`}>
              {backend === "elasticsearch" ? "Live Elastic graph" : "Backup graph preview"}
            </span>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-4">
            <Metric label="works" value={works.length} />
            <Metric label="canonical edges" value={edges.length} />
            <Metric label="work links" value={workEdges.length} />
            <Metric label="source links" value={sourceEdges.length} />
          </div>
        </div>

        <LineagePanel works={works} edges={edges} backend={backend} />

        <div className="rounded-md border border-line bg-white p-5 shadow-soft">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase text-sage"><NetworkIcon /> Canonical edges</div>
          <div className="mt-4 grid gap-3">
            {edges.map((edge, index) => (
              <RelationshipEdgeCard key={edge.edge_id ?? `${edge.from_id}-${edge.to_id}-${index}`} edge={edge} />
            ))}
          </div>
          {!edges.length ? <EmptyState title="No graph edges yet" detail="Open the jury access link, run relationship analysis, then approve proposals into the canonical graph." /> : null}
        </div>
      </div>

      <aside className="min-w-0 space-y-5">
        <div className="rounded-md border border-line bg-white p-4 shadow-soft">
          <div className="text-xs font-semibold uppercase text-sage">Operator access</div>
          {hasAdminToken ? (
            <p className="mt-2 text-sm leading-6 text-ink/65">
              Relationship reanalysis is available from the toolbar above. Results appear here as review proposals before touching the canonical graph.
            </p>
          ) : (
            <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-900">
              Relationship reanalysis and proposal approval open from the jury access link.
            </div>
          )}
          {readOnly ? (
            <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              Backup Preview is read-only. Reanalysis requires Live Elastic.
            </div>
          ) : null}
        </div>

        <div className="rounded-md border border-line bg-white p-4 shadow-soft">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs font-semibold uppercase text-sage">Relationship proposals</div>
            <span className="rounded-md bg-paper px-2 py-1 text-xs font-semibold">{proposals.length}</span>
          </div>
          <div className="mt-3 space-y-3">
            {proposals.map((proposal) => (
              <RelationshipProposalCard key={proposal.proposal_id} proposal={proposal} readOnly={readOnly} onDecision={onDecision} />
            ))}
          </div>
          {!proposals.length ? (
            <p className="mt-3 rounded-md bg-paper p-3 text-sm leading-6 text-ink/65">
              No pending relationship proposals. Open jury access to run reanalysis, or use the existing canonical graph.
            </p>
          ) : null}
        </div>

        {unresolved.length ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-4">
            <div className="text-xs font-semibold uppercase text-amber-800">Metadata warnings</div>
            <p className="mt-2 text-sm leading-6 text-amber-900">{unresolved.length} edge(s) reference records outside the current work set.</p>
          </div>
        ) : null}
      </aside>
    </section>
  );
}

function RelationshipEdgeCard({edge}: {edge: RelationshipLineage}) {
  return (
    <article className="rounded-md border border-line bg-paper/55 p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="safe-text text-sm font-semibold text-night">{humanize(String(edge.from_id ?? edge.from ?? "source"))} → {humanize(String(edge.to_id ?? edge.to ?? "target"))}</div>
          <div className="mt-1 flex flex-wrap gap-2 text-xs text-ink/60">
            <span className="rounded-md bg-white px-2 py-1">{humanize(String(edge.relation ?? "relationship"))}</span>
            <span className="rounded-md bg-white px-2 py-1">{edge.from_type ?? "record"} to {edge.to_type ?? "record"}</span>
            <span className="rounded-md bg-white px-2 py-1">{Math.round(Number(edge.confidence ?? 0) * 100)}%</span>
          </div>
        </div>
        <span className="rounded-md bg-white px-2 py-1 text-xs text-sage">{edge.verification_status ?? edge.provenance ?? "unverified"}</span>
      </div>
      {edge.reasoning_summary ? <p className="mt-2 text-xs leading-5 text-ink/65">{edge.reasoning_summary}</p> : null}
    </article>
  );
}

function RelationshipProposalCard({proposal, readOnly, onDecision}: {proposal: CatalogProposal; readOnly: boolean; onDecision: (proposal: CatalogProposal, action: "approve" | "reject") => void}) {
  return (
    <article className="rounded-md border border-line bg-paper p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="safe-text text-sm font-semibold text-night">{proposal.source_id ?? proposal.work_id ?? humanize(proposal.proposal_type)}</div>
          <p className="mt-1 text-xs leading-5 text-ink/65">{proposal.reasoning}</p>
        </div>
        <span className="rounded-md bg-white px-2 py-1 text-xs font-semibold">{Math.round(proposal.confidence * 100)}%</span>
      </div>
      <div className="mt-3 grid gap-2 text-xs">
        <ProposalValue label="Suggested relationship" value={proposal.proposed_value} emphasized />
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button className="rounded-md border border-line bg-white px-2 py-1 text-xs font-semibold text-ink/65 disabled:opacity-40" disabled={readOnly} onClick={() => onDecision(proposal, "reject")} type="button">Reject</button>
        <button className="rounded-md bg-night px-2 py-1 text-xs font-semibold text-white disabled:opacity-40" disabled={readOnly} onClick={() => onDecision(proposal, "approve")} type="button">Approve</button>
      </div>
    </article>
  );
}

function NetworkIcon() {
  return <GitBranch size={15} />;
}

function BackendBanner({meta}: {meta: LibrarySearchResponse["meta"]}) {
  const preview = meta?.backend === "elastic_backup_preview";
  const live = meta?.backend === "elasticsearch";
  return (
    <div className={`flex flex-col gap-2 border-l-4 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between ${live ? "border-signal bg-emerald-50/70" : preview ? "border-copper bg-amber-50/70" : "border-line bg-white"}`}>
      <div className="flex items-center gap-2 font-semibold text-night">
        {live ? <CheckCircle2 className="text-signal" size={17} /> : <CircleAlert className="text-copper" size={17} />}
        {live ? "Live Elastic context layer" : preview ? "Elastic backup preview" : "Limited seed fallback"}
      </div>
      <div className="safe-text text-xs text-ink/60">
        {preview ? "Read-only snapshot for UI development. Hybrid retrieval and operator changes require live Elastic." : meta?.limitations}
      </div>
    </div>
  );
}

function LibraryRail({libraries, selected, onSelect}: {libraries: LibrarySummary[]; selected: string; onSelect: (id: string) => void}) {
  return (
    <section className="mt-6 grid gap-3 md:grid-cols-2">
      {libraries.map((library) => {
        const active = library.library_id === selected;
        return (
          <button
            key={library.library_id}
            className={`min-w-0 rounded-md border p-4 text-left transition ${active ? "border-sage bg-night text-white shadow-board" : "border-line bg-white hover:border-sage"}`}
            onClick={() => onSelect(library.library_id)}
            type="button"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className={`safe-text font-semibold ${active ? "text-white" : "text-night"}`}>{library.name ?? humanize(library.library_id)}</div>
                <div className={`safe-text mt-1 text-xs leading-5 ${active ? "text-white/65" : "text-ink/55"}`}>{library.description}</div>
              </div>
              <ChevronRight className={active ? "text-white" : "text-sage"} size={18} />
            </div>
            <div className="mt-4 grid grid-cols-4 gap-2 text-xs">
              <Metric label="works" value={library.work_count} active={active} />
              <Metric label="witnesses" value={library.source_count} active={active} />
              <Metric label="passages" value={compactNumber(library.passage_count)} active={active} />
              <Metric label="relations" value={library.edge_count} active={active} />
            </div>
          </button>
        );
      })}
    </section>
  );
}

function WorkCard({work, witnesses, selected, onSelect}: {work: WorkRecord; witnesses: SourceWitness[]; selected: boolean; onSelect: () => void}) {
  const searchable = witnesses.filter((source) => source.ingestion_status === "searchable").length;
  return (
    <article className={`rounded-md border bg-white p-4 shadow-soft transition ${selected ? "border-sage ring-2 ring-sage/15" : "border-line"}`}>
      <button className="w-full text-left" onClick={onSelect} type="button">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-wrap gap-2"><LayerPill layer={work.layer_type} />{work.catalog_review_status === "needs_review" ? <span className="rounded-md bg-amber-50 px-2 py-1 text-[11px] font-semibold uppercase text-amber-800">Metadata review</span> : null}</div>
          <ChevronRight className="shrink-0 text-sage" size={18} />
        </div>
        <h3 className="safe-text mt-3 text-lg font-semibold text-night">{work.title ?? humanize(work.work_id)}</h3>
        {work.title_ar ? <div className="arabic safe-text mt-2 text-base text-copper">{work.title_ar}</div> : null}
        <div className="safe-text mt-3 text-sm font-medium text-sage">{work.author_name ?? "Metadata unresolved"}</div>
        <div className="mt-4 grid grid-cols-3 gap-2">
          <Metric label="witnesses" value={witnesses.length || work.source_count} />
          <Metric label="searchable" value={searchable || work.searchable_source_count} />
          <Metric label="passages" value={compactNumber(work.passage_count)} />
        </div>
      </button>
      <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-3 text-xs text-ink/55">
        <span>{work.domain ?? "classical texts"}</span>
        <span>·</span>
        <span>{work.relationship_count ?? 0} relationships</span>
      </div>
    </article>
  );
}

function WorkDetail({work, sources, proposals, readOnly, onClose}: {work: WorkRecord; sources: SourceWitness[]; proposals: CatalogProposal[]; readOnly: boolean; onClose: () => void}) {
  const citation = [work.author_name, work.title ?? work.work_id].filter(Boolean).join("; ");
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-night/35" role="dialog" aria-modal="true">
      <section className="h-full w-full max-w-2xl overflow-y-auto bg-mist shadow-board">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-white/95 px-5 py-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-sage"><BookOpen size={17} /> Work dossier</div>
          <button className="rounded-md p-2 hover:bg-paper" onClick={onClose} title="Close work detail" type="button"><X size={18} /></button>
        </div>
        <div className="p-5">
          <LayerPill layer={work.layer_type} />
          <h2 className="safe-text mt-3 text-2xl font-semibold text-night">{work.title ?? humanize(work.work_id)}</h2>
          {work.title_ar ? <div className="arabic safe-text mt-2 text-xl text-copper">{work.title_ar}</div> : null}
          <div className="mt-3 text-sm font-semibold text-sage">{work.author_name ?? "Metadata unresolved"}</div>
          <div className="mt-5 flex flex-wrap gap-2">
            <Action icon={<Clipboard size={15} />} label="Copy citation" onClick={() => navigator.clipboard.writeText(citation)} />
            <a className="inline-flex items-center gap-2 rounded-md bg-night px-3 py-2 text-sm font-semibold text-white" href={`/?passage=${encodeURIComponent(work.title ?? work.work_id)}&library_id=${encodeURIComponent(work.library_id ?? "demo_kalam")}`}>
              <Play size={15} /> Investigate work
            </a>
          </div>
          {proposals.length ? (
            <div className="mt-6 rounded-md border border-copper/35 bg-amber-50/70 p-4">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase text-copper"><Sparkles size={15} /> Pending curator suggestions</div>
              <div className="mt-3 space-y-2">{proposals.slice(0, 4).map((proposal) => <div key={proposal.proposal_id} className="flex items-center justify-between gap-3 text-sm"><span className="safe-text font-medium text-night">{humanize(proposal.proposal_type)}</span><span className="text-xs text-ink/50">{Math.round(proposal.confidence * 100)}% · {proposal.model_route}</span></div>)}</div>
            </div>
          ) : null}

          <div className="mt-7 flex items-center justify-between border-b border-line pb-3">
            <div>
              <div className="text-xs font-semibold uppercase text-sage">Witnesses & sources</div>
              <div className="mt-1 text-sm text-ink/60">PDFs, text layers, OCR outputs, and editions grouped under this work.</div>
            </div>
            <span className="rounded-md bg-white px-2 py-1 text-xs font-semibold">{sources.length}</span>
          </div>
          <div className="divide-y divide-line">
            {sources.map((source) => <WitnessRow key={source.source_id} source={source} readOnly={readOnly} />)}
          </div>
        </div>
      </section>
    </div>
  );
}

function WitnessRow({source, readOnly}: {source: SourceWitness; readOnly: boolean}) {
  const searchable = source.ingestion_status === "searchable";
  return (
    <article className="py-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-md px-2 py-1 text-xs font-semibold ${searchable ? "bg-emerald-50 text-signal" : "bg-amber-50 text-copper"}`}>{source.ingestion_status ?? "unknown"}</span>
            <span className="text-xs text-ink/50">{source.file_type ?? "source"} · {source.provider ?? "provider unresolved"}</span>
          </div>
          <div className="safe-text mt-2 font-semibold text-night">{source.title ?? humanize(source.source_id)}</div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink/60">
            <span>{compactNumber(source.indexed_passage_count)} passages</span>
            <span>{source.ocr_page_count ?? 0} pages</span>
            <span>{source.ocr_quality_status ?? source.ocr_status ?? "OCR unknown"}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <a className="rounded-md border border-line bg-white p-2 text-sage" href={`/sources/${encodeURIComponent(source.source_id)}`} title={readOnly ? "View source status" : "Open OCR editor"}>
            {readOnly ? <FileText size={16} /> : <FilePenLine size={16} />}
          </a>
          {source.source_page_url || source.url ? (
            <a className="rounded-md border border-line bg-white p-2 text-sage" href={source.source_page_url ?? source.url} target="_blank" title="Open source">
              <ExternalLink size={16} />
            </a>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function LineagePanel({works, edges, backend}: {works: WorkRecord[]; edges: RelationshipLineage[]; backend?: string}) {
  const workIds = new Set(works.map((work) => work.work_id));
  const meaningful = edges.filter((edge) => edge.from_type === "work" && edge.to_type === "work" && workIds.has(String(edge.from_id)) && workIds.has(String(edge.to_id)));
  const layers = ["matn", "sharh", "hashiya"].map((layer) => ({
    layer,
    works: works.filter((work) => normalizedLayer(work.layer_type) === layer),
  })).filter((group) => group.works.length);
  return (
    <section className="mt-7 border-t border-line pt-5">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase text-sage"><GitBranch size={15} /> Commentary lineage</div>
      {layers.length ? (
        <div className="mt-4 space-y-3">
          {layers.map((group, index) => (
            <div key={group.layer}>
              {index ? <div className="mb-3 flex justify-center text-copper"><ArrowRight className="rotate-90" size={17} /></div> : null}
              <div className="rounded-md border border-line bg-white p-3">
                <div className="text-[11px] font-semibold uppercase text-copper">{layerLabel(group.layer)}</div>
                <div className="mt-2 space-y-2">
                  {group.works.map((work) => (
                    <div key={work.work_id} className="safe-text text-sm font-semibold text-night">{work.title_ar ?? work.title ?? humanize(work.work_id)}</div>
                  ))}
                </div>
              </div>
            </div>
          ))}
          <div className="rounded-md bg-paper px-3 py-2 text-xs text-ink/60">
            {meaningful.length} resolved work-to-work relations · {backend === "elasticsearch" ? "live Elastic graph" : "backup graph preview"}
          </div>
        </div>
      ) : (
        <EmptyState title="Lineage not resolved" detail={backend === "elastic_backup_preview" ? "No work-level relationship was present in this backup preview." : "Relationship analysis has not produced a work-level chain yet."} />
      )}
    </section>
  );
}

function CollectionSummary({library}: {library?: LibrarySummary}) {
  if (!library) return null;
  return (
    <section>
      <div className="text-xs font-semibold uppercase text-sage">Collection health</div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <Metric label="works" value={library.work_count} />
        <Metric label="witnesses" value={library.source_count} />
        <Metric label="searchable" value={library.searchable_source_count} />
        <Metric label="passages" value={compactNumber(library.passage_count)} />
      </div>
    </section>
  );
}

function PassageResults({passages, query}: {passages: Record<string, unknown>[]; query: string}) {
  return (
    <section className="mt-8 border-t border-line pt-5">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase text-sage"><Search size={15} /> Matching passage samples</div>
      <div className="mt-4 grid gap-3">
        {passages.slice(0, 8).map((passage) => (
          <article key={String(passage.passage_id)} className="rounded-md border border-line bg-white p-4">
            <div className="safe-text text-sm font-semibold text-night">{String(passage.work_title ?? passage.work_id ?? "Work metadata unresolved")}</div>
            <div className="safe-text mt-1 text-xs text-sage">{String(passage.author_name ?? "Author metadata unresolved")} · {String(passage.page_ref ?? passage.source_page ?? "location unresolved")}</div>
            <blockquote className="evidence-quote mt-3 max-h-40 overflow-auto border-l-4 border-copper bg-mist p-3 text-base">{highlight(String(passage.text_raw ?? ""), query)}</blockquote>
          </article>
        ))}
      </div>
    </section>
  );
}

function UploadDialog({libraryId, works, onClose, onComplete}: {libraryId: string; works: WorkRecord[]; onClose: () => void; onComplete: () => void}) {
  const [kind, setKind] = useState<UploadKind>("existing_work_source");
  const [workId, setWorkId] = useState(works[0]?.work_id ?? "");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  async function submit() {
    setLoading(true);
    setMessage("");
    try {
      await addLibrarySource({
        libraryId,
        provider: "Institutional Upload",
        workId: kind === "existing_work_source" ? workId : workId || slug(title),
        title,
        authorName: author,
        url: url || undefined,
        file,
      });
      onComplete();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setLoading(false);
    }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-night/45 p-4" role="dialog" aria-modal="true">
      <section className="w-full max-w-xl rounded-md bg-white p-5 shadow-board">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 font-semibold text-night"><Upload size={17} /> Add to {humanize(libraryId)}</div>
          <button className="rounded-md p-2 hover:bg-paper" onClick={onClose} title="Close upload" type="button"><X size={18} /></button>
        </div>
        <div className="mt-5 grid grid-cols-2 gap-2">
          <Choice active={kind === "existing_work_source"} icon={<BookCopy size={17} />} label="New witness" onClick={() => setKind("existing_work_source")} />
          <Choice active={kind === "new_work"} icon={<BookOpen size={17} />} label="New work" onClick={() => setKind("new_work")} />
        </div>
        {kind === "existing_work_source" ? (
          <label className="mt-4 block text-xs font-semibold text-ink/60">Existing work<select className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm" value={workId} onChange={(event) => setWorkId(event.target.value)}>{works.map((work) => <option key={work.work_id} value={work.work_id}>{work.title ?? work.work_id}</option>)}</select></label>
        ) : (
          <label className="mt-4 block text-xs font-semibold text-ink/60">Work ID<input className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm" value={workId} onChange={(event) => setWorkId(event.target.value)} placeholder="Generated from title if blank" /></label>
        )}
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Title" value={title} onChange={setTitle} />
          <Field label="Author" value={author} onChange={setAuthor} />
        </div>
        <Field label="Source URL" value={url} onChange={setUrl} />
        <label className="mt-3 block text-xs font-semibold text-ink/60">PDF or text file<input className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm" type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
        {message ? <div className="safe-text mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-copper">{message}</div> : null}
        <button className="mt-5 inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-md bg-night px-4 text-sm font-semibold text-white disabled:opacity-50" disabled={loading || !title || (!url && !file)} onClick={submit} type="button">
          {loading ? <Loader2 className="animate-spin" size={16} /> : <Upload size={16} />} Register source
        </button>
      </section>
    </div>
  );
}

function Choice({active, icon, label, onClick}: {active: boolean; icon: React.ReactNode; label: string; onClick: () => void}) {
  return <button className={`flex items-center gap-2 rounded-md border px-3 py-3 text-sm font-semibold ${active ? "border-sage bg-mist text-night" : "border-line text-ink/60"}`} onClick={onClick} type="button">{icon}{label}</button>;
}
function Field({label, value, onChange}: {label: string; value: string; onChange: (value: string) => void}) {
  return <label className="mt-3 block text-xs font-semibold text-ink/60">{label}<input className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}
function Filter({label, value, options, onChange}: {label: string; value: string; options: string[]; onChange: (value: string) => void}) {
  return <label className="text-xs font-semibold text-ink/55">{label}<select className="mt-1 w-full rounded-md border border-line bg-white px-3 py-2 text-sm" value={value} onChange={(event) => onChange(event.target.value)}><option value="">All</option>{options.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>;
}
function LayerPill({layer}: {layer?: string}) {
  const normalized = normalizedLayer(layer);
  return <span className="inline-flex rounded-md bg-mist px-2 py-1 text-[11px] font-semibold uppercase text-copper">{layerLabel(normalized)}</span>;
}
function Metric({label, value, active = false}: {label: string; value: unknown; active?: boolean}) {
  return <div className={`rounded-md px-2 py-2 ${active ? "bg-white/10" : "bg-paper"}`}><div className={`font-semibold ${active ? "text-white" : "text-night"}`}>{String(value ?? 0)}</div><div className={active ? "text-white/55" : "text-ink/45"}>{label}</div></div>;
}
function Action({icon, label, onClick}: {icon: React.ReactNode; label: string; onClick: () => void}) {
  return <button className="inline-flex items-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold" onClick={onClick} type="button">{icon}{label}</button>;
}
function EmptyState({title, detail}: {title: string; detail: string}) {
  return <div className="mt-5 border border-dashed border-line bg-white/60 px-4 py-8 text-center"><div className="font-semibold text-night">{title}</div><div className="mt-2 text-sm text-ink/55">{detail}</div></div>;
}
function LoadingState() {
  return <div className="mt-6 flex items-center gap-2 text-sm text-ink/55"><Loader2 className="animate-spin" size={17} /> Loading catalog</div>;
}
function preferredLibrary(libraries: LibrarySummary[]) {
  return libraries.find((library) => library.library_id === "shamsiyya_hashiya_demo")?.library_id ?? libraries[0]?.library_id ?? "";
}
function buildWorkRecords(works: WorkRecord[], sources: SourceWitness[], edges: RelationshipLineage[]) {
  const records = new Map(works.map((work) => [work.work_id, {...work}]));
  for (const source of sources) {
    const workId = source.work_id ?? source.source_id;
    const current = records.get(workId) ?? {work_id: workId};
    const witnesses = sources.filter((item) => (item.work_id ?? item.source_id) === workId);
    records.set(workId, {
      ...current,
      title: current.title ?? source.title ?? humanize(workId),
      title_ar: current.title_ar ?? source.title_ar,
      author_name: current.author_name ?? source.author_name ?? "Metadata unresolved",
      library_id: current.library_id ?? source.library_id,
      layer_type: current.layer_type ?? source.text_layer ?? source.source_role ?? "independent_work",
      layer_rank: current.layer_rank ?? source.layer_rank,
      source_count: current.source_count ?? witnesses.length,
      passage_count: current.passage_count ?? witnesses.reduce((total, item) => total + Number(item.indexed_passage_count ?? 0), 0),
      searchable_source_count: current.searchable_source_count ?? witnesses.filter((item) => item.ingestion_status === "searchable").length,
      relationship_count: current.relationship_count ?? edges.filter((edge) => edge.from_id === workId || edge.to_id === workId).length,
      catalog_review_status: current.catalog_review_status ?? (witnesses.some((item) => item.catalog_review_status === "needs_review") ? "needs_review" : "verified"),
      catalog_review_reasons: current.catalog_review_reasons ?? witnesses.flatMap((item) => item.catalog_review_reasons ?? []),
      metadata_quality_score: current.metadata_quality_score ?? (witnesses.length ? Math.min(...witnesses.map((item) => Number(item.metadata_quality_score ?? 100))) : 100),
      ocr_status_summary: current.ocr_status_summary ?? (witnesses.some((item) => String(item.ocr_quality_status ?? "").includes("low") || String(item.ocr_quality_status ?? "").includes("weak")) ? "low_quality" : "acceptable"),
    });
  }
  return Array.from(records.values()).sort((left, right) => (
    String(left.library_id).localeCompare(String(right.library_id))
    || Number(left.layer_rank ?? 99) - Number(right.layer_rank ?? 99)
    || String(left.title).localeCompare(String(right.title))
  ));
}
function libraryTitle(libraries: LibrarySummary[], id: string) {
  return libraries.find((library) => library.library_id === id)?.name ?? humanize(id || "Collection");
}
function normalizedLayer(layer?: string) {
  const value = String(layer ?? "").toLowerCase();
  if (value.includes("matn") || value.includes("base_text")) return "matn";
  if (value.includes("sharh") || value.includes("commentary")) return "sharh";
  if (value.includes("hashiya")) return "hashiya";
  return "independent";
}
function layerLabel(layer?: string) {
  return {matn: "Base text", sharh: "Primary commentary", hashiya: "Hashiya / gloss", independent: "Independent work"}[normalizedLayer(layer)] ?? "Independent work";
}
function compactNumber(value: unknown) {
  const number = Number(value ?? 0);
  return number >= 1000 ? `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)}k` : number;
}
function humanize(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (part) => part.toUpperCase());
}
function slug(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || `work-${Date.now()}`;
}
function flattenRecord(value: Record<string, unknown>) {
  const fields = value.fields && typeof value.fields === "object" ? value.fields as Record<string, unknown> : value;
  return Object.entries(fields).filter(([, entry]) => entry !== null && entry !== undefined && typeof entry !== "object");
}
function highlight(text: string, query: string) {
  const tokens = query.trim().split(/\s+/).filter((part) => part.length > 2).slice(0, 4);
  if (!tokens.length) return text;
  const pattern = new RegExp(`(${tokens.map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(pattern).map((part, index) => tokens.some((token) => token.toLowerCase() === part.toLowerCase()) ? <mark key={`${part}-${index}`} className="rounded bg-bronze/30 px-1">{part}</mark> : <span key={`${part}-${index}`}>{part}</span>);
}
