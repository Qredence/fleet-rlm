import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetApiClient } from "./fleet-api-client.js";
import { FleetSseAgent } from "./fleet-sse-agent.js";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("FleetSseAgent", () => {
  it("forwards the latest user text and exposes UI SSE parts as fullStream", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(
          [
            'data: {"type":"start","messageId":"run-id"}\n\n',
            'data: {"type":"text-start","id":"text-1"}\n\n',
            'data: {"type":"text-delta","id":"text-1","delta":"Hello"}\n\n',
            'data: {"type":"text-end","id":"text-1"}\n\n',
            'data: {"type":"finish","finishReason":"stop"}\n\n',
            "data: [DONE]\n\n",
          ].join(""),
          { headers: { "x-vercel-ai-ui-message-stream": "v1" } },
        ),
      );
    const agent = new FleetSseAgent(
      new FleetApiClient({ baseUrl: "http://fleet.test" }),
      "session-id",
    );

    const result = await agent.stream({
      prompt: [{ role: "user", content: "Hello Fleet" }],
      options: undefined,
    });

    const parts = await collect(result.fullStream);
    expect(parts.map((part) => part.type)).toEqual([
      "start",
      "text-start",
      "text-delta",
      "text-end",
      "finish",
    ]);
    expect(parts.find((part) => part.type === "text-delta")).toMatchObject({ text: "Hello" });
  });

  it("consumes a server error through finish:error and DONE without rendering it twice", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(
          [
            'data: {"type":"start","messageId":"run-id"}\n\n',
            'data: {"type":"error","errorText":"Turn timed out"}\n\n',
            'data: {"type":"finish","finishReason":"error"}\n\n',
            "data: [DONE]\n\n",
          ].join(""),
          { headers: { "x-vercel-ai-ui-message-stream": "v1" } },
        ),
      );
    const agent = new FleetSseAgent(
      new FleetApiClient({ baseUrl: "http://fleet.test" }),
      "session-id",
    );

    const result = await agent.stream({
      prompt: [{ role: "user", content: "long task" }],
      options: undefined,
    });

    await expect(collect(result.fullStream)).resolves.toEqual([
      { type: "start" },
      { type: "error", error: "Turn timed out" },
    ]);
  });

  it("does not emit a visible error for an empty internal prompt", async () => {
    const agent = new FleetSseAgent(
      new FleetApiClient({ baseUrl: "http://fleet.test" }),
      "session-id",
    );
    const result = await agent.stream({ messages: [], options: undefined });

    await expect(collect(result.fullStream)).resolves.toEqual([]);
  });

  it("rejects duplicate DONE and post-DONE frames", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(
          [
            'data: {"type":"start","messageId":"run-id"}\n\n',
            'data: {"type":"finish","finishReason":"stop"}\n\n',
            "data: [DONE]\n\n",
            "data: [DONE]\n\n",
          ].join(""),
          { headers: { "x-vercel-ai-ui-message-stream": "v1" } },
        ),
      );
    const agent = new FleetSseAgent(
      new FleetApiClient({ baseUrl: "http://fleet.test" }),
      "session-id",
    );
    const result = await agent.stream({
      prompt: [{ role: "user", content: "hello" }],
      options: undefined,
    });
    await expect(collect(result.fullStream)).resolves.toEqual([
      { type: "start" },
      expect.objectContaining({ type: "finish", finishReason: "stop" }),
      { type: "error", error: "Fleet API emitted duplicate [DONE] markers" },
    ]);
  });
});

async function collect<T>(values: AsyncIterable<T>): Promise<T[]> {
  const result: T[] = [];
  for await (const value of values) {
    result.push(value);
  }
  return result;
}
