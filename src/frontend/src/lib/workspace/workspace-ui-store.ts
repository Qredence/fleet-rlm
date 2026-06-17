import { create } from "zustand";

import type {
  CreationPhase,
  InspectorTab,
  WorkspaceSidepanelTab,
} from "@/lib/workspace/workspace-types";
import type { WsRuntimeContext } from "@/lib/rlm-api/ws-types";

export type SidebarTab = WorkspaceSidepanelTab;

export interface MemoryEntry {
  id: string;
  content: string;
  timestamp: string;
}

function inspectorTabToSidepanelTab(tab?: InspectorTab): WorkspaceSidepanelTab {
  if (tab === "graph" || tab === "volume" || tab === "trajectories") return tab;
  return "trajectories";
}

export interface WorkspaceUiState {
  selectedAssistantTurnId: string | null;
  activeInspectorTab: InspectorTab;
  activeSidepanelTab: WorkspaceSidepanelTab;
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
  openSidepanel: (tab?: WorkspaceSidepanelTab) => void;
  closeSidepanel: () => void;
  setSidepanelOpen: (open: boolean) => void;
  setSidepanelTab: (tab: WorkspaceSidepanelTab) => void;
  toggleSidepanel: () => void;
  toggleSidebar: () => void;
  setSidebarTab: (tab: SidebarTab) => void;
  addMemoryEntry: (entry: { content: string; timestamp: string }) => void;
  clearMemoryEntries: () => void;
}

let _nextMemoryId = 0;

export const useWorkspaceUiStore = create<WorkspaceUiState>((set, get) => ({
  selectedAssistantTurnId: null,
  activeInspectorTab: "trajectories",
  activeSidepanelTab: "trajectories",
  creationPhase: "idle",
  sessionRevision: 0,
  requestedConversationId: null,
  pendingHitlMessageId: null,
  runtimeContext: null,
  sidebarOpen: false,
  sidebarTab: "trajectories",
  memoryEntries: [],
  newSession: () =>
    set({
      creationPhase: "idle",
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectories",
      activeSidepanelTab: "trajectories",
      requestedConversationId: null,
      runtimeContext: null,
      sessionRevision: get().sessionRevision + 1,
    }),
  requestConversationLoad: (conversationId) =>
    set({
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectories",
      activeSidepanelTab: "trajectories",
      requestedConversationId: conversationId,
    }),
  clearRequestedConversation: () => set({ requestedConversationId: null }),
  openInspector: (turnId, tab) => {
    const sidepanelTab = inspectorTabToSidepanelTab(tab);
    set((state) => ({
      selectedAssistantTurnId: turnId === undefined ? state.selectedAssistantTurnId : turnId,
      activeInspectorTab: tab ?? sidepanelTab,
      activeSidepanelTab: sidepanelTab,
      sidebarOpen: true,
    }));
  },
  selectInspectorTurn: (turnId, tab) => {
    const sidepanelTab = inspectorTabToSidepanelTab(tab);
    set({
      selectedAssistantTurnId: turnId,
      activeInspectorTab: tab ?? sidepanelTab,
      activeSidepanelTab: sidepanelTab,
      sidebarOpen: true,
    });
  },
  setInspectorTab: (tab) => set({ activeInspectorTab: tab }),
  clearInspectorSelection: () =>
    set({
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectories",
      activeSidepanelTab: "trajectories",
    }),
  setCreationPhase: (creationPhase) => set({ creationPhase }),
  setPendingHitlMessageId: (pendingHitlMessageId) => set({ pendingHitlMessageId }),
  setRuntimeContext: (next) =>
    set((state) => {
      const cur = state.runtimeContext;
      if (
        cur === next ||
        (cur != null &&
          next != null &&
          cur.depth === next.depth &&
          cur.max_depth === next.max_depth &&
          cur.sandbox_active === next.sandbox_active &&
          cur.sandbox_transition === next.sandbox_transition &&
          cur.execution_mode === next.execution_mode &&
          cur.execution_profile === next.execution_profile)
      ) {
        return state;
      }
      return { runtimeContext: next };
    }),
  openSidepanel: (tab) =>
    set((state) => {
      const nextTab = tab ?? state.activeSidepanelTab;
      return {
        sidebarOpen: true,
        activeSidepanelTab: nextTab,
        activeInspectorTab: nextTab,
      };
    }),
  closeSidepanel: () => set({ sidebarOpen: false }),
  setSidepanelOpen: (open) => set({ sidebarOpen: open }),
  setSidepanelTab: (activeSidepanelTab) =>
    set({
      activeSidepanelTab,
      activeInspectorTab: activeSidepanelTab,
    }),
  toggleSidepanel: () =>
    set((state) => {
      const next = !state.sidebarOpen;
      return { sidebarOpen: next };
    }),
  toggleSidebar: () =>
    set((state) => {
      const next = !state.sidebarOpen;
      return { sidebarOpen: next };
    }),
  setSidebarTab: (sidebarTab) =>
    set({
      sidebarTab,
      activeSidepanelTab: sidebarTab,
      activeInspectorTab: sidebarTab,
    }),
  addMemoryEntry: ({ content, timestamp }) =>
    set((state) => ({
      memoryEntries: [...state.memoryEntries, { id: `mem-${++_nextMemoryId}`, content, timestamp }],
    })),
  clearMemoryEntries: () => set({ memoryEntries: [] }),
}));

export const useSelectedAssistantTurnId = () =>
  useWorkspaceUiStore((state) => state.selectedAssistantTurnId);
export const useActiveInspectorTab = () => useWorkspaceUiStore((state) => state.activeInspectorTab);
