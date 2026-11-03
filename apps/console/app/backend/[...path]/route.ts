import type { NextRequest } from "next/server";

import { readBoundedBody } from "@/lib/bounded-body";

const backendUrl = process.env.INTERNAL_API_BASE_URL ?? "http://localhost:8000";
// Matches the API's body cap; a statement upload is 1 MiB plus multipart overhead.
const maxRequestBytes = 2 * 1024 * 1024;
const refusalHeaders = {
  "cache-control": "no-store",
  "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
  "referrer-policy": "no-referrer",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
};
const forwardedRequestHeaders = [
  "content-type",
  "x-csrf-token",
  "x-request-id",
  "idempotency-key",
  "x-forwarded-for",
];
const forwardedResponseHeaders = [
  "content-type", "retry-after", "set-cookie", "x-request-id", "cache-control",
];

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const headers = new Headers();
  for (const name of forwardedRequestHeaders) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const cookie = request.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await readBoundedBody(request.body, request.headers.get("content-length"), maxRequestBytes);
  if (body === null) {
    return Response.json(
      {
        error: {
          code: "REQUEST_BODY_TOO_LARGE",
          message: "Request body exceeds the size limit",
          details: { maxBytes: maxRequestBytes },
        },
      },
      { status: 413, headers: refusalHeaders },
    );
  }
  const response = await fetch(`${backendUrl}/${path.join("/")}${request.nextUrl.search}`, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
    redirect: "manual",
  });
  const responseHeaders = new Headers();
  for (const name of forwardedResponseHeaders) {
    const value = response.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  const responseBody = await response.arrayBuffer();
  return new Response(responseBody, { status: response.status, headers: responseHeaders });
}

export const GET = proxy;
export const POST = proxy;
