import { createContext, useContext } from "react";

import type { NavigationContextValue } from "@/hooks/navigation-types";

const noop = () => {};

const defaultCtx: NavigationContextValue = {
  activeNav: "workspace",
  setActiveNav: noop,
  isCanvasOpen: false,
  setIsCanvasOpen: noop,
  openCanvas: noop,
  closeCanvas: noop,
  toggleCanvas: noop,
  registerCanvasHandlers: noop,
  creationPhase: "idle",
  setCreationPhase: noop,
  newSession: noop,
  sessionId: 0,
  isDark: false,
  toggleTheme: noop,
  activeFeatures: new Set(),
  toggleFeature: noop,
  promptMode: "auto",
  setPromptMode: noop,
  selectedPromptSkills: [],
  togglePromptSkill: noop,
  selectedFileNode: null,
  selectFile: noop,
};

const NavigationContext = createContext<NavigationContextValue>(defaultCtx);

function useNavigation(): NavigationContextValue {
  return useContext(NavigationContext);
}

export { NavigationContext, useNavigation };
