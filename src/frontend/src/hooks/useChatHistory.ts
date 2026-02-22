/**
 * useChatHistory — manages conversation history with localStorage persistence.
 *
 * Stores an array of past conversations (id, title, messages, phase,
 * createdAt, updatedAt). Exposes CRUD operations and an auto-save helper.
 *
 * Conversations are stored as JSON in localStorage under the key
 * `hax-fleet:chat-history`. The list is capped at MAX_CONVERSATIONS.
 */

import { useState, useCallback, useRef, useEffect } from "react";
import type { ChatMessage, CreationPhase } from "@/lib/data/types";
import { createLocalId } from "@/lib/id";

// ── Types ────────────────────────────────────────────────────────────

export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  phase: CreationPhase;
  createdAt: string;
  updatedAt: string;
}

// ── Constants ────────────────────────────────────────────────────────

const STORAGE_VERSION = 1;
const STORAGE_KEY = "hax-fleet:chat-history";
const VERSIONED_KEY = `${STORAGE_KEY}:v${STORAGE_VERSION}`;
const MAX_CONVERSATIONS = 50;

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

function loadFromStorage(): Conversation[] {
  try {
    // Migrate from unversioned key if present
    const legacy = localStorage.getItem(STORAGE_KEY);
    if (legacy && !localStorage.getItem(VERSIONED_KEY)) {
      localStorage.setItem(VERSIONED_KEY, legacy);
      localStorage.removeItem(STORAGE_KEY);
    }

    const raw = localStorage.getItem(VERSIONED_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed as Conversation[];
  } catch {
    return [];
  }
}

function saveToStorage(conversations: Conversation[]) {
  try {
    localStorage.setItem(
      VERSIONED_KEY,
      JSON.stringify(conversations.slice(0, MAX_CONVERSATIONS)),
    );
  } catch {
    // Storage full or unavailable — silently degrade
  }
}

// ── Hook ─────────────────────────────────────────────────────────────

export interface ChatHistory {
  /** All saved conversations, newest first */
  conversations: Conversation[];

  /**
   * Save the current conversation. If `conversationId` is provided,
   * updates an existing conversation; otherwise creates a new one.
   * Returns the id of the saved conversation.
   */
  saveConversation: (
    messages: ChatMessage[],
    phase: CreationPhase,
    conversationId?: string | null,
  ) => string;

  /** Load a conversation by id. Returns null if not found. */
  loadConversation: (id: string) => Conversation | null;

  /** Delete a conversation by id. */
  deleteConversation: (id: string) => void;

  /** Clear all history. */
  clearHistory: () => void;
}

export function useChatHistory(): ChatHistory {
  const [conversations, setConversations] = useState<Conversation[]>(() =>
    loadFromStorage(),
  );

  // Keep a ref in sync for stable callbacks
  const conversationsRef = useRef(conversations);
  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  const saveConversation = useCallback(
    (
      messages: ChatMessage[],
      phase: CreationPhase,
      conversationId?: string | null,
    ): string => {
      const now = new Date().toISOString();

      // Don't save empty conversations - return empty string if no id provided
      if (messages.length === 0) return conversationId ?? "";

      // Determine the ID upfront to avoid race condition with ref sync
      const finalId = conversationId ?? generateId();
      const title = deriveTitle(messages);

      setConversations((prev) => {
        let updated: Conversation[];

        if (conversationId) {
          // Update existing
          const idx = prev.findIndex((c) => c.id === conversationId);
          if (idx >= 0) {
            const existing = prev[idx];
            if (!existing) {
              return prev;
            }
            const updatedConv: Conversation = {
              ...existing,
              messages,
              phase,
              title,
              updatedAt: now,
            };
            // Move to front (most recent)
            updated = [updatedConv, ...prev.filter((_, i) => i !== idx)];
          } else {
            // Not found — create new with the provided id
            const newConv: Conversation = {
              id: conversationId,
              title,
              messages,
              phase,
              createdAt: now,
              updatedAt: now,
            };
            updated = [newConv, ...prev];
          }
        } else {
          // Create new with the pre-determined id
          const newConv: Conversation = {
            id: finalId,
            title,
            messages,
            phase,
            createdAt: now,
            updatedAt: now,
          };
          updated = [newConv, ...prev];
        }

        // Enforce cap
        const capped = updated.slice(0, MAX_CONVERSATIONS);
        saveToStorage(capped);
        return capped;
      });

      // Return the pre-determined id directly - no race condition
      return finalId;
    },
    [],
  );

  const loadConversation = useCallback((id: string): Conversation | null => {
    return conversationsRef.current.find((c) => c.id === id) ?? null;
  }, []);

  const deleteConversation = useCallback((id: string) => {
    setConversations((prev) => {
      const updated = prev.filter((c) => c.id !== id);
      saveToStorage(updated);
      return updated;
    });
  }, []);

  const clearHistory = useCallback(() => {
    setConversations([]);
    saveToStorage([]);
  }, []);

  return {
    conversations,
    saveConversation,
    loadConversation,
    deleteConversation,
    clearHistory,
  };
}
