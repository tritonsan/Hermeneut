import { RunConsole } from "@/components/RunConsole";
import { getHealth } from "@/lib/api";
import Image from "next/image";

export default async function HomePage() {
  const health = await getHealth().catch(() => null);
  const live = health?.elastic === "connected";
  return (
    <main className="mx-auto max-w-7xl px-5 py-8">
      <section className="mb-6 overflow-hidden rounded-md border border-night/15 bg-night shadow-board">
        <div className="grid gap-px bg-white/10 lg:grid-cols-[1fr_300px]">
          <div className="subtle-grid bg-mist p-5 text-ink sm:p-6">
            <p className="text-sm font-semibold uppercase text-sage">Elastic evidence cockpit</p>
            <h1 className="mt-2 max-w-4xl text-3xl font-semibold tracking-normal text-night sm:text-4xl">
              Trace references by location, quote, source chain, and verification status.
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-ink/70">
              Library Mode searches indexed evidence. Open Discovery resolves containing-layer and citation-chain sources, OCRs trusted targets, then promotes only textual evidence.
            </p>
          </div>
          <div className="bg-night p-5 text-white">
            <div className="flex items-center gap-3">
              <div className="h-14 w-14 overflow-hidden rounded-md border border-white/15 bg-white/10">
                <Image
                  src="/hermeneut-logo.png"
                  alt="Hermeneut mark"
                  width={56}
                  height={56}
                  className="h-full w-full object-cover"
                  priority
                />
              </div>
              <div>
                <div className="text-sm font-semibold text-white">Hermeneut</div>
                <div className="text-xs text-white/55">Evidence-first textual research</div>
              </div>
            </div>
            <div className="mt-4">
              <span className={live ? "inline-flex rounded-md bg-sage px-3 py-2 text-xs font-semibold text-white" : "inline-flex rounded-md bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800"}>
                {live ? "Live Elastic" : "Backup Preview"}
              </span>
              <p className="mt-2 text-xs leading-5 text-white/55">{live ? "Research and operator workflows are available." : "Read-only catalog browsing. Research resumes when Live Elastic is restored."}</p>
            </div>
          </div>
        </div>
      </section>
      <RunConsole />
    </main>
  );
}
