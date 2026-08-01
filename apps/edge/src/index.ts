export interface Env {
  ORIGIN_BASE_URL: string;
  ORIGIN_SIGNING_SECRET: string;
  EDGE_RATE_LIMIT: string;
  EDGE_REPLAY_SECONDS: string;
  TENANT_COORDINATOR: DurableObjectNamespace;
  WEBHOOK_ARCHIVE: R2Bucket;
  WEBHOOK_QUEUE: Queue<WebhookReference>;
}

interface WebhookReference {
  connectorId: string;
  digest: string;
  eventId: string;
  providerTimestamp: string;
  providerSignature: string;
  traceparent: string;
}

const encoder = new TextEncoder();
const API_KEY = /^rpk_(?:test|live_like)_[a-z2-7]{8}\.[A-Za-z0-9_-]{20,128}$/;
const TRACEPARENT = /^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$/;
const CONNECTOR = /^con_[0-9a-f]{32}$/;
const MAX_BODY = 1024 * 1024;
const metrics = new Map<string, number>();

function count(name: string): void { metrics.set(name, (metrics.get(name) ?? 0) + 1); }
function jsonError(status: number, code: string, message: string): Response {
  count(`relaypay_edge_requests_total{outcome="${code.toLowerCase()}"}`);
  return Response.json({ error: { code, message, details: null } }, { status, headers: securityHeaders() });
}
function securityHeaders(): HeadersInit {
  return { "cache-control": "no-store", "content-security-policy": "default-src 'none'", "x-content-type-options": "nosniff", "x-frame-options": "DENY" };
}
async function sha256(bytes: ArrayBuffer): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((value) => value.toString(16).padStart(2, "0")).join("");
}
function hex(bytes: Uint8Array): string { return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join(""); }
function validTraceparent(value: string | null): string {
  if (value && TRACEPARENT.test(value)) return value;
  const trace = new Uint8Array(16); const span = new Uint8Array(8);
  crypto.getRandomValues(trace); crypto.getRandomValues(span);
  return `00-${hex(trace)}-${hex(span)}-01`;
}
async function hmac(secret: string, value: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(value))));
}
async function originHeaders(env: Env, method: string, target: string, body: ArrayBuffer, traceparent: string): Promise<Headers> {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonceBytes = new Uint8Array(16); crypto.getRandomValues(nonceBytes);
  const nonce = hex(nonceBytes); const digest = await sha256(body);
  const canonical = ["relaypay-edge-v1", method.toUpperCase(), target, timestamp, nonce, digest, traceparent].join("\n");
  return new Headers({
    "x-relaypay-edge-key-id": "v1", "x-relaypay-edge-timestamp": timestamp,
    "x-relaypay-edge-nonce": nonce, "x-relaypay-edge-body-sha256": digest,
    "x-relaypay-edge-signature": await hmac(env.ORIGIN_SIGNING_SECRET, canonical), traceparent,
  });
}

export class TenantCoordinator {
  constructor(private readonly ctx: DurableObjectState, env: Env) { void env; }

  async fetch(request: Request): Promise<Response> {
    const input = await request.json<{ replayKey: string; timestamp: number; limit: number; windowSeconds: number }>();
    const now = Math.floor(Date.now() / 1000);
    if (!Number.isSafeInteger(input.timestamp) || Math.abs(now - input.timestamp) > input.windowSeconds) return Response.json({ allowed: false, code: "EDGE_REPLAY_WINDOW_EXCEEDED" }, { status: 401 });
    const replayKey = `replay:${input.replayKey}`;
    if (await this.ctx.storage.get(replayKey)) return Response.json({ allowed: false, code: "EDGE_REPLAY_DETECTED" }, { status: 409 });
    const bucket = Math.floor(now / 60); const rateKey = `rate:${bucket}`;
    const used = (await this.ctx.storage.get<number>(rateKey)) ?? 0;
    if (used >= input.limit) return Response.json({ allowed: false, code: "EDGE_RATE_LIMITED" }, { status: 429 });
    await this.ctx.storage.put({ [replayKey]: now, [rateKey]: used + 1 });
    return Response.json({ allowed: true });
  }
}

async function admit(request: Request, env: Env, tenant: string): Promise<Response | null> {
  const timestamp = Number(request.headers.get("x-relaypay-edge-timestamp"));
  const replayKey = request.headers.get("x-relaypay-edge-replay-key") ?? "";
  if (!/^[A-Za-z0-9_-]{16,128}$/.test(replayKey)) return jsonError(400, "EDGE_REPLAY_KEY_REQUIRED", "A bounded replay key is required");
  const response = await env.TENANT_COORDINATOR.getByName(tenant).fetch("https://coordinator/admit", { method: "POST", body: JSON.stringify({ replayKey, timestamp, limit: Number(env.EDGE_RATE_LIMIT), windowSeconds: Number(env.EDGE_REPLAY_SECONDS) }) });
  if (response.ok) return null;
  const payload = await response.json<{ code: string }>();
  return jsonError(response.status, payload.code, "Edge admission rejected the request");
}

async function synchronous(request: Request, env: Env): Promise<Response> {
  const authorization = request.headers.get("authorization") ?? "";
  const key = authorization.startsWith("Bearer ") ? authorization.slice(7).trim() : "";
  if (!API_KEY.test(key)) return jsonError(401, "EDGE_API_KEY_PREFIX_INVALID", "A valid RelayPay API-key shape is required");
  const tenant = key.split(".", 1)[0] ?? "";
  const denied = await admit(request, env, tenant); if (denied) return denied;
  const body = await request.arrayBuffer(); if (body.byteLength > MAX_BODY) return jsonError(413, "EDGE_BODY_TOO_LARGE", "Request body exceeds one MiB");
  if (body.byteLength && !request.headers.get("content-type")?.startsWith("application/json")) return jsonError(415, "EDGE_CONTENT_TYPE_INVALID", "JSON content type is required");
  const url = new URL(request.url); const target = url.pathname + url.search; const traceparent = validTraceparent(request.headers.get("traceparent"));
  const headers = new Headers(request.headers); (await originHeaders(env, request.method, target, body, traceparent)).forEach((value, name) => headers.set(name, value));
  try {
    const init: RequestInit = { method: request.method, headers, redirect: "manual" };
    if (body.byteLength) init.body = body;
    const response = await fetch(new URL(target, env.ORIGIN_BASE_URL), init);
    count("relaypay_edge_origin_requests_total{outcome=\"completed\"}"); return response;
  } catch { return jsonError(503, "EDGE_ORIGIN_UNAVAILABLE", "The origin is temporarily unavailable"); }
}

async function webhook(request: Request, env: Env, connectorId: string): Promise<Response> {
  if (!CONNECTOR.test(connectorId)) return jsonError(404, "EDGE_ROUTE_NOT_FOUND", "Resource not found");
  const eventId = request.headers.get("x-provider-event-id") ?? ""; const providerTimestamp = request.headers.get("x-provider-timestamp") ?? ""; const providerSignature = request.headers.get("x-provider-signature") ?? "";
  if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(eventId) || !/^\d{10}$/.test(providerTimestamp) || !/^v1=[0-9a-f]{64}$/.test(providerSignature)) return jsonError(400, "EDGE_WEBHOOK_SHAPE_INVALID", "Required webhook headers are invalid");
  const denied = await admit(request, env, `${connectorId}:${eventId}`); if (denied) return denied;
  const body = await request.arrayBuffer(); if (body.byteLength > MAX_BODY) return jsonError(413, "EDGE_BODY_TOO_LARGE", "Webhook exceeds one MiB");
  const digest = await sha256(body); const key = `sha256/${digest}`; const existing = await env.WEBHOOK_ARCHIVE.get(key);
  if (existing) { if (await sha256(await existing.arrayBuffer()) !== digest) return jsonError(500, "EDGE_ARCHIVE_INTEGRITY_FAILED", "Archived bytes failed integrity validation"); }
  else await env.WEBHOOK_ARCHIVE.put(key, body, { sha256: digest, customMetadata: { digest, synthetic: "true" } });
  const traceparent = validTraceparent(request.headers.get("traceparent"));
  await env.WEBHOOK_QUEUE.send({ connectorId, digest, eventId, providerTimestamp, providerSignature, traceparent });
  count("relaypay_edge_webhooks_total{outcome=\"queued\"}");
  return Response.json({ digest, queued: true }, { status: 202, headers: securityHeaders() });
}

async function forward(reference: WebhookReference, env: Env): Promise<boolean> {
  const object = await env.WEBHOOK_ARCHIVE.get(`sha256/${reference.digest}`); if (!object) return false;
  const body = await object.arrayBuffer(); if (await sha256(body) !== reference.digest) return false;
  const target = `/api/inbound/v1/connectors/${reference.connectorId}/events`; const headers = await originHeaders(env, "POST", target, body, reference.traceparent);
  headers.set("x-provider-event-id", reference.eventId); headers.set("x-provider-timestamp", reference.providerTimestamp); headers.set("x-provider-signature", reference.providerSignature); headers.set("content-type", "application/json");
  try { const response = await fetch(new URL(target, env.ORIGIN_BASE_URL), { method: "POST", headers, body, redirect: "manual" }); return response.status >= 200 && response.status < 300; } catch { return false; }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/__edge/metrics" && request.method === "GET") return new Response([...metrics].map(([name, value]) => `${name} ${value}`).join("\n") + "\n", { headers: { "content-type": "text/plain; version=0.0.4" } });
    const match = url.pathname.match(/^\/api\/inbound\/v1\/connectors\/(con_[0-9a-f]{32})\/events$/);
    if (match && request.method === "POST") return webhook(request, env, match[1] as string);
    if (url.pathname.startsWith("/api/v1/")) return synchronous(request, env);
    return jsonError(404, "EDGE_ROUTE_NOT_FOUND", "Resource not found");
  },
  async queue(batch: MessageBatch<WebhookReference>, env: Env): Promise<void> {
    for (const message of batch.messages) { if (await forward(message.body, env)) message.ack(); else message.retry({ delaySeconds: 2 }); }
  },
} satisfies ExportedHandler<Env, WebhookReference>;
