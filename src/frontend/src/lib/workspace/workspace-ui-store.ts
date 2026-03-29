import { create } from "zustand";

import type { CreationPhase, InspectorTab } from "@/lib/workspace/workspace-types";
import { useNavigationStore } from "@/stores/navigation-store";

export interface WorkspaceUiState {
  selectedAssistantTurnId: string | null;
  activeInspectorTab: InspectorTab;
  creationPhase: CreationPhase;
  sessionRevision: number;
  requestedConversationId: string | null;
  newSession: () => void;
  requestConversationLoad: (conversationId: string) => void;
  clearRequestedConversation: () => void;
  openInspector: (turnId?: string | null, tab?: InspectorTab) => void;
  selectInspectorTurn: (turnId: string, tab?: InspectorTab) => void;
  setInspectorTab: (tab: InspectorTab) => void;
  clearInspectorSelection: () => void;
  setCreationPhase: (phase: CreationPhase) => void;
}

function openShellCanvas() {
  useNavigationStore.getState().openCanvas();
}

export const useWorkspaceUiStore = create<WorkspaceUiState>((set, get) => ({
  selectedAssistantTurnId: null,
  activeInspectorTab: "trajectory",
  creationPhase: "idle",
  sessionRevision: 0,
  requestedConversationId: null,
  newSession: () =>
    set({
      creationPhase: "idle",
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectory",
      requestedConversationId: null,
      sessionRevision: get().sessionRevision + 1,
    }),
  requestConversationLoad: (conversationId) =>
    set({
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectory",
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
      activeInspectorTab: "trajectory",
    }),
  setCreationPhase: (creationPhase) => set({ creationPhase }),
}));

export const useSelectedAssistantTurnId = () =>
  useWorkspaceUiStore((state) => state.selectedAssistantTurnId);
export const useActiveInspectorTab = () => useWorkspaceUiStore((state) => state.activeInspectorTab);
