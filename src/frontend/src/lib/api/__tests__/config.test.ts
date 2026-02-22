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
  it("defaults legacy probing to disabled when env is unset", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", undefined);

    const { apiConfig } = await loadConfigModule();
    expect(apiConfig.enableLegacyApiProbes).toBe(false);
  });

  it("enables legacy probing when env is truthy", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", "true");

    const { apiConfig } = await loadConfigModule();
    expect(apiConfig.enableLegacyApiProbes).toBe(true);
  });

  it.each(["true", "1", "yes", "TRUE", "YeS"])(
    "parses %s as truthy",
    async (value) => {
      vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
      vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", value);

      const { apiConfig } = await loadConfigModule();
      expect(apiConfig.enableLegacyApiProbes).toBe(true);
    },
  );

  it.each(["false", "0", "no", "FALSE", "No"])(
    "parses %s as falsy",
    async (value) => {
      vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
      vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", value);

      const { apiConfig } = await loadConfigModule();
      expect(apiConfig.enableLegacyApiProbes).toBe(false);
    },
  );

  it.each(["foo", "enabled", "on", "off"])(
    "falls back to default false for invalid value %s",
    async (value) => {
      vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
      vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", value);

      const { apiConfig } = await loadConfigModule();
      expect(apiConfig.enableLegacyApiProbes).toBe(false);
    },
  );
});
