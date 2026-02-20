import type {
  CreationPhase,
  FsNode,
  NavItem,
  PromptFeature,
  PromptMode,
} from "@/lib/data/types";

interface CanvasHandlers {
  open: () => void;
  close: () => void;
}

interface NavigationContextValue {
  activeNav: NavItem;
  setActiveNav: (nav: NavItem) => void;
  isCanvasOpen: boolean;
  setIsCanvasOpen: (open: boolean) => void;
  openCanvas: () => void;
  closeCanvas: () => void;
  toggleCanvas: () => void;
  registerCanvasHandlers: (handlers: CanvasHandlers) => void;
  selectedSkillId: string | null;
  selectSkill: (id: string | null) => void;
  selectedFileNode: FsNode | null;
  selectFile: (node: FsNode | null) => void;
  creationPhase: CreationPhase;
  setCreationPhase: (phase: CreationPhase) => void;
  newSession: () => void;
  sessionId: number;
  isDark: boolean;
  toggleTheme: () => void;
  activeFeatures: Set<PromptFeature>;
  toggleFeature: (feature: PromptFeature) => void;
  promptMode: PromptMode;
  setPromptMode: (mode: PromptMode) => void;
  selectedPromptSkills: string[];
  togglePromptSkill: (skillId: string) => void;
}

export type { CanvasHandlers, NavigationContextValue };
