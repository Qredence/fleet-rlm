import { beforeEach, describe, expect, it, vi } from "vite-plus/test";

const STORAGE_KEY = "hax-fleet:chat-history:v2";

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

const conversationFixture = {
  id: "conv-1",
  title: "Stored conversation",
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

  it("hydrates conversations from the current persisted key", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        state: { conversations: [conversationFixture] },
        version: 2,
      }),
    );

    const { useChatHistoryStore } =
      await import("@/screens/workspace/use-workspace");

    await useChatHistoryStore.persist.rehydrate();

    expect(useChatHistoryStore.getState().conversations).toEqual([
      conversationFixture,
    ]);
  });

  it("keeps only the newest stored item for the same logical chat session", async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        state: {
          conversations: [
            {
              ...conversationFixture,
              id: "conv-newer",
              updatedAt: "2026-03-09T11:00:00.000Z",
            },
            {
              ...conversationFixture,
              id: "conv-older",
              updatedAt: "2026-03-09T10:00:00.000Z",
            },
          ],
        },
        version: 2,
      }),
    );

    const { useChatHistoryStore } =
      await import("@/screens/workspace/use-workspace");

    await useChatHistoryStore.persist.rehydrate();

    expect(useChatHistoryStore.getState().conversations).toEqual([
      {
        ...conversationFixture,
        id: "conv-newer",
        updatedAt: "2026-03-09T11:00:00.000Z",
      },
    ]);
  });

  it("persists turn-scoped artifacts with saved conversations", async () => {
    const { useChatHistoryStore } =
      await import("@/screens/workspace/use-workspace");

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

    const loaded = useChatHistoryStore
      .getState()
      .loadConversation(conversationId);
    const persisted = JSON.parse(
      localStorage.getItem(STORAGE_KEY) ?? "null",
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

  it("updates the existing saved item when the same chat session is saved again", async () => {
    const { useChatHistoryStore } =
      await import("@/screens/workspace/use-workspace");

    const firstSaveId = useChatHistoryStore.getState().saveConversation(
      [
        {
          id: "user-1",
          type: "user",
          content: "Start this session",
        },
        {
          id: "assistant-1",
          type: "assistant",
          content: "First reply",
          streaming: false,
        },
      ],
      "idle",
    );

    const secondSaveId = useChatHistoryStore.getState().saveConversation(
      [
        {
          id: "user-1",
          type: "user",
          content: "Start this session",
        },
        {
          id: "assistant-1",
          type: "assistant",
          content: "First reply",
          streaming: false,
        },
        {
          id: "user-2",
          type: "user",
          content: "Follow up",
        },
        {
          id: "assistant-2",
          type: "assistant",
          content: "Updated reply",
          streaming: false,
        },
      ],
      "complete",
    );

    const conversations = useChatHistoryStore.getState().conversations;

    expect(secondSaveId).toBe(firstSaveId);
    expect(conversations).toHaveLength(1);
    expect(conversations[0]?.id).toBe(firstSaveId);
    expect(conversations[0]?.phase).toBe("complete");
    expect(conversations[0]?.messages.at(-1)?.id).toBe("assistant-2");
  });
});
