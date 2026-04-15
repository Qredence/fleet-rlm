import { create } from "zustand";

import type { CreationPhase, InspectorTab } from "@/lib/workspace/workspace-types";
import { useNavigationStore } from "@/stores/navigation-store";

export interface WorkspaceUiState {
  selectedAssistantTurnId: string | null;
  activeInspectorTab: InspectorTab;
  creationPhase: CreationPhase;
  sessionRevision: number;
  requestedConversationId: string | null;
  pendingHitlMessageId: string | null;
  newSession: () => void;
  requestConversationLoad: (conversationId: string) => void;
  clearRequestedConversation: () => void;
  openInspector: (turnId?: string | null, tab?: InspectorTab) => void;
  selectInspectorTurn: (turnId: string, tab?: InspectorTab) => void;
  setInspectorTab: (tab: InspectorTab) => void;
  clearInspectorSelection: () => void;
  setCreationPhase: (phase: CreationPhase) => void;
  setPendingHitlMessageId: (id: string | null) => void;
}

function openShellCanvas() {
  useNavigationStore.getState().openCanvas();
}

export const useWorkspaceUiStore = create<WorkspaceUiState>((set, get) => ({
  selectedAssistantTurnId: null,
  activeInspectorTab: "message",
  creationPhase: "idle",
  sessionRevision: 0,
  requestedConversationId: null,
  pendingHitlMessageId: null,
  newSession: () =>
    set({
      creationPhase: "idle",
      selectedAssistantTurnId: null,
      activeInspectorTab: "message",
      requestedConversationId: null,
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
}));

export const useSelectedAssistantTurnId = () =>
  useWorkspaceUiStore((state) => state.selectedAssistantTurnId);
export const useActiveInspectorTab = () => useWorkspaceUiStore((state) => state.activeInspectorTab);
