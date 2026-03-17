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

async function loadRuntimeModule() {
  vi.resetModules();
  return import("@/lib/rlm-api/runtime");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("runtimeEndpoints", () => {
  it("fetches runtime settings from the runtime settings endpoint", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        env_path: "/tmp/.env",
        keys: [],
        values: {},
        masked_values: {},
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    await runtimeEndpoints.settings();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://localhost:8000/api/v1/runtime/settings");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit)?.method).toBe("GET");
  });

  it("patches runtime settings updates using PATCH", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        updated: ["DSPY_LM_MODEL"],
        env_path: "/tmp/.env",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    await runtimeEndpoints.patchSettings({
      updates: { DSPY_LM_MODEL: "openai/gpt-4o-mini" },
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://localhost:8000/api/v1/runtime/settings");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("PATCH");
    expect(String(init.body)).toContain("DSPY_LM_MODEL");
  });

  it("calls runtime status endpoint", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        app_env: "local",
        write_enabled: true,
        ready: false,
        llm: {},
        modal: {},
        tests: { modal: null, lm: null },
        guidance: [],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    await runtimeEndpoints.status();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://localhost:8000/api/v1/runtime/status");
  });

  it("uses frontend dev fallback data when runtime endpoints are unavailable", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Not Found" }, 404));
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    const status = await runtimeEndpoints.status();
    const settings = await runtimeEndpoints.settings();

    expect(status.ready).toBe(true);
    expect(status.guidance?.[0]).toContain("built-in runtime fallback");
    expect(settings.env_path).toBe(".env");
  });

  it("uses fallback data when a local loopback backend returns 502", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://127.0.0.1:8000");
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Bad Gateway" }, 502));
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    const status = await runtimeEndpoints.status();

    expect(status.ready).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
