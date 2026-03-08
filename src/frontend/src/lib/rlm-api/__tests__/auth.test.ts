import { afterEach, describe, expect, it, vi } from "vitest";

type MockResponseBody = Record<string, unknown>;

function mockJsonResponse(body: MockResponseBody, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

async function loadAuthModule() {
  vi.resetModules();
  return import("@/lib/rlm-api/auth");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("authEndpoints", () => {
  it("calls /api/v1/auth/me", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        tenant_claim: "default",
        user_claim: "fleetwebapp-user",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { authEndpoints } = await loadAuthModule();
    await authEndpoints.me();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://localhost:8000/api/v1/auth/me",
    );
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe("GET");
  });

  it("clears local auth without calling the backend", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    sessionStorage.setItem("fleet-rlm:access-token", "abc-123");

    const { authEndpoints } = await loadAuthModule();
    authEndpoints.clearLocalAuth();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("fleet-rlm:access-token")).toBeNull();
  });
});
