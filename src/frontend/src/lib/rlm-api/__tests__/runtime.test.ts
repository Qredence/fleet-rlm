import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { RlmApiError } from "@/lib/rlm-api/client";

type MockResponseBody = Record<string, unknown>;
type RlmApiConfig = (typeof import("@/lib/rlm-api/config"))["rlmApiConfig"];

function mockJsonResponse(body: MockResponseBody, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

async function loadRuntimeModule(configOverride?: Partial<RlmApiConfig>) {
  vi.resetModules();
  vi.doUnmock("@/lib/rlm-api/config");
  if (configOverride) {
    vi.doMock("@/lib/rlm-api/config", async () => {
      const actual =
        await vi.importActual<typeof import("@/lib/rlm-api/config")>("@/lib/rlm-api/config");

      return {
        ...actual,
        rlmApiConfig: {
          ...actual.rlmApiConfig,
          ...configOverride,
        },
      };
    });
  }
  return import("@/lib/rlm-api/runtime");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.doUnmock("@/lib/rlm-api/config");
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

  it("uses fallback data in explicit mock mode when runtime endpoints are unavailable", async () => {
    vi.stubEnv("VITE_MOCK_MODE", "true");
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Not Found" }, 404));
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    const status = await runtimeEndpoints.status();
    const settings = await runtimeEndpoints.settings();

    expect(status.ready).toBe(true);
    expect(status.guidance?.[0]).toContain("built-in runtime fallback");
    expect(settings.env_path).toBe(".env");
  });

  it("does not use read fallback for same-origin errors without explicit mock mode", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "");
    vi.stubEnv("VITE_FLEET_WS_URL", "");
    vi.stubEnv("VITE_MOCK_MODE", "");
    vi.stubEnv("VITE_E2E", "");
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Not Found" }, 404));
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();

    await expect(runtimeEndpoints.status()).rejects.toEqual(
      expect.objectContaining<RlmApiError>({
        detail: "Not Found",
        message: "[404] Not Found",
        name: "RlmApiError",
        status: 404,
      }),
    );
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

  it("does not use write fallback for loopback backend failures", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://127.0.0.1:8000");
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Bad Gateway" }, 502));
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();

    await expect(
      runtimeEndpoints.patchSettings({
        updates: { DSPY_LM_MODEL: "openai/gemini-3-flash-preview" },
      }),
    ).rejects.toEqual(
      expect.objectContaining<RlmApiError>({
        detail: "Bad Gateway",
        message: "[502] Bad Gateway",
        name: "RlmApiError",
        status: 502,
      }),
    );
  });

  it("uses write fallback in explicit mock mode", async () => {
    vi.stubEnv("VITE_MOCK_MODE", "true");
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Bad Gateway" }, 502));
    vi.stubGlobal("fetch", fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    const result = await runtimeEndpoints.patchSettings({
      updates: { DSPY_LM_MODEL: "openai/gemini-3-flash-preview" },
    });

    expect(result.updated).toContain("DSPY_LM_MODEL");
  });
});
