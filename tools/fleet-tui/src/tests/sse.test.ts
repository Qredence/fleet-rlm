import { describe, expect, it, vi } from "vitest";

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

  it("cancels an interrupted body when the consumer stops early", async () => {
    const cancel = vi.fn();
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode('data: {"type":"start","messageId":"run-1","messageMetadata":{}}\n\n'),
        );
      },
      cancel,
    });

    const iterator = parseSSE(body);
    await expect(iterator.next()).resolves.toEqual({
      done: false,
      value: '{"type":"start","messageId":"run-1","messageMetadata":{}}',
    });
    await iterator.return(undefined);

    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("normalizes malformed JSON into a stable stream error", () => {
    expect(() => parseUIChunk("{")).toThrow("invalid AI SDK UI stream chunk");
    expect(() => parseUIChunk("[]")).toThrow("invalid AI SDK UI stream chunk");
    expect(() => parseUIChunk('{"type":"data-unknown"}')).toThrow("invalid AI SDK UI stream chunk");
  });

  it("parses typed render data payloads", () => {
    const chunks = [
      { type: "data-status", data: { phase: "execution", status: "running" } },
      { type: "data-skill", data: { skill_id: "skill-1", name: "inspect", version: "1" } },
      { type: "data-rlm-code", data: { code: "print(1)", step: 1 } },
      { type: "data-rlm-output", data: { output: "1", step: 1 } },
      {
        type: "data-attachment",
        data: { attachment_id: "attachment-1", filename: "input.txt", byte_size: 2 },
      },
      { type: "data-warning", data: { message: "warning", code: null } },
      {
        type: "data-artifact",
        data: {
          artifact_id: "artifact-1",
          artifact_kind: "markdown",
          title: "report",
          media_type: "text/markdown",
          byte_size: 3,
          checksum_sha256: "a".repeat(64),
        },
      },
      { type: "data-usage", data: { usage: { iterations: 1 } } },
      {
        type: "data-structured-result",
        data: { schema_id: "answer", schema_version: "1", value: 7 },
      },
    ];

    for (const chunk of chunks) {
      expect(parseUIChunk(JSON.stringify(chunk))).toEqual(chunk);
    }
  });

  it("rejects malformed typed render data payloads", () => {
    const chunks = [
      { type: "data-status", data: { phase: "execution" } },
      { type: "data-rlm-code", data: { code: 7 } },
      { type: "data-rlm-output", data: {} },
      { type: "data-usage", data: { usage: [] } },
      { type: "data-structured-result", data: { schema_id: "answer", schema_version: "1" } },
    ];

    for (const chunk of chunks) {
      expect(() => parseUIChunk(JSON.stringify(chunk))).toThrow("invalid AI SDK UI stream chunk");
    }
  });
});

async function collect<T>(values: AsyncIterable<T>): Promise<T[]> {
  const result: T[] = [];
  for await (const value of values) {
    result.push(value);
  }
  return result;
}
