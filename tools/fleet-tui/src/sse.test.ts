import { describe, expect, it } from "vitest";

import { parseSSE, parseUIChunk, toTextStreamParts } from "./sse.js";

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

  it("maps RLM code and output into one visible tool trajectory", () => {
    const textChunk = parseUIChunk('{"type":"text-delta","id":"t","delta":"hi"}');
    const codeChunk = parseUIChunk(
      '{"type":"data-rlm-code","id":"code-1","data":{"step":3,"code":"print(1)"}}',
    );
    const outputChunk = parseUIChunk(
      '{"type":"data-rlm-output","id":"output-1","data":{"step":3,"output":"1"}}',
    );
    const statusChunk = parseUIChunk('{"type":"data-status","data":{"phase":"running"}}');
    expect(textChunk).not.toBe("[DONE]");
    expect(codeChunk).not.toBe("[DONE]");
    expect(outputChunk).not.toBe("[DONE]");
    expect(statusChunk).not.toBe("[DONE]");
    expect(toTextStreamParts(textChunk as Exclude<typeof textChunk, "[DONE]">)).toEqual([
      {
        type: "text-delta",
        id: "t",
        text: "hi",
      },
    ]);
    expect(toTextStreamParts(codeChunk as Exclude<typeof codeChunk, "[DONE]">)).toEqual([
      expect.objectContaining({
        type: "tool-call",
        toolCallId: "rlm-step-3",
        toolName: "RLM step 3",
        input: "print(1)",
      }),
    ]);
    expect(toTextStreamParts(outputChunk as Exclude<typeof outputChunk, "[DONE]">)).toEqual([
      expect.objectContaining({
        type: "tool-result",
        toolCallId: "rlm-step-3",
        output: "1",
      }),
    ]);
    expect(toTextStreamParts(statusChunk as Exclude<typeof statusChunk, "[DONE]">)).toEqual([]);
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
