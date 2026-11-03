/**
 * Reads a request body with a byte cap, counting bytes as each chunk arrives. A declared
 * Content-Length over the cap is refused before any read. Returns null when the body is over the
 * cap; the stream is cancelled and nothing past the cap is buffered.
 */
export async function readBoundedBody(
  request: Request,
  maxBytes: number,
): Promise<ArrayBuffer | null> {
  const declaredLength = request.headers.get("content-length");
  const declared =
    declaredLength === null || !/^\d+$/.test(declaredLength) ? null : Number(declaredLength);
  if (declared !== null && declared > maxBytes) {
    await request.body?.cancel();
    return null;
  }
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  let buffer = new Uint8Array(0);
  let length = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (length + value.byteLength > maxBytes) {
      await reader.cancel();
      return null;
    }
    if (length + value.byteLength > buffer.byteLength) {
      // Doubling keeps the copy count logarithmic; the capacity never exceeds the cap.
      const grown = new Uint8Array(
        Math.min(maxBytes, Math.max(buffer.byteLength * 2, length + value.byteLength)),
      );
      grown.set(buffer.subarray(0, length));
      buffer = grown;
    }
    buffer.set(value, length);
    length += value.byteLength;
  }
  return buffer.slice(0, length).buffer;
}
