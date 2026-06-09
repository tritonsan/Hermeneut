"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, BookOpen, ChevronDown, FileSearch, Globe2, Layers3, Loader2, Play, Quote, Sparkles } from "lucide-react";
import { createRun, getJuryStatus, searchLibrary } from "@/lib/api";
import type {JuryStatus} from "@/lib/types";
import type { LibrarySearchResponse } from "@/lib/types";

const examples = [
  {
    title: "Shamsiyya copula",
    passage: "لغة اليونان توجب ذكر الرابطة الزمانية دون غيرها",
    libraryId: "shamsiyya_hashiya_demo",
    domainHint: "Arabic logic/commentary",
    context: "Institutional Shamsiyya hashiya demo library.",
  },
  {
    title: "Necessary existence",
    passage: "قيل إن ما كان وجوده من غيره فهو ممكن، وأما الواجب فلا يكون وجوده عن غيره.",
    libraryId: "demo_kalam",
    domainHint: "kalam/philosophy",
    context: "Classical Arabic kalam/philosophy demo corpus.",
  },
  {
    title: "Eternity of the world",
    passage: "ذكر بعضهم أن العالم لا أول لوجوده وأنه صدر عن الأول بالضرورة.",
    libraryId: "demo_kalam",
    domainHint: "kalam/philosophy",
    context: "Classical Arabic kalam/philosophy demo corpus.",
  },
  {
    title: "Divine knowledge",
    passage: "ينسب إلى الفلاسفة أن الأول يعلم الكليات ولا يعلم الجزئيات المتغيرة.",
    libraryId: "demo_kalam",
    domainHint: "kalam/philosophy",
    context: "Classical Arabic kalam/philosophy demo corpus.",
  },
];

const intentOptions: {key: "exact" | "paraphrase" | "scoped" | "discovery"; label: string; icon: React.ReactNode}[] = [
  {key: "exact", label: "Exact quote", icon: <Quote size={15} />},
  {key: "paraphrase", label: "Paraphrase", icon: <FileSearch size={15} />},
  {key: "scoped", label: "Author/work scope", icon: <Layers3 size={15} />},
  {key: "discovery", label: "Open web", icon: <Globe2 size={15} />},
];

export function RunConsole() {
  const router = useRouter();
  const [mode, setMode] = useState<"library" | "open_discovery">("library");
  const [passage, setPassage] = useState(examples[0].passage);
  const [context, setContext] = useState(examples[0].context);
  const [containingAuthor, setContainingAuthor] = useState("");
  const [containingWork, setContainingWork] = useState("");
  const [periodHint, setPeriodHint] = useState("4th-6th/10th-12th century");
  const [domainHint, setDomainHint] = useState(examples[0].domainHint);
  const [languageHint, setLanguageHint] = useState("ar");
  const [libraryId, setLibraryId] = useState(examples[0].libraryId);
  const [enableWebResearch, setEnableWebResearch] = useState(true);
  const [allowSourceDownloadSuggestions, setAllowSourceDownloadSuggestions] = useState(true);
  const [autoDownloadSources, setAutoDownloadSources] = useState(true);
  const [ocrMode, setOcrMode] = useState<"full" | "text_layer_first" | "skip">("full");
  const [intent, setIntent] = useState<"exact" | "paraphrase" | "scoped" | "discovery">("exact");
  const [coverage, setCoverage] = useState<LibrarySearchResponse>({});
  const [juryStatus, setJuryStatus] = useState<JuryStatus>({access: "public", research_enabled: false, operator_enabled: false});
  const [hintsOpen, setHintsOpen] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const normalizedPreview = useMemo(() => passage.replace(/[أإآ]/g, "ا").replace(/[ى]/g, "ي").replace(/[ة]/g, "ه").trim(), [passage]);
  const variantChips = useMemo(() => {
    const chips = [passage.trim(), normalizedPreview].filter(Boolean);
    if (domainHint) chips.push(domainHint);
    return Array.from(new Set(chips)).slice(0, 3);
  }, [domainHint, normalizedPreview, passage]);

  useEffect(() => {
    let cancelled = false;
    searchLibrary("")
      .then((result) => {
        if (!cancelled) setCoverage(result);
      })
      .catch(() => {
        if (!cancelled) setCoverage({});
      });
    return () => {
      cancelled = true;
    };
  }, [libraryId]);

  useEffect(() => {
    let cancelled = false;
    getJuryStatus()
      .then((status) => {
        if (!cancelled) setJuryStatus(status);
      })
      .catch(() => {
        if (!cancelled) setJuryStatus({access: "public", research_enabled: false, operator_enabled: false});
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedPassage = params.get("passage");
    const requestedLibrary = params.get("library_id");
    if (requestedPassage) setPassage(requestedPassage);
    if (requestedLibrary) setLibraryId(requestedLibrary);
  }, []);

  function applyIntent(nextIntent: typeof intent) {
    setIntent(nextIntent);
    if (nextIntent === "discovery") {
      setMode("open_discovery");
      setEnableWebResearch(true);
      setAllowSourceDownloadSuggestions(true);
      setAutoDownloadSources(true);
      setOcrMode("full");
    }
    if (nextIntent === "exact") {
      setMode("library");
      setEnableWebResearch(false);
    }
    if (nextIntent === "paraphrase") {
      setMode("library");
    }
    if (nextIntent === "scoped") {
      setMode("library");
      setHintsOpen(true);
    }
  }

  async function submit() {
    setLoading(true);
    setError(null);
    try {
      const run = await createRun({
        mode,
        passage,
        context,
        containing_author: mode === "open_discovery" ? containingAuthor || undefined : undefined,
        containing_work: mode === "open_discovery" ? containingWork || undefined : undefined,
        period_hint: periodHint || undefined,
        domain_hint: domainHint || undefined,
        language_hint: languageHint || undefined,
        library_id: libraryId || "demo_kalam",
        enable_web_research: mode === "open_discovery" ? enableWebResearch : false,
        allow_source_download_suggestions: mode === "open_discovery" ? allowSourceDownloadSuggestions : false,
        auto_download_sources: mode === "open_discovery" ? autoDownloadSources : false,
        max_source_candidates: mode === "open_discovery" ? 12 : 5,
        max_pdf_downloads: mode === "open_discovery" ? 6 : 3,
        max_containing_source_downloads: mode === "open_discovery" ? 3 : 0,
        max_citation_source_downloads: mode === "open_discovery" ? 3 : 0,
        ocr_mode: ocrMode,
      });
      router.push(`/runs/${run.run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
      <div className="scholarly-surface rounded-md border border-line p-5 shadow-soft">
        <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-sage">
          <Sparkles size={17} />
          Research cockpit
        </div>
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          <button
            className={mode === "library" ? "rounded-md border border-sage bg-mist p-3 text-left shadow-insetLine" : "rounded-md border border-line bg-white p-3 text-left hover:border-sage"}
            onClick={() => setMode("library")}
            type="button"
          >
            <div className="flex items-center gap-2 text-sm font-semibold">
              <BookOpen size={17} />
              Search My Library
            </div>
            <p className="mt-2 text-xs leading-5 text-ink/65">Search only OCR/indexed sources inside the selected library.</p>
          </button>
          <button
            className={mode === "open_discovery" ? "rounded-md border border-sage bg-mist p-3 text-left shadow-insetLine" : "rounded-md border border-line bg-white p-3 text-left hover:border-sage"}
            onClick={() => setMode("open_discovery")}
            type="button"
          >
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Globe2 size={17} />
              Open Source Discovery
            </div>
            <p className="mt-2 text-xs leading-5 text-ink/65">Discover candidate sources online first; search only after OCR/indexing.</p>
          </button>
        </div>
        <div className="mb-4 grid gap-2 sm:grid-cols-4">
          {intentOptions.map(({key, label, icon}) => (
            <button
              key={key}
              className={intent === key ? "inline-flex items-center justify-center gap-2 rounded-md border border-sage bg-night px-3 py-2 text-xs font-semibold text-white" : "inline-flex items-center justify-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-xs font-semibold text-ink/65 hover:border-sage"}
              onClick={() => applyIntent(key)}
              type="button"
            >
              {icon}
              {label}
            </button>
          ))}
        </div>
        {mode === "library" ? (
          <div className="mb-4 grid gap-2 rounded-md border border-line bg-paper p-3 text-xs text-ink/70 sm:grid-cols-4">
            <span className="font-semibold text-sage">Library coverage</span>
            <span>passages: {String(coverage.meta?.counts?.passages ?? "..." )}</span>
            <span>sources: {String(coverage.meta?.counts?.sources ?? "...")}</span>
            <span>works: {String(coverage.meta?.counts?.works ?? "...")}</span>
          </div>
        ) : (
          <div className="mb-4 grid gap-2 rounded-md border border-line bg-paper p-3 text-xs text-ink/70 sm:grid-cols-5">
            {["discovered", "candidate", "approval", "OCR/index", "searchable"].map((step) => (
              <span key={step} className="rounded-md bg-white px-2 py-1 text-center font-semibold">{step}</span>
            ))}
            <span className="safe-text rounded-md bg-night px-2 py-1 text-center font-semibold text-white sm:col-span-5">
              default OCR policy: 3 containing-layer + 3 citation-chain targets
            </span>
          </div>
        )}
        {coverage.meta?.read_only ? (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm font-semibold text-amber-800">
            <BookOpen size={16} />
            Research requires Live Elastic. Library browsing remains available in Backup Preview.
          </div>
        ) : null}
        {!juryStatus.research_enabled ? (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-sm font-semibold text-ink/70">
            <BookOpen size={16} />
            Public preview is active. Search and OCR workflows open from the jury access link.
          </div>
        ) : null}
        <textarea
          className="arabic min-h-44 w-full resize-y rounded-md border border-line bg-white/80 p-4 text-xl outline-none focus:border-copper"
          value={passage}
          onChange={(event) => setPassage(event.target.value)}
        />
        <label className="mt-4 block text-sm font-medium text-ink/70">Optional context</label>
        <input
          className="mt-2 w-full rounded-md border border-line bg-white px-3 py-2 outline-none focus:border-copper"
          value={context}
          onChange={(event) => setContext(event.target.value)}
        />
        <div className="mt-3 flex flex-wrap gap-2 text-xs text-ink/60">
          {variantChips.map((chip, index) => (
            <span key={`${chip}-${index}`} className="safe-text rounded-md bg-paper px-2 py-1">
              variant: {chip}
            </span>
          ))}
        </div>
        <button
          className="mt-4 flex w-full items-center justify-between rounded-md border border-line bg-paper px-3 py-2 text-sm font-semibold text-ink"
          onClick={() => setHintsOpen((current) => !current)}
          type="button"
        >
          Research hints
          <ChevronDown className={hintsOpen ? "rotate-180 transition" : "transition"} size={17} />
        </button>
        {hintsOpen ? (
          <div className="mt-3 grid gap-3 rounded-md border border-line bg-white/70 p-3 sm:grid-cols-2">
            {mode === "open_discovery" ? (
              <>
                <HintInput label="Containing author" value={containingAuthor} onChange={setContainingAuthor} />
                <HintInput label="Containing work" value={containingWork} onChange={setContainingWork} />
              </>
            ) : null}
            <HintInput label="Period hint" value={periodHint} onChange={setPeriodHint} />
            <HintInput label="Domain hint" value={domainHint} onChange={setDomainHint} />
            <HintInput label="Language hint" value={languageHint} onChange={setLanguageHint} />
            <HintInput label="Library scope" value={libraryId} onChange={setLibraryId} />
            {mode === "open_discovery" ? (
              <>
                <label className="flex items-center gap-2 rounded-md bg-paper px-3 py-2 text-sm text-ink/75">
                  <input
                    checked={enableWebResearch}
                    onChange={(event) => setEnableWebResearch(event.target.checked)}
                    type="checkbox"
                  />
                  Enable web research
                </label>
                <label className="flex items-center gap-2 rounded-md bg-paper px-3 py-2 text-sm text-ink/75">
                  <input
                    checked={allowSourceDownloadSuggestions}
                    onChange={(event) => setAllowSourceDownloadSuggestions(event.target.checked)}
                    type="checkbox"
                  />
                  Suggest source downloads
                </label>
                <label className="flex items-center gap-2 rounded-md bg-paper px-3 py-2 text-sm text-ink/75">
                  <input
                    checked={autoDownloadSources}
                    onChange={(event) => setAutoDownloadSources(event.target.checked)}
                    type="checkbox"
                  />
                  Auto-download public sources
                </label>
              </>
            ) : (
              <div className="rounded-md bg-paper px-3 py-2 text-sm leading-6 text-ink/70 sm:col-span-2">
                Library Mode disables web discovery and searches only the selected OCR/indexed library scope.
              </div>
            )}
            <label className="block text-sm font-medium text-ink/70">
              OCR mode
              <select
                className="mt-1 w-full rounded-md border border-line bg-white px-3 py-2 outline-none focus:border-copper"
                value={ocrMode}
                onChange={(event) => setOcrMode(event.target.value as "full" | "text_layer_first" | "skip")}
              >
                <option value="full">Full OCR first</option>
                <option value="text_layer_first">Text layer first</option>
                <option value="skip">Skip OCR</option>
              </select>
            </label>
          </div>
        ) : null}
        {error ? <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}
        <button
          className="mt-5 flex min-h-11 items-center gap-2 rounded-md bg-umber px-4 py-2 text-sm font-semibold text-paper disabled:opacity-60"
          disabled={loading || Boolean(coverage.meta?.read_only) || !juryStatus.research_enabled}
          onClick={submit}
        >
          {loading ? <Loader2 className="animate-spin" size={18} /> : <Play size={18} />}
          Start investigation
          <ArrowRight size={17} />
        </button>
      </div>
      <div className="rounded-md border border-line bg-white/90 p-5 shadow-soft">
        <div className="mb-4 text-sm font-semibold text-sage">Demo examples</div>
        <div className="space-y-3">
          {examples.map((example) => (
            <button
              key={example.title}
              className="w-full rounded-md border border-line p-3 text-left hover:border-copper hover:bg-paper"
              onClick={() => {
                setPassage(example.passage);
                setContext(example.context);
                setDomainHint(example.domainHint);
                setLibraryId(example.libraryId);
              }}
            >
              <div className="mb-2 text-sm font-semibold">{example.title}</div>
              <div className="arabic text-base text-ink/75">{example.passage}</div>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function HintInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-sm font-medium text-ink/70">
      {label}
      <input
        className="mt-1 w-full rounded-md border border-line bg-white px-3 py-2 outline-none focus:border-copper"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
