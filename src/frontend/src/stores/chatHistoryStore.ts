/**
 * Chat history store — manages conversation history with localStorage persistence.
 *
 * Stores an array of past conversations (id, title, messages, phase,
 * createdAt, updatedAt). Exposes CRUD operations.
 *
 * Uses Zustand persist middleware for automatic localStorage sync.
 */
import { create } from "zustand";
import {
  persist,
  type PersistStorage,
  type StorageValue,
} from "zustand/middleware";
import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import { createLocalId } from "@/lib/id";
import type { ExecutionStep } from "@/stores/artifactStore";

// ── Types ────────────────────────────────────────────────────────────

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  turnArtifactsByMessageId?: Record<string, ExecutionStep[]>;
  phase: CreationPhase;
  createdAt: string;
  updatedAt: string;
}

interface ChatHistoryState {
  conversations: Conversation[];
  saveConversation: (
    messages: ChatMessage[],
    phase: CreationPhase,
    conversationId?: string | null,
    turnArtifactsByMessageId?: Record<string, ExecutionStep[]>,
  ) => string;
  loadConversation: (id: string) => Conversation | null;
  deleteConversation: (id: string) => void;
  clearHistory: () => void;
}

// ── Constants ────────────────────────────────────────────────────────

const STORAGE_VERSION = 2;
const STORAGE_KEY = "hax-fleet:chat-history";
const VERSIONED_KEY = `${STORAGE_KEY}:v${STORAGE_VERSION}`;
const LEGACY_VERSIONED_KEYS = [`${STORAGE_KEY}:v1`];
const MAX_CONVERSATIONS = 50;
type ChatHistoryPersistedState = Pick<ChatHistoryState, "conversations">;

// ── Helpers ──────────────────────────────────────────────────────────

function generateId(): string {
  return createLocalId("conv");
}

/** Derive a human-readable title from the first user message. */
function deriveTitle(messages: ChatMessage[]): string {
  const firstUser = messages.find((m) => m.type === "user");
  if (!firstUser) return "Untitled conversation";
  const text = firstUser.content.trim();
  return text.length > 60 ? `${text.slice(0, 57)}…` : text;
}

function parseStoredJson(raw: string | null): unknown {
  try {
    return raw ? (JSON.parse(raw) as unknown) : null;
  } catch {
    return null;
  }
}

function toConversationArray(value: unknown): Conversation[] | null {
  return Array.isArray(value) ? (value as Conversation[]) : null;
}

function toPersistedChatHistory(
  value: unknown,
): StorageValue<ChatHistoryPersistedState> | null {
  const directConversations = toConversationArray(value);
  if (directConversations) {
    return {
      state: { conversations: directConversations },
      version: STORAGE_VERSION,
    };
  }

  if (typeof value !== "object" || value === null) {
    return null;
  }

  const maybePersisted = value as {
    state?: unknown;
    version?: unknown;
  };
  const maybeState = maybePersisted.state;
  if (typeof maybeState !== "object" || maybeState === null) {
    return null;
  }

  const conversations = toConversationArray(
    (maybeState as { conversations?: unknown }).conversations,
  );
  if (!conversations) {
    return null;
  }

  return {
    state: { conversations },
    version:
      typeof maybePersisted.version === "number"
        ? maybePersisted.version
        : STORAGE_VERSION,
  };
}

const chatHistoryStorage: PersistStorage<ChatHistoryPersistedState> = {
  getItem: (name) => {
    for (const key of [name, ...LEGACY_VERSIONED_KEYS]) {
      const storedValue = toPersistedChatHistory(
        parseStoredJson(localStorage.getItem(key)),
      );
      if (storedValue) {
        return storedValue;
      }
    }

    const legacyValue = toConversationArray(
      parseStoredJson(localStorage.getItem(STORAGE_KEY)),
    );
    if (!legacyValue) {
      return null;
    }

    return {
      state: { conversations: legacyValue },
      version: STORAGE_VERSION,
    };
  },
  setItem: (name, value) => {
    localStorage.setItem(name, JSON.stringify(value));
  },
  removeItem: (name) => {
    localStorage.removeItem(name);
  },
};

// ── Store ────────────────────────────────────────────────────────────

export const useChatHistoryStore = create<ChatHistoryState>()(
  persist(
    (set, get) => ({
      conversations: [],

      saveConversation: (
        messages,
        phase,
        conversationId,
        turnArtifactsByMessageId,
      ) => {
        const now = new Date().toISOString();

        // Don't save empty conversations
        if (messages.length === 0) return conversationId ?? "";

        const finalId = conversationId ?? generateId();
        const title = deriveTitle(messages);

        set((state) => {
          let updated: Conversation[];

          if (conversationId) {
            const idx = state.conversations.findIndex(
              (c) => c.id === conversationId,
            );
            if (idx >= 0) {
              const existing = state.conversations[idx];
              if (!existing) return state;
              const updatedConv: Conversation = {
                ...existing,
                messages,
                turnArtifactsByMessageId,
                phase,
                title,
                updatedAt: now,
              };
              updated = [
                updatedConv,
                ...state.conversations.filter((_, i) => i !== idx),
              ];
            } else {
              const newConv: Conversation = {
                id: conversationId,
                title,
                messages,
                turnArtifactsByMessageId,
                phase,
                createdAt: now,
                updatedAt: now,
              };
              updated = [newConv, ...state.conversations];
            }
          } else {
            const newConv: Conversation = {
              id: finalId,
              title,
              messages,
              turnArtifactsByMessageId,
              phase,
              createdAt: now,
              updatedAt: now,
            };
            updated = [newConv, ...state.conversations];
          }

          return {
            conversations: updated.slice(0, MAX_CONVERSATIONS),
          };
        });

        return finalId;
      },

      loadConversation: (id) => {
        return get().conversations.find((c) => c.id === id) ?? null;
      },

      deleteConversation: (id) => {
        set((state) => ({
          conversations: state.conversations.filter((c) => c.id !== id),
        }));
      },

      clearHistory: () => {
        set({ conversations: [] });
      },
    }),
    {
      name: VERSIONED_KEY,
      version: STORAGE_VERSION,
      storage: chatHistoryStorage,
      partialize: (state) => ({ conversations: state.conversations }),
    },
  ),
);

// ── Selector hooks ───────────────────────────────────────────────────

export const useConversations = () =>
  useChatHistoryStore((s) => s.conversations);
