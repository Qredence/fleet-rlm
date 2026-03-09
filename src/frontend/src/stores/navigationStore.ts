/**
 * Navigation store — global navigation and session state.
 *
 * Replaces NavigationProvider context with Zustand for:
 * - Simpler testing (no mocking required)
 * - No provider wrapper needed
 * - Consistent with other stores
 */
import { create } from "zustand";
import type {
  CreationPhase,
  FsNode,
  InspectorTab,
  NavItem,
  PromptFeature,
  PromptMode,
} from "@/lib/data/types";

// ── Types ───────────────────────────────────────────────────────────

interface CanvasHandlers {
  open: () => void;
  close: () => void;
}

interface NavigationState {
  // Navigation
  activeNav: NavItem;
  setActiveNav: (nav: NavItem) => void;

  // Canvas
  isCanvasOpen: boolean;
  setIsCanvasOpen: (open: boolean) => void;
  openCanvas: () => void;
  closeCanvas: () => void;
  toggleCanvas: () => void;
  registerCanvasHandlers: (handlers: CanvasHandlers) => void;

  // Workspace inspector
  selectedAssistantTurnId: string | null;
  activeInspectorTab: InspectorTab;
  openInspector: (turnId?: string | null, tab?: InspectorTab) => void;
  selectInspectorTurn: (turnId: string, tab?: InspectorTab) => void;
  setInspectorTab: (tab: InspectorTab) => void;
  clearInspectorSelection: () => void;

  // File selection
  selectedFileNode: FsNode | null;
  selectFile: (node: FsNode | null) => void;

  // Creation phase
  creationPhase: CreationPhase;
  setCreationPhase: (phase: CreationPhase) => void;

  // Session
  newSession: () => void;
  sessionId: number;

  // Features
  activeFeatures: Set<PromptFeature>;
  toggleFeature: (feature: PromptFeature) => void;

  // Prompt mode
  promptMode: PromptMode;
  setPromptMode: (mode: PromptMode) => void;

  // Skills
  selectedPromptSkills: string[];
  togglePromptSkill: (skillId: string) => void;
}

// ── Canvas Handlers (external registration) ──────────────────────────

let canvasHandlers: CanvasHandlers = {
  open: () => {
    useNavigationStore.setState({ isCanvasOpen: true });
  },
  close: () => {
    useNavigationStore.setState({ isCanvasOpen: false });
  },
};

// ── Store ────────────────────────────────────────────────────────────

export const useNavigationStore = create<NavigationState>((set, get) => ({
  // Navigation
  activeNav: "workspace",
  setActiveNav: (nav) => {
    const prev = get().activeNav;
    set({ activeNav: nav });

    // Clear file selection when navigating away from volumes
    if (nav === "volumes" && prev !== "volumes") {
      set({ selectedFileNode: null });
      canvasHandlers.open();
    }
    if (nav !== "volumes" && prev === "volumes") {
      set({ selectedFileNode: null });
    }
  },

  // Canvas
  isCanvasOpen: false,
  setIsCanvasOpen: (open) => set({ isCanvasOpen: open }),
  openCanvas: () => canvasHandlers.open(),
  closeCanvas: () => canvasHandlers.close(),
  toggleCanvas: () => {
    if (get().isCanvasOpen) {
      canvasHandlers.close();
    } else {
      canvasHandlers.open();
    }
  },
  registerCanvasHandlers: (handlers) => {
    canvasHandlers = handlers;
    if (get().isCanvasOpen) {
      handlers.open();
    }
  },

  // Workspace inspector
  selectedAssistantTurnId: null,
  activeInspectorTab: "trajectory",
  openInspector: (turnId, tab) => {
    set((state) => ({
      selectedAssistantTurnId:
        turnId === undefined ? state.selectedAssistantTurnId : turnId,
      activeInspectorTab: tab ?? state.activeInspectorTab,
    }));
    canvasHandlers.open();
  },
  selectInspectorTurn: (turnId, tab) => {
    set((state) => ({
      selectedAssistantTurnId: turnId,
      activeInspectorTab: tab ?? state.activeInspectorTab,
    }));
    canvasHandlers.open();
  },
  setInspectorTab: (tab) => set({ activeInspectorTab: tab }),
  clearInspectorSelection: () =>
    set({
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectory",
    }),

  // File selection
  selectedFileNode: null,
  selectFile: (node) => set({ selectedFileNode: node }),

  // Creation phase
  creationPhase: "idle",
  setCreationPhase: (phase) => set({ creationPhase: phase }),

  // Session
  sessionId: 0,
  newSession: () => {
    set({
      activeNav: "workspace",
      creationPhase: "idle",
      activeFeatures: new Set(),
      promptMode: "auto",
      selectedPromptSkills: [],
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectory",
      sessionId: get().sessionId + 1,
    });
  },

  // Features
  activeFeatures: new Set(),
  toggleFeature: (feature) => {
    set((state) => {
      const next = new Set(state.activeFeatures);
      if (next.has(feature)) {
        next.delete(feature);
      } else {
        next.add(feature);
      }
      return { activeFeatures: next };
    });
  },

  // Prompt mode
  promptMode: "auto",
  setPromptMode: (mode) => set({ promptMode: mode }),

  // Skills
  selectedPromptSkills: [],
  togglePromptSkill: (skillId) => {
    set((state) => ({
      selectedPromptSkills: state.selectedPromptSkills.includes(skillId)
        ? state.selectedPromptSkills.filter((id) => id !== skillId)
        : [...state.selectedPromptSkills, skillId],
    }));
  },
}));

// ── Selector hooks for performance ───────────────────────────────────

export const useActiveNav = () => useNavigationStore((s) => s.activeNav);
export const useIsCanvasOpen = () => useNavigationStore((s) => s.isCanvasOpen);
export const useSessionId = () => useNavigationStore((s) => s.sessionId);
export const useCreationPhase = () =>
  useNavigationStore((s) => s.creationPhase);
export const useActiveFeatures = () =>
  useNavigationStore((s) => s.activeFeatures);
export const usePromptMode = () => useNavigationStore((s) => s.promptMode);
export const useSelectedPromptSkills = () =>
  useNavigationStore((s) => s.selectedPromptSkills);
export const useSelectedFileNode = () =>
  useNavigationStore((s) => s.selectedFileNode);
export const useSelectedAssistantTurnId = () =>
  useNavigationStore((s) => s.selectedAssistantTurnId);
export const useActiveInspectorTab = () =>
  useNavigationStore((s) => s.activeInspectorTab);
