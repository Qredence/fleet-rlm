import { describe, expect, it, vi } from "vitest";

import { FleetApiError, type FleetApiClient } from "./fleet-api-client.js";
import { streamFleetTurn } from "./fleet-turn-stream.js";

function response(...frames: string[]): Response {
  return new Response(frames.map((frame) => `data: ${frame}\n\n`).join(""), {
    headers: { "x-vercel-ai-ui-message-stream": "v1" },
  });
}

function client(streamTurn: FleetApiClient["streamTurn"]): FleetApiClient {
  return { streamTurn } as FleetApiClient;
}

async function collect(options: Parameters<typeof streamFleetTurn>[0]) {
  const chunks = [];
  for await (const chunk of streamFleetTurn(options)) chunks.push(chunk);
  return chunks;
}

describe("streamFleetTurn", () => {
  it("opens once and yields typed chunks while consuming exactly one DONE", async () => {
    const streamTurn = vi
      .fn()
      .mockResolvedValue(
        response(
          '{"type":"start","messageId":"run-1","messageMetadata":{}}',
          '{"type":"text-delta","id":"text-1","delta":"hi"}',
          '{"type":"finish","finishReason":"stop"}',
          "[DONE]",
        ),
      );

    await expect(
      collect({ client: client(streamTurn), sessionId: "session-1", message: "hello" }),
    ).resolves.toEqual([
      { type: "start", messageId: "run-1", messageMetadata: {} },
      { type: "text-delta", id: "text-1", delta: "hi" },
      { type: "finish", finishReason: "stop" },
    ]);
  });

  it("retries one status-0 open with the same idempotency key", async () => {
    const streamTurn = vi
      .fn()
      .mockRejectedValueOnce(new FleetApiError(0, "offline"))
      .mockResolvedValueOnce(
        response(
          '{"type":"start","messageId":"run-1","messageMetadata":{}}',
          '{"type":"finish","finishReason":"stop"}',
          "[DONE]",
        ),
      );

    await collect({
      client: client(streamTurn),
      sessionId: "session-1",
      message: "hello",
      idempotencyKey: "same-key",
    });

    expect(streamTurn).toHaveBeenCalledTimes(2);
    expect(streamTurn.mock.calls[0]?.[0].idempotencyKey).toBe("same-key");
    expect(streamTurn.mock.calls[1]?.[0].idempotencyKey).toBe("same-key");
  });

  it.each([
    ["missing start", ['{"type":"finish","finishReason":"stop"}', "[DONE]"]],
    [
      "duplicate start",
      [
        '{"type":"start","messageId":"run-1","messageMetadata":{}}',
        '{"type":"start","messageId":"run-2","messageMetadata":{}}',
      ],
    ],
    [
      "finish error without error",
      [
        '{"type":"start","messageId":"run-1","messageMetadata":{}}',
        '{"type":"finish","finishReason":"error"}',
      ],
    ],
    [
      "chunk after terminal",
      [
        '{"type":"start","messageId":"run-1","messageMetadata":{}}',
        '{"type":"finish","finishReason":"stop"}',
        '{"type":"text-delta","id":"t","delta":"late"}',
      ],
    ],
    [
      "missing DONE",
      [
        '{"type":"start","messageId":"run-1","messageMetadata":{}}',
        '{"type":"finish","finishReason":"stop"}',
      ],
    ],
    [
      "duplicate DONE",
      [
        '{"type":"start","messageId":"run-1","messageMetadata":{}}',
        '{"type":"finish","finishReason":"stop"}',
        "[DONE]",
        "[DONE]",
      ],
    ],
  ])("rejects %s", async (_label, frames) => {
    await expect(
      collect({
        client: client(vi.fn().mockResolvedValue(response(...frames))),
        sessionId: "session-1",
        message: "hello",
      }),
    ).rejects.toThrow();
  });
});
