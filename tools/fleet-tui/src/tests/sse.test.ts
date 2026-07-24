import { describe, expect, it } from "vitest";

import { parseSSE, parseUIChunk } from "../sse.js";

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

describe("Fleet SSE parser", () => {
  it("handles fragmented data frames and the terminal marker", async () => {
    const body = streamFrom([
      'data: {"type":"text-delta","id":"text-1",',
      '"delta":"hello"}\n\n',
      "data: [DONE]\n\n",
    ]);

    await expect(collect(parseSSE(body))).resolves.toEqual([
      '{"type":"text-delta","id":"text-1","delta":"hello"}',
      "[DONE]",
    ]);
  });

  it("consumes a final unterminated frame instead of dropping the body tail", async () => {
    const body = streamFrom(['data: {"type":"finish","finishReason":"stop"}']);
    await expect(collect(parseSSE(body))).resolves.toEqual([
      '{"type":"finish","finishReason":"stop"}',
    ]);
  });

  it("ignores heartbeat comment frames without dropping adjacent data", async () => {
    const body = streamFrom([
      ": ping\n\n",
      'data: {"type":"start","messageId":"run-1","messageMetadata":{}}\n\n',
      ": ping\n\n",
      "data: [DONE]\n\n",
    ]);

    await expect(collect(parseSSE(body))).resolves.toEqual([
      '{"type":"start","messageId":"run-1","messageMetadata":{}}',
      "[DONE]",
    ]);
  });

  it("rejects malformed UI chunks", () => {
    expect(() => parseUIChunk("[]")).toThrow("invalid AI SDK UI stream chunk");
    expect(() => parseUIChunk('{"type":"data-unknown"}')).toThrow("invalid AI SDK UI stream chunk");
  });
});

async function collect<T>(values: AsyncIterable<T>): Promise<T[]> {
  const result: T[] = [];
  for await (const value of values) {
    result.push(value);
  }
  return result;
}
