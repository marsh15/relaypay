import { describe, expect, it, vi } from "vitest";
import worker, { type Env, TenantCoordinator } from "../src/index";

function environment(overrides: Partial<Env> = {}): Env {
  const archive = new Map<string, ArrayBuffer>();
  return {
    ORIGIN_BASE_URL: "https://origin.test", ORIGIN_SIGNING_SECRET: "edge-test-secret",
    EDGE_RATE_LIMIT: "60", EDGE_REPLAY_SECONDS: "300",
    TENANT_COORDINATOR: { getByName: () => ({ fetch: async () => Response.json({ allowed: true }) }) } as never,
    WEBHOOK_ARCHIVE: {
      get: async (key: string) => { const value = archive.get(key); return value ? { arrayBuffer: async () => value } : null; },
      put: async (key: string, value: ArrayBuffer) => { archive.set(key, value); return {} as never; },
    } as never,
    WEBHOOK_QUEUE: { send: vi.fn(async () => undefined) } as never,
    ...overrides,
  };
}
const replayHeaders = { "x-relaypay-edge-timestamp": Math.floor(Date.now() / 1000).toString(), "x-relaypay-edge-replay-key": "replay-key-00000001" };

describe("RelayPay edge", () => {
  it("rejects replay and enforces a fixed tenant rate window", async () => {
    const values = new Map<string, unknown>();
    const storage = {
      get: async (key: string) => values.get(key),
      put: async (entries: Record<string, unknown>) => { for (const [key, value] of Object.entries(entries)) values.set(key, value); },
    };
    const coordinator = new TenantCoordinator({ storage } as never, environment());
    const timestamp = Math.floor(Date.now() / 1000);
    const request = (replayKey: string) => new Request("https://coordinator/admit", { method: "POST", body: JSON.stringify({ replayKey, timestamp, limit: 1, windowSeconds: 300 }) });
    expect((await coordinator.fetch(request("first-replay-key"))).status).toBe(200);
    expect((await coordinator.fetch(request("first-replay-key"))).status).toBe(409);
    expect((await coordinator.fetch(request("second-replay-key"))).status).toBe(429);
  });

  it("blocks bypass-shaped API credentials before origin", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const response = await worker.fetch(new Request("https://edge.test/api/v1/payment_intents", { headers: replayHeaders }), environment());
    expect(response.status).toBe(401); expect(fetchMock).not.toHaveBeenCalled(); fetchMock.mockRestore();
  });

  it("signs synchronous origin calls and preserves trace context", async () => {
    const traceparent = `00-${"1".repeat(32)}-${"2".repeat(16)}-01`;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => new Response(JSON.stringify({ traceparent: new Headers(init?.headers).get("traceparent"), signature: new Headers(init?.headers).get("x-relaypay-edge-signature") }), { status: 200 }));
    const response = await worker.fetch(new Request("https://edge.test/api/v1/payment_intents", { headers: { ...replayHeaders, authorization: `Bearer rpk_test_abcdefgh.${"x".repeat(32)}`, traceparent } }), environment());
    expect(response.status).toBe(200); expect(await response.json()).toMatchObject({ traceparent, signature: expect.stringMatching(/^[0-9a-f]{64}$/) }); fetchMock.mockRestore();
  });

  it("archives immutable digest-addressed webhook bytes and queues only a reference", async () => {
    const env = environment(); const body = '{"synthetic":true}';
    const response = await worker.fetch(new Request(`https://edge.test/api/inbound/v1/connectors/con_${"a".repeat(32)}/events`, { method: "POST", body, headers: { ...replayHeaders, "x-provider-event-id": "evt-1", "x-provider-timestamp": "1760000000", "x-provider-signature": `v1=${"b".repeat(64)}` } }), env);
    expect(response.status).toBe(202); const result = await response.json<{ digest: string }>(); expect(result.digest).toMatch(/^[0-9a-f]{64}$/); expect(env.WEBHOOK_QUEUE.send).toHaveBeenCalledWith(expect.objectContaining({ digest: result.digest, eventId: "evt-1" }));
  });

  it("retries queue delivery on origin outage", async () => {
    const env = environment(); const bytes = new TextEncoder().encode("{}"); const digestBytes = await crypto.subtle.digest("SHA-256", bytes); const digest = [...new Uint8Array(digestBytes)].map((v) => v.toString(16).padStart(2, "0")).join(""); await env.WEBHOOK_ARCHIVE.put(`sha256/${digest}`, bytes);
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new Error("origin down")); const retry = vi.fn(); const ack = vi.fn();
    await worker.queue({ messages: [{ body: { connectorId: `con_${"a".repeat(32)}`, digest, eventId: "evt-1", providerTimestamp: "1760000000", providerSignature: `v1=${"b".repeat(64)}`, traceparent: `00-${"1".repeat(32)}-${"2".repeat(16)}-01` }, retry, ack }] } as never, env);
    expect(retry).toHaveBeenCalledWith({ delaySeconds: 2 }); expect(ack).not.toHaveBeenCalled(); vi.restoreAllMocks();
  });
});
