import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CANONICAL_KEY = "fleet-rlm:access-token";
const LEGACY_KEY = "fleet_access_token";

const createStorageMock = () => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] || null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value.toString();
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      store = {};
    }),
  };
};

type TokenStoreModule = Awaited<typeof import("@/lib/auth/tokenStore")>;

async function loadTokenStore(): Promise<TokenStoreModule> {
  vi.resetModules();
  return import("@/lib/auth/tokenStore");
}

beforeEach(() => {
  Object.defineProperty(window, "localStorage", { value: createStorageMock(), writable: true });
  Object.defineProperty(window, "sessionStorage", { value: createStorageMock(), writable: true });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("tokenStore", () => {
  it("persists canonical token in sessionStorage and removes local keys", async () => {
    localStorage.setItem(LEGACY_KEY, "legacy-token");

    const { setAccessToken, getAccessToken } = await loadTokenStore();
    setAccessToken("abc-123");

    expect(getAccessToken()).toBe("abc-123");
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBe("abc-123");
    expect(localStorage.getItem(CANONICAL_KEY)).toBeNull();
    expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
  });

  it("migrates canonical localStorage token into sessionStorage", async () => {
    localStorage.setItem(CANONICAL_KEY, "canonical-local-token");

    const { getAccessToken } = await loadTokenStore();

    expect(getAccessToken()).toBe("canonical-local-token");
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBe("canonical-local-token");
    expect(localStorage.getItem(CANONICAL_KEY)).toBeNull();
  });

  it("reads legacy localStorage token and migrates it to sessionStorage", async () => {
    localStorage.setItem(LEGACY_KEY, "legacy-only-token");

    const { getAccessToken } = await loadTokenStore();

    expect(getAccessToken()).toBe("legacy-only-token");
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBe("legacy-only-token");
    expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
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
