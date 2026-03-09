import { beforeEach, describe, expect, it, vi } from "vitest";

const VERSIONED_KEY = "hax-fleet:chat-history:v1";
const LEGACY_KEY = "hax-fleet:chat-history";

function createMemoryStorage(): Storage {
  const values = new Map<string, string>();

  return {
    get length() {
      return values.size;
    },
    clear: () => {
      values.clear();
    },
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

const legacyConversation = {
  id: "conv-1",
  title: "Legacy conversation",
  messages: [
    {
      id: "msg-1",
      type: "user" as const,
      content: "Hello from storage",
      phase: 1 as const,
    },
  ],
  phase: "idle" as const,
  createdAt: "2026-03-09T10:00:00.000Z",
  updatedAt: "2026-03-09T10:00:00.000Z",
};

describe("useChatHistoryStore", () => {
  beforeEach(() => {
    vi.resetModules();
    const storage = createMemoryStorage();
    vi.stubGlobal("localStorage", storage);
    Object.defineProperty(window, "localStorage", {
      value: storage,
      configurable: true,
    });
  });

  it("hydrates conversations from the previous raw versioned array format", async () => {
    localStorage.setItem(VERSIONED_KEY, JSON.stringify([legacyConversation]));

    const { useChatHistoryStore } = await import("@/stores/chatHistoryStore");

    await useChatHistoryStore.persist.rehydrate();

    expect(useChatHistoryStore.getState().conversations).toEqual([
      legacyConversation,
    ]);
  });

  it("hydrates conversations from the unversioned legacy key", async () => {
    localStorage.setItem(LEGACY_KEY, JSON.stringify([legacyConversation]));

    const { useChatHistoryStore } = await import("@/stores/chatHistoryStore");

    await useChatHistoryStore.persist.rehydrate();

    expect(useChatHistoryStore.getState().conversations).toEqual([
      legacyConversation,
    ]);
  });
});
