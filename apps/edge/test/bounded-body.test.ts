import { describe, expect, it } from "vitest";
import { readBoundedBody } from "../src/bounded-body";

// Enqueues every chunk up front and calls onCancel if the consumer cancels the stream.
function streamOf(chunks: Uint8Array[], onCancel: () => void = () => undefined): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) { for (const chunk of chunks) controller.enqueue(chunk); controller.close(); },
    cancel: onCancel,
  });
}
// Only the two fields the reader touches, so the tests do not depend on how a runtime treats
// a Content-Length header that was set on a Request built from a stream.
function requestOf(stream: ReadableStream<Uint8Array> | null, headers: Record<string, string> = {}): Request {
  return { headers: new Headers(headers), body: stream } as unknown as Request;
}
const bytes = (values: number[]): Uint8Array => new Uint8Array(values);

describe("bounded edge body reader", () => {
  it("returns the exact bytes for a body at the cap, split across chunks", async () => {
    const request = requestOf(streamOf([bytes([1, 2, 3, 4, 5]), bytes([6, 7, 8, 9, 10, 11]), bytes([12, 13, 14, 15, 16])]));
    const body = await readBoundedBody(request, 16);
    expect(body).not.toBeNull();
    expect(new Uint8Array(body as ArrayBuffer)).toEqual(bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]));
  });

  it("returns the exact bytes for a body below the cap and for an empty body", async () => {
    expect(new Uint8Array((await readBoundedBody(requestOf(streamOf([bytes([7, 8])])), 16)) as ArrayBuffer)).toEqual(bytes([7, 8]));
    const empty = await readBoundedBody(requestOf(null), 16);
    expect(empty?.byteLength).toBe(0);
  });

  it("cancels the stream and returns null on the first chunk that crosses the cap", async () => {
    let cancelled = false;
    const request = requestOf(streamOf([bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), bytes([1, 2, 3, 4, 5, 6, 7]), bytes([9])], () => { cancelled = true; }));
    expect(await readBoundedBody(request, 16)).toBeNull();
    expect(cancelled).toBe(true);
  });

  it("refuses a declared oversize body before reading and cancels it", async () => {
    let cancelled = false;
    const request = requestOf(streamOf([bytes([1])], () => { cancelled = true; }), { "content-length": "17" });
    expect(await readBoundedBody(request, 16)).toBeNull();
    expect(cancelled).toBe(true);
  });

  it("counts real chunks when Content-Length understates the body", async () => {
    const request = requestOf(streamOf([bytes(new Array(10).fill(1)), bytes(new Array(10).fill(2))]), { "content-length": "3" });
    expect(await readBoundedBody(request, 16)).toBeNull();
  });
});
