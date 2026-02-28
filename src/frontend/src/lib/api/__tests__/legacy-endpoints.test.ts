import { describe, expect, it } from "vitest";

async function loadLegacyEndpoints() {
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

  return import("@/lib/api/endpoints");
}

describe("legacy auth/chat endpoint guards", () => {
  it("throws for deprecated chat endpoints", async () => {
    const { chatEndpoints } = await loadLegacyEndpoints();
    expect(() =>
      chatEndpoints.send({ sessionId: "s1", message: "hello" }),
    ).toThrow(/deprecated and intentionally disabled/);
    expect(() =>
      chatEndpoints.resolveHitl({
        sessionId: "s1",
        messageId: "m1",
        action: "approve",
      }),
    ).toThrow(/deprecated and intentionally disabled/);
    expect(() =>
      chatEndpoints.resolveClarification({
        sessionId: "s1",
        messageId: "m1",
        answer: "yes",
      }),
    ).toThrow(/deprecated and intentionally disabled/);
  });

  it("throws for deprecated auth endpoints", async () => {
    const { authEndpoints } = await loadLegacyEndpoints();
    expect(() =>
      authEndpoints.login({ email: "dev@example.com", password: "pw" }),
    ).toThrow(/deprecated and intentionally disabled/);
    expect(() => authEndpoints.logout()).toThrow(
      /deprecated and intentionally disabled/,
    );
    expect(() => authEndpoints.me()).toThrow(/deprecated and intentionally disabled/);
  });
});
