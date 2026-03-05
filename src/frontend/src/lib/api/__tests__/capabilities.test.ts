import { afterEach, describe, expect, it, vi } from "vitest";

type CapabilitiesModule = Awaited<typeof import("@/lib/api/capabilities")>;

async function loadCapabilitiesModule(): Promise<CapabilitiesModule> {
  vi.resetModules();
  return import("@/lib/api/capabilities");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("api capabilities", () => {
  it("returns unavailable capabilities when not in mock mode", async () => {
    vi.stubEnv("VITE_MOCK_MODE", "false");
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    const { getApiCapabilities } = await loadCapabilitiesModule();
    const caps = await getApiCapabilities({ forceRefresh: true });

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(caps.skills.available).toBe(false);
    expect(caps.memory.available).toBe(false);
    expect(caps.taxonomy.available).toBe(false);
    expect(caps.analytics.available).toBe(false);
    expect(caps.filesystem.available).toBe(false);
    expect(caps.skills.reason).toContain("removed");
  });

  it("returns available capabilities in explicit mock mode", async () => {
    vi.stubEnv("VITE_MOCK_MODE", "true");

    const { getApiCapabilities } = await loadCapabilitiesModule();
    const caps = await getApiCapabilities({ forceRefresh: true });

    expect(caps.skills.available).toBe(true);
    expect(caps.memory.available).toBe(true);
    expect(caps.taxonomy.available).toBe(true);
    expect(caps.analytics.available).toBe(true);
    expect(caps.filesystem.available).toBe(true);
  });

  it("maps fallback data source with explicit reason", async () => {
    const { dataSourceForCapability } = await loadCapabilitiesModule();

    const next = dataSourceForCapability(
      false,
      {
        available: false,
        reason: "Memory API was removed from backend.",
      },
      "memory",
    );

    expect(next.dataSource).toBe("fallback");
    expect(next.degradedReason).toContain(
      "memory data is using local mock fallback",
    );
    expect(next.degradedReason).toContain("removed from backend");
  });

  it("resets capability cache", async () => {
    vi.stubEnv("VITE_MOCK_MODE", "false");
    const { getApiCapabilities, resetApiCapabilitiesCache } =
      await loadCapabilitiesModule();

    const first = await getApiCapabilities();
    resetApiCapabilitiesCache();
    const second = await getApiCapabilities();

    expect(first).toStrictEqual(second);
  });
});
