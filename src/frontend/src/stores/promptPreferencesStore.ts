import { create } from "zustand";

import type { PromptFeature, PromptMode } from "@/lib/data/types";

interface PromptPreferencesState {
  activeFeatures: Set<PromptFeature>;
  toggleFeature: (feature: PromptFeature) => void;
  promptMode: PromptMode;
  setPromptMode: (mode: PromptMode) => void;
  selectedPromptSkills: string[];
  togglePromptSkill: (skillId: string) => void;
}

export const usePromptPreferencesStore = create<PromptPreferencesState>((set) => ({
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

  promptMode: "auto",
  setPromptMode: (mode) => set({ promptMode: mode }),

  selectedPromptSkills: [],
  togglePromptSkill: (skillId) => {
    set((state) => ({
      selectedPromptSkills: state.selectedPromptSkills.includes(skillId)
        ? state.selectedPromptSkills.filter((id) => id !== skillId)
        : [...state.selectedPromptSkills, skillId],
    }));
  },
}));

export const useActiveFeatures = () => usePromptPreferencesStore((s) => s.activeFeatures);
export const usePromptMode = () => usePromptPreferencesStore((s) => s.promptMode);
export const useSelectedPromptSkills = () =>
  usePromptPreferencesStore((s) => s.selectedPromptSkills);
