import { describe, expect, it } from "vitest";

function installSessionStorageShim() {
  const storage = new Map<string, string>();
  globalThis.sessionStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, value);
    },
    removeItem: (key: string) => {
      storage.delete(key);
    },
    clear: () => {
      storage.clear();
    },
    key: () => null,
    get length() {
      return storage.size;
    },
  } as Storage;
}

describe("legacy api surface", () => {
  it("does not expose deprecated auth/chat endpoint stubs", async () => {
    installSessionStorageShim();
    const legacyEndpointsModule = await import("@/lib/api/endpoints");
    const legacyBarrelModule = await import("@/lib/api");

    expect("chatEndpoints" in legacyEndpointsModule).toBe(false);
    expect("authEndpoints" in legacyEndpointsModule).toBe(false);
    expect("chatEndpoints" in legacyBarrelModule).toBe(false);
    expect("authEndpoints" in legacyBarrelModule).toBe(false);
  });
});
