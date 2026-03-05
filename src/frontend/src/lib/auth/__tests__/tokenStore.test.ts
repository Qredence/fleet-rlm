import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CANONICAL_KEY = "fleet-rlm:access-token";
const LEGACY_KEY = "fleet_access_token";

type TokenStoreModule = Awaited<typeof import("@/lib/auth/tokenStore")>;

async function loadTokenStore(): Promise<TokenStoreModule> {
  vi.resetModules();
  return import("@/lib/auth/tokenStore");
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("tokenStore", () => {
  it("persists canonical token and removes legacy key", async () => {
    localStorage.setItem(LEGACY_KEY, "legacy-token");

    const { setAccessToken, getAccessToken } = await loadTokenStore();
    setAccessToken("abc-123");

    expect(getAccessToken()).toBe("abc-123");
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBe("abc-123");
    expect(localStorage.getItem(CANONICAL_KEY)).toBe("abc-123");
    expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
  });

  it("reads from legacy localStorage key when canonical value is absent", async () => {
    localStorage.setItem(LEGACY_KEY, "legacy-only-token");

    const { getAccessToken } = await loadTokenStore();

    expect(getAccessToken()).toBe("legacy-only-token");
  });

  it("clears canonical and legacy tokens", async () => {
    const { setAccessToken, clearAccessToken, getAccessToken } =
      await loadTokenStore();

    setAccessToken("to-clear");
    localStorage.setItem(LEGACY_KEY, "legacy-to-clear");

    clearAccessToken();

    expect(getAccessToken()).toBeNull();
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBeNull();
    expect(localStorage.getItem(CANONICAL_KEY)).toBeNull();
    expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
  });
});
