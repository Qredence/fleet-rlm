/**
 * Theme store — dark mode toggle with localStorage persistence.
 */
import { create } from "zustand";
import { persist, type PersistStorage, type StorageValue } from "zustand/middleware";
import { telemetryClient } from "@/lib/telemetry/client";
import { parseStoredJson } from "@/lib/utils/env";

// ── Types ───────────────────────────────────────────────────────────

interface ThemeState {
  isDark: boolean;
  toggle: () => void;
  setDark: (dark: boolean) => void;
}

const THEME_STORAGE_KEY = "theme-storage";
const THEME_STORAGE_VERSION = 1;
type ThemePersistedState = Pick<ThemeState, "isDark">;

function applyThemeToDocument(isDark: boolean) {
  const root = document.documentElement;
  root.dataset.theme = isDark ? "dark" : "light";
  root.classList.toggle("dark", isDark);
  root.style.colorScheme = isDark ? "dark" : "light";
}

function toPersistedTheme(value: unknown): StorageValue<ThemePersistedState> | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }

  const maybePersisted = value as {
    state?: unknown;
    version?: unknown;
  };
  const maybeState = maybePersisted.state;
  if (typeof maybeState !== "object" || maybeState === null) {
    return null;
  }

  const isDark = (maybeState as { isDark?: unknown }).isDark;
  if (typeof isDark !== "boolean") {
    return null;
  }

  return {
    state: { isDark },
    version:
      typeof maybePersisted.version === "number" ? maybePersisted.version : THEME_STORAGE_VERSION,
  };
}

const themeStorage: PersistStorage<ThemePersistedState> = {
  getItem: (name) => {
    return toPersistedTheme(parseStoredJson(localStorage.getItem(name)));
  },
  setItem: (name, value) => {
    localStorage.setItem(name, JSON.stringify(value));
  },
  removeItem: (name) => {
    localStorage.removeItem(name);
  },
};

// ── Store ────────────────────────────────────────────────────────────

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      isDark: false,

      toggle: () => {
        document.documentElement.classList.add("theme-transition");
        const next = !get().isDark;
        set({ isDark: next });
        applyThemeToDocument(next);

        // Telemetry
        telemetryClient.capture("theme_toggled", {
          new_theme: next ? "dark" : "light",
          previous_theme: next ? "light" : "dark",
        });

        setTimeout(() => document.documentElement.classList.remove("theme-transition"), 300);
      },

      setDark: (dark) => {
        set({ isDark: dark });
        applyThemeToDocument(dark);
      },
    }),
    {
      name: THEME_STORAGE_KEY,
      version: THEME_STORAGE_VERSION,
      storage: themeStorage,
      partialize: (state) => ({ isDark: state.isDark }),
      onRehydrateStorage: () => (state) => {
        applyThemeToDocument(state?.isDark ?? false);
      },
    },
  ),
);

// ── Selector hooks ───────────────────────────────────────────────────

export const useIsDark = () => useThemeStore((s) => s.isDark);
