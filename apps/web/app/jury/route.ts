import {NextRequest, NextResponse} from "next/server";
import {createJurySession, JURY_COOKIE, juryAccessEnabled, verifyAccessCode} from "@/lib/juryAccess";

function firstHeader(value: string | null): string | null {
  return value?.split(",")[0]?.trim() || null;
}

function publicOrigin(request: NextRequest): string {
  const configured = process.env.HERMENEUT_WEB_BASE_URL;
  if (configured) {
    return configured.replace(/\/$/, "");
  }
  const forwardedHost = firstHeader(request.headers.get("x-forwarded-host"));
  const forwardedProto = firstHeader(request.headers.get("x-forwarded-proto")) ?? "https";
  if (forwardedHost && !forwardedHost.startsWith("0.0.0.0")) {
    return `${forwardedProto}://${forwardedHost}`;
  }
  const host = request.headers.get("host");
  if (host && !host.startsWith("0.0.0.0")) {
    return `${forwardedProto}://${host}`;
  }
  return new URL(request.url).origin;
}

export function GET(request: NextRequest) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code") ?? "";
  if (!juryAccessEnabled()) {
    return NextResponse.json({error: "jury_access_not_configured"}, {status: 503});
  }
  if (!verifyAccessCode(code)) {
    return NextResponse.json({error: "invalid_jury_access_code"}, {status: 401});
  }
  const session = createJurySession();
  if (!session) {
    return NextResponse.json({error: "jury_session_not_configured"}, {status: 503});
  }
  const response = NextResponse.redirect(new URL("/?jury=active", publicOrigin(request)));
  response.cookies.set(JURY_COOKIE, session.value, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: session.maxAge,
  });
  return response;
}
