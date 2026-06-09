import {NextRequest, NextResponse} from "next/server";
import {hasJuryAccess, juryAccessEnabled} from "@/lib/juryAccess";

export function GET(request: NextRequest) {
  const jury = juryAccessEnabled() && hasJuryAccess(request);
  return NextResponse.json({
    access: jury ? "jury" : "public",
    research_enabled: jury,
    operator_enabled: jury,
  });
}
