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

describe("api capabilities probing", () => {
  it("does not probe network when legacy probing is disabled", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", "false");

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
    expect(caps.skills.reason).toContain(
      "VITE_FLEET_ENABLE_LEGACY_API_PROBES=true",
    );
  });

  it("probes network when legacy probing is enabled", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", "true");

    const fetchSpy = vi.fn(async () => new Response("", { status: 404 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { getApiCapabilities } = await loadCapabilitiesModule();
    const caps = await getApiCapabilities({ forceRefresh: true });

    expect(fetchSpy).toHaveBeenCalledTimes(5);
    expect(caps.skills.available).toBe(false);
    expect(caps.skills.status).toBe(404);
  });

  it.each([401, 403, 405, 500])(
    "treats status %s as supported route (available)",
    async (status) => {
      vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
      vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", "true");

      const fetchSpy = vi.fn(async () => new Response("", { status }));
      vi.stubGlobal("fetch", fetchSpy);

      const { getApiCapabilities } = await loadCapabilitiesModule();
      const caps = await getApiCapabilities({ forceRefresh: true });

      expect(fetchSpy).toHaveBeenCalledTimes(5);
      expect(caps.skills.available).toBe(true);
      expect(caps.skills.status).toBe(status);
      expect(caps.memory.available).toBe(true);
      expect(caps.taxonomy.available).toBe(true);
    },
  );

  it("maps fallback reason with explicit probe reason", async () => {
    const { dataSourceForCapability } = await loadCapabilitiesModule();

    const next = dataSourceForCapability(false, {
      available: false,
      path: "/api/v1/memory",
      reason: "Endpoint /api/v1/memory responded with 404",
      status: 404,
    });

    expect(next.dataSource).toBe("fallback");
    expect(next.degradedReason).toContain(
      "memory data is using local mock fallback",
    );
    expect(next.degradedReason).toContain("responded with 404");
  });

  it("maps fallback reason from path when reason is absent", async () => {
    const { dataSourceForCapability } = await loadCapabilitiesModule();

    const next = dataSourceForCapability(false, {
      available: false,
      path: "/api/v1/taxonomy",
    });

    expect(next.dataSource).toBe("fallback");
    expect(next.degradedReason).toContain(
      "taxonomy data is using local mock fallback",
    );
    expect(next.degradedReason).toContain("/api/v1/taxonomy is unavailable");
  });

  it("reuses cached capabilities within ttl without extra probes", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", "true");

    const fetchSpy = vi.fn(async () => new Response("", { status: 404 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { getApiCapabilities } = await loadCapabilitiesModule();
    await getApiCapabilities({ forceRefresh: true });
    await getApiCapabilities();

    expect(fetchSpy).toHaveBeenCalledTimes(5);
  });

  it("dedupes concurrent in-flight probe requests", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", "true");

    let releaseFetch: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      releaseFetch = resolve;
    });

    const fetchSpy = vi.fn(async () => {
      await gate;
      return new Response("", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { getApiCapabilities } = await loadCapabilitiesModule();

    const first = getApiCapabilities({ forceRefresh: true });
    const second = getApiCapabilities();

    expect(fetchSpy).toHaveBeenCalledTimes(5);

    releaseFetch?.();
    const [a, b] = await Promise.all([first, second]);

    expect(a).toStrictEqual(b);
    expect(fetchSpy).toHaveBeenCalledTimes(5);
  });

  it("clears cache/in-flight state when reset is called", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_ENABLE_LEGACY_API_PROBES", "true");

    const fetchSpy = vi.fn(async () => new Response("", { status: 404 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { getApiCapabilities, resetApiCapabilitiesCache } =
      await loadCapabilitiesModule();

    await getApiCapabilities({ forceRefresh: true });
    await getApiCapabilities();
    expect(fetchSpy).toHaveBeenCalledTimes(5);

    resetApiCapabilitiesCache();
    await getApiCapabilities();
    expect(fetchSpy).toHaveBeenCalledTimes(10);
  });
});
