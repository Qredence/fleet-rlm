import { afterEach, describe, expect, it, vi } from "vite-plus/test";

async function loadAuthModule() {
  vi.resetModules();
  return import("@/lib/rlm-api/auth");
}

function mockResponse(data: unknown, status = 200) {
  return {
    data,
    error: undefined,
    response: { ok: status >= 200 && status < 300, status } as Response,
  };
}

function mockTypedClient(overrides: {
  GET?: ReturnType<typeof vi.fn>;
  POST?: ReturnType<typeof vi.fn>;
}) {
  vi.doMock("@/lib/rlm-api/typed-client", () => ({
    typedClient: {
      GET: overrides.GET ?? vi.fn(),
      POST: overrides.POST ?? vi.fn(),
      PATCH: vi.fn(),
      DELETE: vi.fn(),
    },
    unwrap: vi.fn(async (promise: Promise<{ data?: unknown; error?: unknown }>) => {
      const result = await promise;
      return result.data;
    }),
    withTimeout: vi.fn((signal?: AbortSignal) => signal),
  }));
}

afterEach(() => {
  vi.doUnmock("@/lib/rlm-api/typed-client");
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("authEndpoints", () => {
  it("calls /api/v1/auth/me", async () => {
    const GET = vi.fn().mockResolvedValue(
      mockResponse({ tenant_claim: "default", user_claim: "fleetwebapp-user" }),
    );
    mockTypedClient({ GET });

    const { authEndpoints } = await loadAuthModule();
    await authEndpoints.me();

    expect(GET).toHaveBeenCalledTimes(1);
    expect(GET.mock.calls[0]?.[0]).toBe("/api/v1/auth/me");
  });

  it("creates a WebSocket ticket through the authenticated HTTP API", async () => {
    const POST = vi.fn().mockResolvedValue(
      mockResponse({ ticket: "ticket-123", expires_at: "2026-06-20T03:30:00Z" }),
    );
    mockTypedClient({ POST });

    const { authEndpoints } = await loadAuthModule();
    await authEndpoints.createWsTicket();

    expect(POST).toHaveBeenCalledTimes(1);
    expect(POST.mock.calls[0]?.[0]).toBe("/api/v1/auth/ws-ticket");
  });

  it("clears local auth without calling the backend", async () => {
    const GET = vi.fn();
    const POST = vi.fn();
    mockTypedClient({ GET, POST });

    sessionStorage.setItem("fleet-rlm:access-token", "abc-123");

    const { authEndpoints } = await loadAuthModule();
    authEndpoints.clearLocalAuth();

    expect(GET).not.toHaveBeenCalled();
    expect(POST).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("fleet-rlm:access-token")).toBeNull();
  });
});
