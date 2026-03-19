/**
 * Chat history store — manages conversation history with localStorage persistence.
 *
 * Stores an array of past conversations (id, title, messages, phase,
 * createdAt, updatedAt). Exposes CRUD operations.
 *
 * Uses Zustand persist middleware for automatic localStorage sync.
 */
import { create } from "zustand";
import { persist, type PersistStorage, type StorageValue } from "zustand/middleware";
import type { ExecutionStep } from "@/screens/workspace/model/artifact-types";
import type { ChatMessage, CreationPhase } from "@/screens/workspace/model/workspace-types";
import { createLocalId } from "@/lib/id";

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
const STORAGE_KEY = "hax-fleet:chat-history:v2";
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

function logicalSessionKey(messages: ChatMessage[]): string | null {
  const firstMessage = messages[0];
  if (!firstMessage) return null;
  return `${firstMessage.type}:${firstMessage.id}`;
}

function sortByUpdatedAtDesc(conversations: Conversation[]): Conversation[] {
  return [...conversations].sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

function normalizeConversations(conversations: Conversation[]): Conversation[] {
  const dedupedBySession = new Map<string, Conversation>();
  const withoutSessionKey: Conversation[] = [];

  for (const conversation of sortByUpdatedAtDesc(conversations)) {
    const sessionKey = logicalSessionKey(conversation.messages);
    if (!sessionKey) {
      withoutSessionKey.push(conversation);
      continue;
    }
    if (!dedupedBySession.has(sessionKey)) {
      dedupedBySession.set(sessionKey, conversation);
    }
  }

  return [...dedupedBySession.values(), ...withoutSessionKey].slice(0, MAX_CONVERSATIONS);
}

function parseStoredJson(raw: string | null): unknown {
  try {
    return raw ? (JSON.parse(raw) as unknown) : null;
  } catch {
    return null;
  }
}

function toConversationArray(value: unknown): Conversation[] | null {
  return Array.isArray(value) ? normalizeConversations(value as Conversation[]) : null;
}

function toPersistedChatHistory(value: unknown): StorageValue<ChatHistoryPersistedState> | null {
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
    version: typeof maybePersisted.version === "number" ? maybePersisted.version : STORAGE_VERSION,
  };
}

const chatHistoryStorage: PersistStorage<ChatHistoryPersistedState> = {
  getItem: (name) => {
    return toPersistedChatHistory(parseStoredJson(localStorage.getItem(name)));
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

      saveConversation: (messages, phase, conversationId, turnArtifactsByMessageId) => {
        const now = new Date().toISOString();

        // Don't save empty conversations
        if (messages.length === 0) return conversationId ?? "";

        const sessionKey = logicalSessionKey(messages);
        const existingConversationId =
          conversationId ??
          (sessionKey
            ? get().conversations.find(
                (conversation) => logicalSessionKey(conversation.messages) === sessionKey,
              )?.id
            : undefined);
        const finalId = existingConversationId ?? generateId();
        const title = deriveTitle(messages);

        set((state) => {
          let updated: Conversation[];

          if (existingConversationId) {
            const idx = state.conversations.findIndex((c) => c.id === existingConversationId);
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
              updated = [updatedConv, ...state.conversations.filter((_, i) => i !== idx)];
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
            conversations: normalizeConversations(updated),
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
      name: STORAGE_KEY,
      version: STORAGE_VERSION,
      storage: chatHistoryStorage,
      partialize: (state) => ({ conversations: state.conversations }),
    },
  ),
);

// ── Selector hooks ───────────────────────────────────────────────────

export const useConversations = () => useChatHistoryStore((s) => s.conversations);
