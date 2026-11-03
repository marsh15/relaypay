/**
 * Reads a request body with a byte cap. Bytes are counted as each chunk arrives, so a missing,
 * wrong or chunked Content-Length cannot bypass the cap. Returns null when the body is over the
 * cap; the stream is cancelled at that point and nothing past the cap is buffered.
 */
export async function readBoundedBody(
  stream: ReadableStream<Uint8Array> | null,
  declaredLength: string | null,
  maxBytes: number,
): Promise<Uint8Array<ArrayBuffer> | null> {
  const declared =
    declaredLength === null || !/^\d+$/.test(declaredLength) ? null : Number(declaredLength);
  if (declared !== null && declared > maxBytes) {
    await stream?.cancel();
    return null;
  }
  if (!stream) return new Uint8Array(0);
  const reader = stream.getReader();
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
  return buffer.subarray(0, length);
}
