import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { Database } from "lucide-react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hermeneut",
  description: "Evidence-first research agent for ambiguous references in classical texts.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-line bg-paper/95 shadow-insetLine">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-5 py-3">
            <Link href="/" className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center overflow-hidden rounded-md border border-line bg-vellum">
                <Image
                  src="/hermeneut-logo.png"
                  alt="Hermeneut"
                  width={48}
                  height={48}
                  className="h-full w-full object-cover"
                  priority
                />
              </div>
              <div>
                <div className="text-lg font-semibold tracking-normal text-umber">Hermeneut</div>
                <div className="text-xs text-ink/60">Evidence-first textual research</div>
              </div>
            </Link>
            <nav className="flex items-center gap-2 text-sm">
              <Link className="flex items-center gap-2 rounded-md px-3 py-2 text-ink/75 hover:bg-white" href="/library">
                <Database size={16} />
                Library
              </Link>
            </nav>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
