import { afterEach, describe, expect, it, vi } from "vitest";

async function loadConfigModule() {
  vi.resetModules();
  return import("@/lib/api/config");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("api config env parsing", () => {
  it("defaults mock mode to disabled when env is unset", async () => {
    vi.stubEnv("VITE_MOCK_MODE", undefined);

    const { apiConfig, isMockMode } = await loadConfigModule();
    expect(apiConfig.mockMode).toBe(false);
    expect(isMockMode()).toBe(false);
  });

  it.each(["true", "1", "yes", "TRUE", "YeS"])(
    "parses %s as mock mode enabled",
    async (value) => {
      vi.stubEnv("VITE_MOCK_MODE", value);

      const { apiConfig, isMockMode } = await loadConfigModule();
      expect(apiConfig.mockMode).toBe(true);
      expect(isMockMode()).toBe(true);
    },
  );

  it.each(["false", "0", "no", "FALSE", "No"])(
    "parses %s as mock mode disabled",
    async (value) => {
      vi.stubEnv("VITE_MOCK_MODE", value);

      const { apiConfig, isMockMode } = await loadConfigModule();
      expect(apiConfig.mockMode).toBe(false);
      expect(isMockMode()).toBe(false);
    },
  );

  it("reports websocket availability from ws url or non-mock mode", async () => {
    vi.stubEnv("VITE_MOCK_MODE", "false");
    vi.stubEnv("VITE_FLEET_WS_URL", "");
    let mod = await loadConfigModule();
    expect(mod.isWsAvailable()).toBe(true);

    vi.resetModules();
    vi.stubEnv("VITE_MOCK_MODE", "true");
    vi.stubEnv("VITE_FLEET_WS_URL", "");
    mod = await loadConfigModule();
    expect(mod.isWsAvailable()).toBe(false);

    vi.resetModules();
    vi.stubEnv("VITE_MOCK_MODE", "true");
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://localhost:8000");
    mod = await loadConfigModule();
    expect(mod.isWsAvailable()).toBe(true);
  });
});
