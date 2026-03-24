import { create } from "zustand";
import { persist, type PersistStorage, type StorageValue } from "zustand/middleware";

import { createLocalId } from "@/lib/id";
import type {
  ChatMessage,
  Conversation,
  CreationPhase,
  ExecutionStep,
} from "@/lib/workspace/workspace-types";

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

const STORAGE_VERSION = 2;
const STORAGE_KEY = "hax-fleet:chat-history:v2";
const MAX_CONVERSATIONS = 50;
type ChatHistoryPersistedState = Pick<ChatHistoryState, "conversations">;

function deriveTitle(messages: ChatMessage[]): string {
  const firstUser = messages.find((message) => message.type === "user");
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
    (first, second) => new Date(second.updatedAt).getTime() - new Date(first.updatedAt).getTime(),
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

export const useChatHistoryStore = create<ChatHistoryState>()(
  persist(
    (set, get) => ({
      conversations: [],

      saveConversation: (messages, phase, conversationId, turnArtifactsByMessageId) => {
        const now = new Date().toISOString();

        if (messages.length === 0) return conversationId ?? "";

        const sessionKey = logicalSessionKey(messages);
        const existingConversationId =
          conversationId ??
          (sessionKey
            ? get().conversations.find(
                (conversation) => logicalSessionKey(conversation.messages) === sessionKey,
              )?.id
            : undefined);
        const finalId = existingConversationId ?? createLocalId("conv");
        const title = deriveTitle(messages);

        set((state) => {
          let updated: Conversation[];

          if (existingConversationId) {
            const index = state.conversations.findIndex(
              (conversation) => conversation.id === existingConversationId,
            );
            if (index >= 0) {
              const existing = state.conversations[index];
              if (!existing) return state;
              const updatedConversation: Conversation = {
                ...existing,
                messages,
                turnArtifactsByMessageId,
                phase,
                title,
                updatedAt: now,
              };
              updated = [
                updatedConversation,
                ...state.conversations.filter((_, itemIndex) => itemIndex !== index),
              ];
            } else {
              const newConversation: Conversation = {
                id: finalId,
                title,
                messages,
                turnArtifactsByMessageId,
                phase,
                createdAt: now,
                updatedAt: now,
              };
              updated = [newConversation, ...state.conversations];
            }
          } else {
            const newConversation: Conversation = {
              id: finalId,
              title,
              messages,
              turnArtifactsByMessageId,
              phase,
              createdAt: now,
              updatedAt: now,
            };
            updated = [newConversation, ...state.conversations];
          }

          return {
            conversations: normalizeConversations(updated),
          };
        });

        return finalId;
      },

      loadConversation: (id) => {
        return get().conversations.find((conversation) => conversation.id === id) ?? null;
      },

      deleteConversation: (id) => {
        set((state) => ({
          conversations: state.conversations.filter((conversation) => conversation.id !== id),
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

export const useConversations = () => useChatHistoryStore((state) => state.conversations);
