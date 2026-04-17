import { afterEach, describe, expect, it, vi } from "vite-plus/test";

type MockResponseBody = Record<string, unknown>;

function mockJsonResponse(body: MockResponseBody, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

async function loadTracesModule() {
  vi.resetModules();
  vi.doUnmock("@/lib/rlm-api/config");
  return import("@/lib/rlm-api/traces");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.doUnmock("@/lib/rlm-api/config");
  vi.restoreAllMocks();
});

describe("traceEndpoints", () => {
  it("posts feedback to the trace feedback endpoint", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        ok: true,
        trace_id: "trace-123",
        client_request_id: "chat-123",
        feedback_logged: true,
        expectation_logged: false,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { traceEndpoints } = await loadTracesModule();

    await traceEndpoints.createFeedback({
      client_request_id: "chat-123",
      is_correct: false,
      comment: "Missed the root cause",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://localhost:8000/api/v1/traces/feedback");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(String(init.body)).toContain('"client_request_id":"chat-123"');
    expect(String(init.body)).toContain('"is_correct":false');
  });
});
