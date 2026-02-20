/**
 * Centralised navigation & app-level state context.
 *
 * Owns: active tab, canvas open/closed, selected skill, creation phase, theme,
 *       prompt feature toggles, prompt mode, and prompt-scoped skill selection.
 *
 * Canvas control:
 *   - `openCanvas`, `closeCanvas`, `toggleCanvas` are stable refs whose
 *     underlying implementation can be swapped by the active shell
 *     (DesktopShell registers panel-aware handlers; MobileShell uses defaults).
 *   - `setIsCanvasOpen` is retained for Panel `onCollapse`/`onExpand` callbacks.
 *
 * Prompt features:
 *   - `activeFeatures` / `toggleFeature` — persisted across tab navigation
 *   - Toggling `library` automatically opens/closes the canvas panel
 *   - `promptMode` / `setPromptMode` — execution mode (Auto, Skill Creation, …)
 *   - `selectedPromptSkills` / `togglePromptSkill` — skill scoping for prompts
 *
 * Does NOT own layout-specific concerns (panel refs, resize state) — those
 * live in DesktopShell / MobileShell.
 */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
  type ReactNode,
} from "react";
import type {
  NavItem,
  CreationPhase,
  PromptFeature,
  PromptMode,
  FsNode,
} from "../data/types";
import { useTheme } from "./useTheme";

// ── Canvas handler shape ────────────────────────────────────────────

interface CanvasHandlers {
  open: () => void;
  close: () => void;
}

// ── Context shape ───────────────────────────────────────────────────

interface NavigationContextValue {
  /* Navigation */
  activeNav: NavItem;
  setActiveNav: (nav: NavItem) => void;

  /* Builder / canvas panel */
  isCanvasOpen: boolean;
  setIsCanvasOpen: (open: boolean) => void;
  openCanvas: () => void;
  closeCanvas: () => void;
  toggleCanvas: () => void;

  /**
   * Shells call this once to register panel-aware open/close handlers.
   * The default handlers simply toggle `isCanvasOpen` state.
   */
  registerCanvasHandlers: (handlers: CanvasHandlers) => void;

  /* Skill selection */
  selectedSkillId: string | null;
  selectSkill: (id: string | null) => void;

  /* Filesystem file selection (non-skill files in sandbox browser) */
  selectedFileNode: FsNode | null;
  selectFile: (node: FsNode | null) => void;

  /* Creation flow phase */
  creationPhase: CreationPhase;
  setCreationPhase: (phase: CreationPhase) => void;

  /* Convenience */
  newSession: () => void;

  /**
   * Monotonically increasing counter. Incremented by `newSession()`.
   * Consumers (e.g. useChatSimulation) can watch this value to reset
   * their local state without a registration pattern.
   */
  sessionId: number;

  /* Theme */
  isDark: boolean;
  toggleTheme: () => void;

  /* Prompt feature state (persisted across navigation) */
  activeFeatures: Set<PromptFeature>;
  toggleFeature: (feature: PromptFeature) => void;
  promptMode: PromptMode;
  setPromptMode: (mode: PromptMode) => void;
  selectedPromptSkills: string[];
  togglePromptSkill: (skillId: string) => void;
}

// ── HMR-safe default value ──────────────────────────────────────────
// During Hot Module Replacement the context module re-evaluates and
// creates a NEW context object while the provider tree still holds the
// OLD one. Supplying a complete default prevents the "must be used
// within <NavigationProvider>" throw during HMR refresh.

const noop = () => {};

const defaultCtx: NavigationContextValue = {
  activeNav: "new",
  setActiveNav: noop,
  isCanvasOpen: false,
  setIsCanvasOpen: noop,
  openCanvas: noop,
  closeCanvas: noop,
  toggleCanvas: noop,
  registerCanvasHandlers: noop,
  selectedSkillId: null,
  selectSkill: noop,
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

// ── Provider ────────────────────────────────────────────────────────

interface ProviderProps {
  children: ReactNode;
}

function NavigationProvider({ children }: ProviderProps) {
  const { isDark, toggle: toggleTheme } = useTheme();
  const [activeNav, setActiveNav] = useState<NavItem>("new");
  const [isCanvasOpen, setIsCanvasOpen] = useState(false);
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const [creationPhase, setCreationPhase] = useState<CreationPhase>("idle");

  // ── Canvas handler registration ─────────────────────────────────
  // Default handlers simply toggle state (sufficient for mobile).
  // DesktopShell overrides with panel-ref-aware versions.
  const canvasHandlersRef = useRef<CanvasHandlers>({
    open: () => setIsCanvasOpen(true),
    close: () => setIsCanvasOpen(false),
  });

  // Track isCanvasOpen in a ref so toggleCanvas stays stable
  const isCanvasOpenRef = useRef(isCanvasOpen);
  useEffect(() => {
    isCanvasOpenRef.current = isCanvasOpen;
  }, [isCanvasOpen]);

  const openCanvas = useCallback(() => {
    canvasHandlersRef.current.open();
  }, []);

  const closeCanvas = useCallback(() => {
    canvasHandlersRef.current.close();
  }, []);

  const toggleCanvas = useCallback(() => {
    if (isCanvasOpenRef.current) {
      canvasHandlersRef.current.close();
    } else {
      canvasHandlersRef.current.open();
    }
  }, []);

  const registerCanvasHandlers = useCallback((handlers: CanvasHandlers) => {
    canvasHandlersRef.current = handlers;
  }, []);

  // ── Other actions ────────────────────────────────────────────────

  const selectSkill = useCallback((id: string | null) => {
    setSelectedSkillId(id);
  }, []);

  // ── Filesystem file selection (non-skill files in sandbox browser) ──
  const [selectedFileNode, setSelectedFileNode] = useState<FsNode | null>(null);
  const selectFile = useCallback((node: FsNode | null) => {
    setSelectedFileNode(node);
  }, []);

  // ── Taxonomy tab → auto-open canvas for graph view ─────────────
  const prevActiveNavRef = useRef(activeNav);
  useEffect(() => {
    const prev = prevActiveNavRef.current;
    prevActiveNavRef.current = activeNav;

    if (activeNav === "taxonomy" && prev !== "taxonomy") {
      // Deselect any skill so the graph shows (not SkillDetail)
      setSelectedSkillId(null);
      setSelectedFileNode(null);
      canvasHandlersRef.current.open();
    }

    // Clear file selection when leaving taxonomy
    if (activeNav !== "taxonomy" && prev === "taxonomy") {
      setSelectedFileNode(null);
    }
  }, [activeNav]);

  // ── Prompt feature state ────────────────────────────────────────

  const [activeFeatures, setActiveFeatures] = useState<Set<PromptFeature>>(
    new Set(),
  );

  const toggleFeature = useCallback((feature: PromptFeature) => {
    setActiveFeatures((prev) => {
      const next = new Set(prev);
      if (next.has(feature)) {
        next.delete(feature);
      } else {
        next.add(feature);
      }
      return next;
    });
  }, []);

  // ── Library ↔ Canvas bi-directional sync ───────────────────────
  //
  // Forward: when the library feature is toggled ON/OFF, open/close
  //          the canvas panel accordingly.
  // Reverse: when the canvas is closed externally (panel X button,
  //          drag-collapse, etc.), deactivate the library feature.

  const prevHasLibraryRef = useRef(false);
  const prevHasContextMemoryRef = useRef(false);

  useEffect(() => {
    const hasLibrary = activeFeatures.has("library");
    const prevHasLibrary = prevHasLibraryRef.current;
    prevHasLibraryRef.current = hasLibrary;

    const hasContextMemory = activeFeatures.has("contextMemory");
    const prevHasContextMemory = prevHasContextMemoryRef.current;
    prevHasContextMemoryRef.current = hasContextMemory;

    // Library sync
    if (hasLibrary && !prevHasLibrary) {
      canvasHandlersRef.current.open();
    } else if (!hasLibrary && prevHasLibrary && !hasContextMemory) {
      canvasHandlersRef.current.close();
    }

    // Context Memory sync — opens canvas for code artifact sandbox
    if (hasContextMemory && !prevHasContextMemory) {
      canvasHandlersRef.current.open();
    } else if (!hasContextMemory && prevHasContextMemory && !hasLibrary) {
      canvasHandlersRef.current.close();
    }
  }, [activeFeatures]);

  // Reverse sync: canvas closed → deactivate library & contextMemory features
  useEffect(() => {
    if (!isCanvasOpen) {
      setActiveFeatures((prev) => {
        if (!prev.has("library") && !prev.has("contextMemory")) return prev;
        const next = new Set(prev);
        next.delete("library");
        next.delete("contextMemory");
        return next;
      });
    }
  }, [isCanvasOpen]);

  const [promptMode, setPromptMode] = useState<PromptMode>("auto");

  const [selectedPromptSkills, setSelectedPromptSkills] = useState<string[]>(
    [],
  );
  const togglePromptSkill = useCallback((skillId: string) => {
    setSelectedPromptSkills((prev) =>
      prev.includes(skillId)
        ? prev.filter((id) => id !== skillId)
        : [...prev, skillId],
    );
  }, []);

  // ── Convenience actions ─────────────────────────────────────────

  const [sessionId, setSessionId] = useState(0);

  const newSession = useCallback(() => {
    setActiveNav("new");
    setCreationPhase("idle");
    // Reset all prompt feature state for a clean slate
    setActiveFeatures(new Set());
    setPromptMode("auto");
    setSelectedPromptSkills([]);
    setSessionId((prev) => prev + 1);
  }, []);

  const value: NavigationContextValue = {
    activeNav,
    setActiveNav,
    isCanvasOpen,
    setIsCanvasOpen,
    openCanvas,
    closeCanvas,
    toggleCanvas,
    registerCanvasHandlers,
    selectedSkillId,
    selectSkill,
    creationPhase,
    setCreationPhase,
    newSession,
    sessionId,
    isDark,
    toggleTheme,
    activeFeatures,
    toggleFeature,
    promptMode,
    setPromptMode,
    selectedPromptSkills,
    togglePromptSkill,
    selectedFileNode,
    selectFile,
  };

  return (
    <NavigationContext.Provider value={value}>
      {children}
    </NavigationContext.Provider>
  );
}

// ── Hook ────────────────────────────────────────────────────────────

function useNavigation(): NavigationContextValue {
  return useContext(NavigationContext);
}

export { NavigationProvider, useNavigation };
export type { NavigationContextValue };
