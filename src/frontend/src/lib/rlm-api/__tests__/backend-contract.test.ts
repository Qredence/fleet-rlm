import { afterEach, describe, expect, it, vi } from "vite-plus/test";

async function loadModules() {
  vi.resetModules();
  const config = await import("@/lib/rlm-api/config");
  const runtime = await import("@/lib/rlm-api/runtime");
  return { config, runtime };
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("backend contract lock", () => {
  it("keeps ws chat/execution paths derived from API URL", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_WS_URL", "");

    const { config } = await loadModules();

    expect(config.rlmApiConfig.wsUrl).toBe("ws://localhost:8000/api/v1/ws/chat");
    expect(config.rlmApiConfig.wsExecutionUrl).toBe("ws://localhost:8000/api/v1/ws/execution");
  });

  it("maps explicit /chat ws URL to /execution for execution stream", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    vi.stubEnv("VITE_FLEET_API_URL", "");

    const { config } = await loadModules();

    expect(config.rlmApiConfig.wsUrl).toBe("ws://localhost:8000/api/v1/ws/chat");
    expect(config.rlmApiConfig.wsExecutionUrl).toBe("ws://localhost:8000/api/v1/ws/execution");
  });

  it("keeps runtime endpoint paths on /api/v1/runtime/*", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
      text: async () => "{}",
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    const { runtime } = await loadModules();

    await runtime.runtimeEndpoints.settings();
    await runtime.runtimeEndpoints.patchSettings({ updates: {} });
    await runtime.runtimeEndpoints.testModal();
    await runtime.runtimeEndpoints.testLm();
    await runtime.runtimeEndpoints.status();

    const calledUrls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledUrls).toEqual([
      "http://localhost:8000/api/v1/runtime/settings",
      "http://localhost:8000/api/v1/runtime/settings",
      "http://localhost:8000/api/v1/runtime/tests/modal",
      "http://localhost:8000/api/v1/runtime/tests/lm",
      "http://localhost:8000/api/v1/runtime/status",
    ]);
  });
});
