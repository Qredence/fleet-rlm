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

function setupTypedClientWithFetch(fetchMock: ReturnType<typeof vi.fn>) {
  vi.doMock("@/lib/rlm-api/typed-client", async () => {
    const { RlmApiError: ActualRlmApiError } = await vi.importActual<
      typeof import("@/lib/rlm-api/client")
    >("@/lib/rlm-api/client");

    function resolveBaseUrl(): string {
      const apiUrl = import.meta.env.VITE_FLEET_API_URL ?? "";
      if (!apiUrl) return "";
      return apiUrl.replace(/\/$/, "");
    }

    const baseUrl = resolveBaseUrl();

    async function coreFetch(method: string, path: string, init?: { body?: unknown }) {
      const url = baseUrl ? `${baseUrl}${path}` : path;
      const response = await (fetchMock as unknown as (url: string, init?: RequestInit) => Promise<Response>)(url, { method, body: init?.body ? JSON.stringify(init.body) : undefined });
      const status = (response as unknown as { status: number }).status;
      const ok = status >= 200 && status < 300;
      const body = await (response as Response).json();
      if (ok) return { data: body, error: undefined, response };
      return { data: undefined, error: body, response };
    }

    return {
      typedClient: {
        GET: vi.fn((_path: string, _opts?: unknown) => coreFetch("GET", _path)),
        POST: vi.fn((_path: string, _opts?: unknown) => coreFetch("POST", _path, _opts as { body?: unknown })),
        PATCH: vi.fn((_path: string, _opts?: unknown) => coreFetch("PATCH", _path, _opts as { body?: unknown })),
        DELETE: vi.fn((_path: string, _opts?: unknown) => coreFetch("DELETE", _path)),
      },
      unwrap: vi.fn(async (promise: Promise<{ data?: unknown; error?: unknown; response: Response }>) => {
        const result = await promise;
        if (result.error !== undefined) {
          const errBody = result.error as Record<string, unknown> | undefined;
          const detail =
            (typeof errBody?.detail === "string" && errBody.detail) ||
            (typeof errBody?.message === "string" && errBody.message) ||
            `HTTP ${result.response.status}`;
          throw new ActualRlmApiError(result.response.status, detail);
        }
        if (!result.response.ok) {
          throw new ActualRlmApiError(result.response.status, `HTTP ${result.response.status}`);
        }
        return result.data;
      }),
      withTimeout: vi.fn((signal?: AbortSignal) => signal),
    };
  });
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.doUnmock("@/lib/rlm-api/config");
  vi.doUnmock("@/lib/rlm-api/typed-client");
  vi.restoreAllMocks();
});

describe("runtimeEndpoints", () => {
  it("fetches runtime settings from the runtime settings endpoint", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        env_path: "/tmp/.env",
        categories: [],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    setupTypedClientWithFetch(fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    await runtimeEndpoints.settings();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://localhost:8000/api/v1/runtime/settings");
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
    setupTypedClientWithFetch(fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    await runtimeEndpoints.patchSettings({
      updates: { DSPY_LM_MODEL: "openai/gpt-4o-mini" },
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://localhost:8000/api/v1/runtime/settings");
  });

  it("calls runtime status endpoint", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        app_env: "local",
        write_enabled: true,
        ready: false,
        llm: {},
        daytona: {},
        tests: { daytona: null, lm: null },
        guidance: [],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    setupTypedClientWithFetch(fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    await runtimeEndpoints.status();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://localhost:8000/api/v1/runtime/status");
  });

  it("uses a focused timeout for runtime connectivity smoke tests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse({
        kind: "lm",
        ok: true,
        preflight_ok: true,
        checked_at: "2026-04-16T10:00:00Z",
        checks: {},
        guidance: [],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    setupTypedClientWithFetch(fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();

    await runtimeEndpoints.testLm();
    await runtimeEndpoints.testDaytona();

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://localhost:8000/api/v1/runtime/tests/lm", expect.any(Object));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://localhost:8000/api/v1/runtime/tests/daytona", expect.any(Object));
  });

  it("uses fallback data in explicit mock mode when runtime endpoints are unavailable", async () => {
    vi.stubEnv("VITE_MOCK_MODE", "true");
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Not Found" }, 404));
    vi.stubGlobal("fetch", fetchMock);
    setupTypedClientWithFetch(fetchMock);

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
    setupTypedClientWithFetch(fetchMock);

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

  it("surfaces canonical API error envelope messages", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "");
    const fetchMock = vi.fn().mockResolvedValue(
      mockJsonResponse(
        {
          code: "validation_error",
          message: "Request validation failed.",
          detail: [
            { loc: ["query", "max_depth"], msg: "Input should be greater than or equal to 1" },
          ],
        },
        422,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    setupTypedClientWithFetch(fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();

    await expect(runtimeEndpoints.status()).rejects.toEqual(
      expect.objectContaining<RlmApiError>({
        detail: "Request validation failed.",
        message: "[422] Request validation failed.",
        name: "RlmApiError",
        status: 422,
      }),
    );
  });

  it("does not use read fallback when a local loopback backend returns 502", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://127.0.0.1:8000");
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Bad Gateway" }, 502));
    vi.stubGlobal("fetch", fetchMock);
    setupTypedClientWithFetch(fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    await expect(runtimeEndpoints.status()).rejects.toEqual(
      expect.objectContaining<RlmApiError>({
        detail: "Bad Gateway",
        message: "[502] Bad Gateway",
        name: "RlmApiError",
        status: 502,
      }),
    );
  });

  it("does not use write fallback for loopback backend failures", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://127.0.0.1:8000");
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ detail: "Bad Gateway" }, 502));
    vi.stubGlobal("fetch", fetchMock);
    setupTypedClientWithFetch(fetchMock);

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
    setupTypedClientWithFetch(fetchMock);

    const { runtimeEndpoints } = await loadRuntimeModule();
    const result = await runtimeEndpoints.patchSettings({
      updates: { DSPY_LM_MODEL: "openai/gemini-3-flash-preview" },
    });

    expect(result.updated).toContain("DSPY_LM_MODEL");
  });
});
