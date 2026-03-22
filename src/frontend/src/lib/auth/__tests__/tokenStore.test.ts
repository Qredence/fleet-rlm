import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

const CANONICAL_KEY = "fleet-rlm:access-token";

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
  it("persists canonical token in sessionStorage", async () => {
    const { setAccessToken, getAccessToken } = await loadTokenStore();
    setAccessToken("abc-123");

    expect(getAccessToken()).toBe("abc-123");
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBe("abc-123");
  });

  it("clears canonical token", async () => {
    const { setAccessToken, clearAccessToken, getAccessToken } = await loadTokenStore();

    setAccessToken("to-clear");

    clearAccessToken();

    expect(getAccessToken()).toBeNull();
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBeNull();
  });

  it("migrates canonical localStorage tokens into sessionStorage", async () => {
    localStorage.setItem(CANONICAL_KEY, "local-canonical");

    const { getAccessToken } = await loadTokenStore();

    expect(getAccessToken()).toBe("local-canonical");
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBe("local-canonical");
    expect(localStorage.getItem(CANONICAL_KEY)).toBeNull();
  });

  it("ignores legacy localStorage tokens", async () => {
    localStorage.setItem("fleet_access_token", "legacy-token");

    const { getAccessToken } = await loadTokenStore();

    expect(getAccessToken()).toBeNull();
    expect(sessionStorage.getItem(CANONICAL_KEY)).toBeNull();
    expect(localStorage.getItem("fleet_access_token")).toBe("legacy-token");
  });
});
