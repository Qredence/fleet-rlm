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

    expect(config.rlmApiConfig.wsUrl).toBe("ws://localhost:8000/api/v1/ws/execution");
    expect(config.rlmApiConfig.wsExecutionUrl).toBe(
      "ws://localhost:8000/api/v1/ws/execution/events",
    );
  });

  it("keeps explicit execution ws URLs unchanged", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/execution");
    vi.stubEnv("VITE_FLEET_API_URL", "");

    const { config } = await loadModules();

    expect(config.rlmApiConfig.wsUrl).toBe("ws://localhost:8000/api/v1/ws/execution");
    expect(config.rlmApiConfig.wsExecutionUrl).toBe(
      "ws://localhost:8000/api/v1/ws/execution/events",
    );
  });

  it("rejects deleted retired chat websocket URLs instead of using retired routes", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000/api/v1/ws/chat");
    vi.stubEnv("VITE_FLEET_API_URL", "");

    const { config } = await loadModules();

    expect(config.rlmApiConfig.wsUrl).toBe("ws://localhost:3000/api/v1/ws/execution");
    expect(config.rlmApiConfig.wsExecutionUrl).toBe(
      "ws://localhost:3000/api/v1/ws/execution/events",
    );
  });

  it("keeps runtime endpoint paths on /api/v1/runtime/*", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");

    const calls: string[] = [];
    vi.doMock("@/lib/rlm-api/typed-client", () => {
      function makeMethod() {
        return vi.fn(async (path: string) => {
          calls.push(`http://localhost:8000${path}`);
          return { data: {}, error: undefined, response: { ok: true, status: 200 } as Response };
        });
      }
      return {
        typedClient: {
          GET: makeMethod(),
          POST: makeMethod(),
          PATCH: makeMethod(),
          DELETE: makeMethod(),
        },
        unwrap: vi.fn(async (promise: Promise<{ data?: unknown; error?: unknown }>) => {
          const result = await promise;
          return result.data;
        }),
        withTimeout: vi.fn((signal?: AbortSignal) => signal),
      };
    });

    const { runtime } = await loadModules();

    await runtime.runtimeEndpoints.settings();
    await runtime.runtimeEndpoints.patchSettings({ updates: {} });
    await runtime.runtimeEndpoints.testDaytona();
    await runtime.runtimeEndpoints.testLm();
    await runtime.runtimeEndpoints.status();

    vi.doUnmock("@/lib/rlm-api/typed-client");

    expect(calls).toEqual([
      "http://localhost:8000/api/v1/runtime/settings",
      "http://localhost:8000/api/v1/runtime/settings",
      "http://localhost:8000/api/v1/runtime/tests/daytona",
      "http://localhost:8000/api/v1/runtime/tests/lm",
      "http://localhost:8000/api/v1/runtime/status",
    ]);
  });
});
