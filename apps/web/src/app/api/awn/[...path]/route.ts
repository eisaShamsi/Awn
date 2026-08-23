import "server-only";

import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const MAX_BODY_BYTES = 1_000_000;
const MAX_RESPONSE_BYTES = 5_000_000;
const UPSTREAM_TIMEOUT_MS = 15_000;
const SAFE_SEGMENT = /^[a-zA-Z0-9_-]{1,100}$/;

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

function isAllowedPath(path: string[]): boolean {
  if (path.length === 1) return path[0] === "setup" || path[0] === "workspaces";
  if (path[0] !== "workspaces" || path.length < 2) return false;
  if (path.length === 2) return true;
  if (path[2] === "tasks") return path.length === 3 || path.length === 4;
  if (path[2] !== "conversations") return false;
  if (path.length === 3 || path.length === 4) return true;
  if (path[4] === "messages") return path.length === 5;
  if (path[4] !== "runs") return false;
  return (
    path.length === 5 ||
    path.length === 6 ||
    (path.length === 7 &&
      (path[6] === "steps" || path[6] === "approvals" || path[6] === "tool-calls")) ||
    (path.length === 9 && path[6] === "approvals" && path[8] === "decision")
  );
}

function upstreamUrl(path: string[]): URL | null {
  if (path.length === 0 || path.some((segment) => !SAFE_SEGMENT.test(segment))) {
    return null;
  }

  let upstreamPath: string;
  if (path.length === 1 && (path[0] === "health" || path[0] === "ready")) {
    upstreamPath = `/${path[0]}`;
  } else if (isAllowedPath(path)) {
    upstreamPath = `/api/v1/${path.join("/")}`;
  } else {
    return null;
  }

  try {
    const baseUrl = new URL(process.env.AWN_API_URL ?? "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(baseUrl.protocol) || baseUrl.username || baseUrl.password) {
      return null;
    }
    return new URL(upstreamPath, baseUrl);
  } catch {
    return null;
  }
}

function exceedsDeclaredLimit(value: string | null, limit: number): boolean {
  if (value === null) return false;
  return !/^\d+$/.test(value) || Number(value) > limit;
}

async function readBoundedBody(request: NextRequest): Promise<string | null> {
  if (request.body === null) return "";

  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let total = 0;
  let body = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    body += decoder.decode(value, { stream: true });
  }

  return body + decoder.decode();
}

async function proxy(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  const { path } = await context.params;
  const target = upstreamUrl(path);
  if (target === null) {
    return NextResponse.json({ detail: "مسار غير مسموح" }, { status: 404 });
  }

  if (exceedsDeclaredLimit(request.headers.get("content-length"), MAX_BODY_BYTES)) {
    return NextResponse.json({ detail: "حجم الطلب أكبر من الحد المسموح" }, { status: 413 });
  }

  let body: string | undefined;
  if (request.method === "POST" || request.method === "PATCH") {
    if (!request.headers.get("content-type")?.toLowerCase().includes("application/json")) {
      return NextResponse.json({ detail: "نوع المحتوى يجب أن يكون JSON" }, { status: 415 });
    }
    const boundedBody = await readBoundedBody(request);
    if (boundedBody === null) {
      return NextResponse.json({ detail: "حجم الطلب أكبر من الحد المسموح" }, { status: 413 });
    }
    body = boundedBody;
  }

  try {
    const response = await fetch(target, {
      method: request.method,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      headers: {
        Accept: "application/json",
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
    });
    if (exceedsDeclaredLimit(response.headers.get("content-length"), MAX_RESPONSE_BYTES)) {
      return NextResponse.json({ detail: "استجابة الخدمة أكبر من الحد المسموح" }, { status: 502 });
    }
    const responseBody = await response.arrayBuffer();
    if (responseBody.byteLength > MAX_RESPONSE_BYTES) {
      return NextResponse.json({ detail: "استجابة الخدمة أكبر من الحد المسموح" }, { status: 502 });
    }
    const upstreamContentType = response.headers.get("content-type")?.toLowerCase();
    return new NextResponse(responseBody, {
      status: response.status,
      headers: {
        "Content-Type": upstreamContentType?.includes("json")
          ? upstreamContentType
          : "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "خدمة عَوْن الخلفية غير متاحة" },
      { status: 503 },
    );
  }
}

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
