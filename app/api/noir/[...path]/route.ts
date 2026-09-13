import { env } from "cloudflare:workers";
import type { NextRequest } from "next/server";

const rules: Array<[string, RegExp]> = [
  ["GET", /^v1\/health$/],
  ["GET", /^v1\/auth\/me$/],
  ["POST", /^v1\/uploads$/],
  ["PATCH", /^v1\/uploads\/[a-f0-9]{32}$/],
  ["POST", /^v1\/uploads\/[a-f0-9]{32}\/complete$/],
  ["GET", /^v1\/jobs\/[a-f0-9]{16}$/],
  ["GET", /^v1\/projects\/[a-f0-9]{16}$/],
  ["GET", /^v1\/projects\/[a-f0-9]{16}\/events$/],
  ["POST", /^v1\/projects\/[a-f0-9]{16}\/workflow\/(prepare|finish)$/],
  ["GET", /^v1\/projects\/[a-f0-9]{16}\/plans\/[a-f0-9]{16}$/],
  ["GET", /^v1\/projects\/[a-f0-9]{16}\/patches\/[a-f0-9]{16}$/],
  ["GET", /^v1\/projects\/[a-f0-9]{16}\/patches\/[a-f0-9]{16}\/diff$/],
  ["GET", /^v1\/projects\/[a-f0-9]{16}\/builds$/],
  ["GET", /^v1\/projects\/[a-f0-9]{16}\/builds\/[a-f0-9]{16}\/download$/],
];

const forwardedResponseHeaders = [
  "content-disposition",
  "content-length",
  "content-type",
  "location",
  "upload-offset",
];

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  if (!rules.some(([method, pattern]) => method === request.method && pattern.test(route))) {
    return Response.json({ detail: "Route not available" }, { status: 404 });
  }

  const runtime = env as unknown as Record<string, string | undefined>;
  const baseUrl = (runtime.NOIR_BACKEND_URL || process.env.NOIR_BACKEND_URL)?.replace(/\/$/, "");
  const token = runtime.NOIR_API_TOKEN || process.env.NOIR_API_TOKEN;
  if (!baseUrl || (!token && route !== "v1/health")) {
    return Response.json({ detail: "NOIR backend is not configured" }, { status: 503 });
  }

  const target = new URL(`${baseUrl}/${route}`);
  for (const key of ["authorized", "artifact", "after", "limit"]) {
    const value = request.nextUrl.searchParams.get(key);
    if (value !== null) target.searchParams.set(key, value);
  }

  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  for (const key of ["content-type", "idempotency-key", "upload-offset"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }

  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" ? undefined : await request.arrayBuffer(),
    redirect: "manual",
  });
  const responseHeaders = new Headers({ "Cache-Control": "no-store" });
  for (const key of forwardedResponseHeaders) {
    const value = upstream.headers.get(key);
    if (value) responseHeaders.set(key, value);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
