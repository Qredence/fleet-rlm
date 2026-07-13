import { describe, expect, it } from "vitest";

import { parseSSE, parseUIChunk, toTextStreamPart } from "./sse.js";

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

  it("maps renderable UI chunks and ignores Fleet data parts", () => {
    const textChunk = parseUIChunk('{"type":"text-delta","id":"t","delta":"hi"}');
    const artifactChunk = parseUIChunk('{"type":"data-artifact","data":{}}');
    expect(textChunk).not.toBe("[DONE]");
    expect(artifactChunk).not.toBe("[DONE]");
    expect(toTextStreamPart(textChunk as Exclude<typeof textChunk, "[DONE]">)).toEqual({
      type: "text-delta",
      id: "t",
      text: "hi",
    });
    expect(toTextStreamPart(artifactChunk as Exclude<typeof artifactChunk, "[DONE]">)).toBeUndefined();
  });

  it("rejects malformed UI chunks", () => {
    expect(() => parseUIChunk("[]")).toThrow("invalid AI SDK UI stream chunk");
  });
});

async function collect<T>(values: AsyncIterable<T>): Promise<T[]> {
  const result: T[] = [];
  for await (const value of values) {
    result.push(value);
  }
  return result;
}
