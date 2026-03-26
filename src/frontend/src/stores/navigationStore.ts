/**
 * Navigation store — global shell navigation and canvas state.
 *
 * Replaces NavigationProvider context with Zustand for:
 * - Simpler testing (no mocking required)
 * - No provider wrapper needed
 * - Consistent with other stores
 */
import { create } from "zustand";
import type { NavItem } from "@/stores/navigation-types";

// ── Types ───────────────────────────────────────────────────────────

interface CanvasHandlers {
  open: () => void;
  close: () => void;
}

interface CommandPaletteHandlers {
  open: () => void;
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

  // Command palette
  openCommandPalette: () => void;
  registerCommandPaletteHandlers: (handlers: CommandPaletteHandlers) => void;
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

// ── Command Palette Handlers (external registration) ─────────────────

let commandPaletteHandlers: CommandPaletteHandlers = {
  open: () => {},
};

// ── Store ────────────────────────────────────────────────────────────

export const useNavigationStore = create<NavigationState>((set, get) => ({
  // Navigation
  activeNav: "workspace",
  setActiveNav: (activeNav) => set({ activeNav }),

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

  // Command palette
  openCommandPalette: () => commandPaletteHandlers.open(),
  registerCommandPaletteHandlers: (handlers) => {
    commandPaletteHandlers = handlers;
  },
}));

// ── Selector hooks for performance ───────────────────────────────────

export const useActiveNav = () => useNavigationStore((s) => s.activeNav);
export const useIsCanvasOpen = () => useNavigationStore((s) => s.isCanvasOpen);
