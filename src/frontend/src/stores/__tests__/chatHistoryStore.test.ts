import { beforeEach, describe, expect, it, vi } from "vitest";

const CURRENT_VERSIONED_KEY = "hax-fleet:chat-history:v2";
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

  it("persists turn-scoped artifacts with saved conversations", async () => {
    const { useChatHistoryStore } = await import("@/stores/chatHistoryStore");

    const conversationId = useChatHistoryStore.getState().saveConversation(
      [
        {
          id: "user-1",
          type: "user",
          content: "Inspect this turn",
        },
        {
          id: "assistant-1",
          type: "assistant",
          content: "Done",
          streaming: false,
        },
      ],
      "idle",
      undefined,
      {
        "assistant-1": [
          {
            id: "step-1",
            type: "llm",
            label: "Planned answer",
            timestamp: 1,
          },
        ],
      },
    );

    const loaded = useChatHistoryStore.getState().loadConversation(conversationId);
    const persisted = JSON.parse(
      localStorage.getItem(CURRENT_VERSIONED_KEY) ?? "null",
    ) as {
      state?: {
        conversations?: Array<{
          turnArtifactsByMessageId?: Record<string, unknown[]>;
        }>;
      };
    } | null;

    expect(loaded?.turnArtifactsByMessageId).toEqual({
      "assistant-1": [
        {
          id: "step-1",
          type: "llm",
          label: "Planned answer",
          timestamp: 1,
        },
      ],
    });
    expect(
      persisted?.state?.conversations?.[0]?.turnArtifactsByMessageId,
    ).toEqual({
      "assistant-1": [
        {
          id: "step-1",
          type: "llm",
          label: "Planned answer",
          timestamp: 1,
        },
      ],
    });
  });
});
