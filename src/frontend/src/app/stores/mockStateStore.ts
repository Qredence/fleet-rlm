/**
 * Zustand store for mock state management.
 *
 * This store centralizes mock data for hooks that need to manage local state
 * in mock mode. Using a store instead of module-level variables ensures:
 * - State is properly isolated per-store-instance (important for tests)
 * - HMR (Hot Module Replacement) works correctly
 * - State can be reset/cleared for testing
 *
 * @example
 * ```tsx
 * // In a test, reset state between tests
 * beforeEach(() => {
 *   useMockStateStore.getState().reset();
 * });
 * ```
 */
import { create } from "zustand";
import { mockMemoryEntries } from "../components/data/mock-skills";
import type { MemoryEntry } from "../components/data/types";

// ── Types ───────────────────────────────────────────────────────────

export interface MockSession {
  id: string;
  userId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  status: "active" | "completed" | "archived";
  metadata?: Record<string, unknown>;
}

// ── Initial State Factory ────────────────────────────────────────────

function createInitialState() {
  return {
    // Memory mock state
    memoryEntries: [...mockMemoryEntries] as MemoryEntry[],

    // Session mock state
    sessions: [
      {
        id: "session-default",
        userId: "usr_01",
        title: "Test Generation Skill",
        createdAt: "2026-02-15T10:00:00Z",
        updatedAt: "2026-02-15T10:05:00Z",
        status: "active" as const,
      },
    ] as MockSession[],
  };
}

// ── Store Interface ──────────────────────────────────────────────────

interface MockState {
  // State
  memoryEntries: MemoryEntry[];
  sessions: MockSession[];

  // Memory actions
  addMemoryEntry: (entry: MemoryEntry) => void;
  updateMemoryEntry: (
    id: string,
    patch: Partial<
      Pick<MemoryEntry, "content" | "tags" | "pinned" | "relevance">
    >,
  ) => void;
  removeMemoryEntry: (id: string) => void;
  bulkUpdateMemoryPinned: (ids: string[], pinned: boolean) => void;
  bulkRemoveMemoryEntries: (ids: string[]) => void;

  // Session actions
  addSession: (session: MockSession) => void;

  // Reset (useful for testing)
  reset: () => void;
}

// ── Store ────────────────────────────────────────────────────────────

export const useMockStateStore = create<MockState>((set) => ({
  // Initial state
  ...createInitialState(),

  // Memory actions
  addMemoryEntry: (entry) =>
    set((state) => ({
      memoryEntries: [entry, ...state.memoryEntries],
    })),

  updateMemoryEntry: (id, patch) =>
    set((state) => ({
      memoryEntries: state.memoryEntries.map((e) =>
        e.id === id
          ? { ...e, ...patch, updatedAt: new Date().toISOString() }
          : e,
      ),
    })),

  removeMemoryEntry: (id) =>
    set((state) => ({
      memoryEntries: state.memoryEntries.filter((e) => e.id !== id),
    })),

  bulkUpdateMemoryPinned: (ids, pinned) =>
    set((state) => ({
      memoryEntries: state.memoryEntries.map((e) =>
        ids.includes(e.id)
          ? { ...e, pinned, updatedAt: new Date().toISOString() }
          : e,
      ),
    })),

  bulkRemoveMemoryEntries: (ids) =>
    set((state) => ({
      memoryEntries: state.memoryEntries.filter((e) => !ids.includes(e.id)),
    })),

  // Session actions
  addSession: (session) =>
    set((state) => ({
      sessions: [...state.sessions, session],
    })),

  // Reset
  reset: () => set(createInitialState()),
}));
