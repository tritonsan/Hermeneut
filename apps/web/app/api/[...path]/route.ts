import { NextRequest, NextResponse } from "next/server";
import {hasJuryAccess, juryAccessEnabled} from "@/lib/juryAccess";

const API_BASE_URL =
  process.env.HERMENEUT_API_BASE_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://localhost:8000";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const pathName = path.join("/");
  const jury = juryAccessEnabled() && hasJuryAccess(request);
  if (!jury && requiresJuryAccess(request, pathName)) {
    return NextResponse.json(
      {
        detail: {
          code: "jury_access_required",
          message: "Search and operator actions are available through the jury access link.",
        },
      },
      {status: 403},
    );
  }
  const target = new URL(`/api/${pathName}`, API_BASE_URL);
  target.search = request.nextUrl.search;

  const headers = new Headers(request.headers);
  headers.delete("host");
  if (process.env.NODE_ENV === "production") headers.delete("authorization");
  headers.delete("x-hermeneut-jury-token");
  if (jury && process.env.JURY_PROXY_TOKEN) {
    headers.set("x-hermeneut-jury-token", process.env.JURY_PROXY_TOKEN);
  }

  const response = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
  });

  return new Response(response.body, {
    status: response.status,
    headers: response.headers,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;

function requiresJuryAccess(request: NextRequest, pathName: string): boolean {
  const method = request.method.toUpperCase();
  if (pathName === "library/search") {
    return Boolean(request.nextUrl.searchParams.get("q")?.trim());
  }
  if (pathName === "catalog/search") {
    return method !== "GET" && request.nextUrl.searchParams.has("endpoint_url");
  }
  if (pathName.startsWith("runs")) {
    return method !== "GET" && method !== "HEAD";
  }
  if (pathName.startsWith("catalog-curator")) return true;
  if (pathName.startsWith("sources")) {
    return method !== "GET" && method !== "HEAD" || /\/pages\/[^/]+$/.test(pathName);
  }
  if (pathName.startsWith("libraries") && method !== "GET" && method !== "HEAD") return true;
  if (pathName === "catalog/harvest") return true;
  return method !== "GET" && method !== "HEAD";
}
