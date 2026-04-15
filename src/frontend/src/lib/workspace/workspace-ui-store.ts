import { create } from "zustand";

import type { CreationPhase, InspectorTab } from "@/lib/workspace/workspace-types";
import type { WsRuntimeContext } from "@/lib/rlm-api/ws-types";
import { useNavigationStore } from "@/stores/navigation-store";

export type SidebarTab = "documents" | "memory" | "context" | "checkpoint";

export interface MemoryEntry {
  id: string;
  content: string;
  timestamp: string;
}

function readSidebarOpen(): boolean {
  try {
    return localStorage.getItem("workspace.sidebarOpen") === "true";
  } catch {
    return false;
  }
}

function writeSidebarOpen(value: boolean): void {
  try {
    localStorage.setItem("workspace.sidebarOpen", value ? "true" : "false");
  } catch {
    // ignore
  }
}

export interface WorkspaceUiState {
  selectedAssistantTurnId: string | null;
  activeInspectorTab: InspectorTab;
  creationPhase: CreationPhase;
  sessionRevision: number;
  requestedConversationId: string | null;
  pendingHitlMessageId: string | null;
  runtimeContext: WsRuntimeContext | null;
  sidebarOpen: boolean;
  sidebarTab: SidebarTab;
  memoryEntries: MemoryEntry[];
  newSession: () => void;
  requestConversationLoad: (conversationId: string) => void;
  clearRequestedConversation: () => void;
  openInspector: (turnId?: string | null, tab?: InspectorTab) => void;
  selectInspectorTurn: (turnId: string, tab?: InspectorTab) => void;
  setInspectorTab: (tab: InspectorTab) => void;
  clearInspectorSelection: () => void;
  setCreationPhase: (phase: CreationPhase) => void;
  setPendingHitlMessageId: (id: string | null) => void;
  setRuntimeContext: (ctx: WsRuntimeContext | null) => void;
  toggleSidebar: () => void;
  setSidebarTab: (tab: SidebarTab) => void;
  addMemoryEntry: (entry: { content: string; timestamp: string }) => void;
  clearMemoryEntries: () => void;
}

function openShellCanvas() {
  useNavigationStore.getState().openCanvas();
}

let _nextMemoryId = 0;

export const useWorkspaceUiStore = create<WorkspaceUiState>((set, get) => ({
  selectedAssistantTurnId: null,
  activeInspectorTab: "message",
  creationPhase: "idle",
  sessionRevision: 0,
  requestedConversationId: null,
  pendingHitlMessageId: null,
  runtimeContext: null,
  sidebarOpen: readSidebarOpen(),
  sidebarTab: "memory",
  memoryEntries: [],
  newSession: () =>
    set({
      creationPhase: "idle",
      selectedAssistantTurnId: null,
      activeInspectorTab: "message",
      requestedConversationId: null,
      runtimeContext: null,
      sessionRevision: get().sessionRevision + 1,
    }),
  requestConversationLoad: (conversationId) =>
    set({
      selectedAssistantTurnId: null,
      activeInspectorTab: "message",
      requestedConversationId: conversationId,
    }),
  clearRequestedConversation: () => set({ requestedConversationId: null }),
  openInspector: (turnId, tab) => {
    set((state) => ({
      selectedAssistantTurnId: turnId === undefined ? state.selectedAssistantTurnId : turnId,
      activeInspectorTab: tab ?? state.activeInspectorTab,
    }));
    openShellCanvas();
  },
  selectInspectorTurn: (turnId, tab) => {
    set((state) => ({
      selectedAssistantTurnId: turnId,
      activeInspectorTab: tab ?? state.activeInspectorTab,
    }));
    openShellCanvas();
  },
  setInspectorTab: (tab) => set({ activeInspectorTab: tab }),
  clearInspectorSelection: () =>
    set({
      selectedAssistantTurnId: null,
      activeInspectorTab: "message",
    }),
  setCreationPhase: (creationPhase) => set({ creationPhase }),
  setPendingHitlMessageId: (pendingHitlMessageId) => set({ pendingHitlMessageId }),
  setRuntimeContext: (runtimeContext) => set({ runtimeContext }),
  toggleSidebar: () =>
    set((state) => {
      const next = !state.sidebarOpen;
      writeSidebarOpen(next);
      return { sidebarOpen: next };
    }),
  setSidebarTab: (sidebarTab) => set({ sidebarTab }),
  addMemoryEntry: ({ content, timestamp }) =>
    set((state) => ({
      memoryEntries: [...state.memoryEntries, { id: `mem-${++_nextMemoryId}`, content, timestamp }],
    })),
  clearMemoryEntries: () => set({ memoryEntries: [] }),
}));

export const useSelectedAssistantTurnId = () =>
  useWorkspaceUiStore((state) => state.selectedAssistantTurnId);
export const useActiveInspectorTab = () => useWorkspaceUiStore((state) => state.activeInspectorTab);
