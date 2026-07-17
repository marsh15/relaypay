import type { NextRequest } from "next/server";

const backendUrl = process.env.INTERNAL_API_BASE_URL ?? "http://localhost:8000";
const forwardedRequestHeaders = ["content-type", "x-csrf-token", "x-request-id"];
const forwardedResponseHeaders = ["content-type", "retry-after", "set-cookie", "x-request-id"];

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const headers = new Headers();
  for (const name of forwardedRequestHeaders) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const cookie = request.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const response = await fetch(`${backendUrl}/${path.join("/")}${request.nextUrl.search}`, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.bytes(),
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
