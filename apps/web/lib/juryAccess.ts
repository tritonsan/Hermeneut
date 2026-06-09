import {createHash, createHmac, timingSafeEqual} from "crypto";
import type {NextRequest} from "next/server";

export const JURY_COOKIE = "hermeneut_jury_session";

function maxAgeSeconds(): number {
  const configured = Number(process.env.JURY_SESSION_MAX_AGE_SECONDS ?? 172_800);
  return Number.isFinite(configured) && configured > 0 ? configured : 172_800;
}

function sessionSecret(): string | null {
  return process.env.JURY_SESSION_SECRET ?? process.env.JURY_PROXY_TOKEN ?? process.env.JURY_ACCESS_CODE_HASH ?? null;
}

function equalText(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

function sign(payload: string): string | null {
  const secret = sessionSecret();
  if (!secret) return null;
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

export function verifyAccessCode(code: string): boolean {
  const expected = process.env.JURY_ACCESS_CODE_HASH;
  if (!expected) return false;
  const actual = createHash("sha256").update(code.trim()).digest("hex");
  return equalText(actual, expected.trim().toLowerCase());
}

export function createJurySession(): {value: string; maxAge: number} | null {
  const now = Math.floor(Date.now() / 1000);
  const maxAge = maxAgeSeconds();
  const exp = now + maxAge;
  const payload = `${now}.${exp}`;
  const signature = sign(payload);
  if (!signature) return null;
  return {value: `${payload}.${signature}`, maxAge};
}

export function hasJuryAccess(request: NextRequest): boolean {
  const value = request.cookies.get(JURY_COOKIE)?.value;
  if (!value) return false;
  const parts = value.split(".");
  if (parts.length !== 3) return false;
  const [iat, exp, signature] = parts;
  const expected = sign(`${iat}.${exp}`);
  if (!expected || !equalText(signature, expected)) return false;
  const expiresAt = Number(exp);
  return Number.isFinite(expiresAt) && expiresAt > Math.floor(Date.now() / 1000);
}

export function juryAccessEnabled(): boolean {
  return process.env.JURY_ACCESS_ENABLED === "true" && Boolean(process.env.JURY_PROXY_TOKEN);
}
