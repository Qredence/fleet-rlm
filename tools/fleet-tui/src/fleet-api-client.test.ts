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

  it("sends synthetic dev identity and a Fleet chat request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("data: [DONE]\n\n", {
        headers: { "x-vercel-ai-ui-message-stream": "v1" },
      }),
    );
    globalThis.fetch = fetchMock;
    const client = new FleetApiClient({
      baseUrl: "http://fleet.test/",
      identity: { userId: "user-id", workspaceId: "workspace-id" },
    });

    await client.streamChat({ message: "hello", sessionId: "session-id" });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://fleet.test/api/chat",
      expect.objectContaining({
        body: JSON.stringify({ message: "hello", session_id: "session-id" }),
        headers: expect.objectContaining({
          "x-fleet-user-id": "user-id",
          "x-fleet-workspace-id": "workspace-id",
          "content-type": "application/json",
        }),
      }),
    );
  });

  it("rejects a non-v1 SSE response", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });

    await expect(client.streamChat({ message: "hello", sessionId: "session-id" })).rejects.toBeInstanceOf(FleetApiError);
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
        new Response(JSON.stringify({ items: [{ id: "one", sequence: 1 }], has_more: true }), {
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [{ id: "two", sequence: 2 }], has_more: false }), {
          headers: { "content-type": "application/json" },
        }),
      );
    globalThis.fetch = fetchMock;

    await expect(new FleetApiClient({ baseUrl: "http://fleet.test" }).listTurns("session-id")).resolves.toEqual([
      { id: "one", sequence: 1 },
      { id: "two", sequence: 2 },
    ]);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://fleet.test/api/sessions/session-id/turns?limit=200&offset=0", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://fleet.test/api/sessions/session-id/turns?limit=200&offset=1", expect.any(Object));
  });
});
