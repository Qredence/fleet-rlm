import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetApiClient, FleetApiError } from "./fleet-api-client.js";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("FleetApiClient", () => {
  it("creates a session as JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "session-id", title: "New Session" }), {
        headers: { "content-type": "application/json" },
      }),
    );
    globalThis.fetch = fetchMock;

    await new FleetApiClient({ baseUrl: "http://fleet.test" }).createSession();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://fleet.test/api/sessions",
      expect.objectContaining({
        method: "POST",
        body: "{}",
        headers: expect.objectContaining({ "content-type": "application/json" }),
      }),
    );
  });

  it("sends a local-scope Fleet chat request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("data: [DONE]\n\n", {
        headers: { "x-vercel-ai-ui-message-stream": "v1" },
      }),
    );
    globalThis.fetch = fetchMock;
    const client = new FleetApiClient({ baseUrl: "http://fleet.test/" });

    await client.streamTurn({
      message: "hello",
      sessionId: "session-id",
      idempotencyKey: "turn-key",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://fleet.test/api/sessions/session-id/turns",
      expect.objectContaining({
        body: JSON.stringify({ text: "hello", attachment_ids: [] }),
        headers: expect.objectContaining({
          "content-type": "application/json",
          "idempotency-key": "turn-key",
        }),
      }),
    );
    const requestHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(requestHeaders.has("authorization")).toBe(false);
    expect(requestHeaders.has("x-fleet-user-id")).toBe(false);
    expect(requestHeaders.has("x-fleet-workspace-id")).toBe(false);
  });

  it("rejects a non-v1 SSE response", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });

    await expect(
      client.streamTurn({ message: "hello", sessionId: "session-id", idempotencyKey: "turn-key" }),
    ).rejects.toBeInstanceOf(FleetApiError);
  });

  it("retains the request correlation id on public API failures", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: "turn_unavailable", message: "Turn is unavailable" }), {
        status: 503,
        headers: { "content-type": "application/json", "x-request-id": "request-123" },
      }),
    );
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });

    const failure = await client
      .streamTurn({ message: "hello", sessionId: "session-id", idempotencyKey: "turn-key" })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(FleetApiError);
    expect(failure).toMatchObject({
      status: 503,
      message: "Turn is unavailable",
      correlationId: "request-123",
    });
  });

  it("explains how to start Fleet when the API cannot be reached", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("fetch failed"));
    const client = new FleetApiClient({ baseUrl: "http://127.0.0.1:8000" });

    await expect(client.createSession()).rejects.toThrow(
      "Cannot connect to Fleet API at http://127.0.0.1:8000 (fetch failed). Start it with: uv run fleet-rlm serve-api --port 8000",
    );
  });

  it("loads every persisted turn page", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [{ id: "one" }], next_after_sequence: 1 }), {
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [{ id: "two" }], next_after_sequence: null }), {
          headers: { "content-type": "application/json" },
        }),
      );
    globalThis.fetch = fetchMock;

    await expect(
      new FleetApiClient({ baseUrl: "http://fleet.test" }).listTurns("session-id"),
    ).resolves.toEqual([{ id: "one" }, { id: "two" }]);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://fleet.test/api/sessions/session-id/turns?limit=200",
      expect.any(Object),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://fleet.test/api/sessions/session-id/turns?limit=200&after_sequence=1",
      expect.any(Object),
    );
  });
});
