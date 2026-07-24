import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetApiClient, FleetApiError } from "../fleet-api-client.js";

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

  it("renames a Session through PATCH", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "session-id", title: "Research notes" }), {
        headers: { "content-type": "application/json" },
      }),
    );
    globalThis.fetch = fetchMock;

    await new FleetApiClient({ baseUrl: "http://fleet.test" }).updateSession("session-id", {
      title: "Research notes",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://fleet.test/api/sessions/session-id",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ title: "Research notes" }),
      }),
    );
  });

  it("filters the Session list by active status and title", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ items: [], total: 0, offset: 0, limit: 100, has_more: false }),
          { headers: { "content-type": "application/json" } },
        ),
      );
    globalThis.fetch = fetchMock;

    await new FleetApiClient({ baseUrl: "http://fleet.test" }).listSessions({
      limit: 100,
      status: "active",
      search: "research notes",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://fleet.test/api/sessions?limit=100&search=research+notes&status=active",
      expect.any(Object),
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
        body: JSON.stringify({ text: "hello", attachment_ids: [], skill_selections: [] }),
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

  it("serializes pinned Skills and acknowledges them only after stream headers are accepted", async () => {
    const onStreamOpen = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("data: [DONE]\n\n", {
        headers: { "x-vercel-ai-ui-message-stream": "v1" },
      }),
    );
    globalThis.fetch = fetchMock;
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });

    await client.streamTurn({
      message: "hello",
      sessionId: "session-id",
      idempotencyKey: "turn-key",
      skillSelections: [{ id: "00000000-0000-4000-8000-000000000001", expected_version: "2.0.0" }],
      onStreamOpen,
    });

    expect(JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string)).toEqual({
      text: "hello",
      attachment_ids: [],
      skill_selections: [{ id: "00000000-0000-4000-8000-000000000001", expected_version: "2.0.0" }],
    });
    expect(onStreamOpen).toHaveBeenCalledTimes(1);
  });

  it("does not acknowledge pinned Skills when Turn preparation fails before SSE", async () => {
    const onStreamOpen = vi.fn();
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "invalid_skill_selection" }), {
        status: 422,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });

    await expect(
      client.streamTurn({
        message: "hello",
        sessionId: "session-id",
        idempotencyKey: "turn-key",
        skillSelections: [
          { id: "00000000-0000-4000-8000-000000000001", expected_version: "2.0.0" },
        ],
        onStreamOpen,
      }),
    ).rejects.toMatchObject({ status: 422, message: "invalid_skill_selection" });
    expect(onStreamOpen).not.toHaveBeenCalled();
  });

  it("lists discoverable Skill cards", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: "skill-1", name: "long-context" }]), {
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(
      new FleetApiClient({ baseUrl: "http://fleet.test" }).listSkills(),
    ).resolves.toEqual([{ id: "skill-1", name: "long-context" }]);
  });

  it("rejects a non-v1 SSE response", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });

    await expect(
      client.streamTurn({ message: "hello", sessionId: "session-id", idempotencyKey: "turn-key" }),
    ).rejects.toBeInstanceOf(FleetApiError);
  });

  it("presents FastAPI nested public error details", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: { code: "turn_in_progress", message: "A Turn is already running" },
        }),
        { status: 409, headers: { "content-type": "application/json" } },
      ),
    );
    const client = new FleetApiClient({ baseUrl: "http://fleet.test" });

    await expect(
      client.streamTurn({ message: "hello", sessionId: "session-id", idempotencyKey: "turn-key" }),
    ).rejects.toMatchObject({
      status: 409,
      code: "turn_in_progress",
      message: "A Turn is already running",
    });
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
