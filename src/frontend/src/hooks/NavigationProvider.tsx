import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { NavigationContext } from "@/hooks/navigation-context";
import { useTheme } from "@/hooks/useTheme";
import type {
  CreationPhase,
  FsNode,
  NavItem,
  PromptFeature,
  PromptMode,
} from "@/lib/data/types";
import type {
  CanvasHandlers,
  NavigationContextValue,
} from "@/hooks/navigation-types";

interface ProviderProps {
  children: ReactNode;
}

function NavigationProvider({ children }: ProviderProps) {
  const { isDark, toggle: toggleTheme } = useTheme();
  const [activeNav, setActiveNav] = useState<NavItem>("workspace");
  const [isCanvasOpen, setIsCanvasOpen] = useState(false);
  const [creationPhase, setCreationPhase] = useState<CreationPhase>("idle");

  const canvasHandlersRef = useRef<CanvasHandlers>({
    open: () => setIsCanvasOpen(true),
    close: () => setIsCanvasOpen(false),
  });

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

  const [selectedFileNode, setSelectedFileNode] = useState<FsNode | null>(null);
  const selectFile = useCallback((node: FsNode | null) => {
    setSelectedFileNode(node);
  }, []);

  const prevActiveNavRef = useRef(activeNav);
  useEffect(() => {
    const prev = prevActiveNavRef.current;
    prevActiveNavRef.current = activeNav;

    if (activeNav === "volumes" && prev !== "volumes") {
      setSelectedFileNode(null);
      canvasHandlersRef.current.open();
    }

    if (activeNav !== "volumes" && prev === "volumes") {
      setSelectedFileNode(null);
    }
  }, [activeNav]);

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

  const prevHasLibraryRef = useRef(false);
  const prevHasContextMemoryRef = useRef(false);

  useEffect(() => {
    const hasLibrary = activeFeatures.has("library");
    const prevHasLibrary = prevHasLibraryRef.current;
    prevHasLibraryRef.current = hasLibrary;

    const hasContextMemory = activeFeatures.has("contextMemory");
    const prevHasContextMemory = prevHasContextMemoryRef.current;
    prevHasContextMemoryRef.current = hasContextMemory;

    if (hasLibrary && !prevHasLibrary) {
      canvasHandlersRef.current.open();
    } else if (!hasLibrary && prevHasLibrary && !hasContextMemory) {
      canvasHandlersRef.current.close();
    }

    if (hasContextMemory && !prevHasContextMemory) {
      canvasHandlersRef.current.open();
    } else if (!hasContextMemory && prevHasContextMemory && !hasLibrary) {
      canvasHandlersRef.current.close();
    }
  }, [activeFeatures]);

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

  const [sessionId, setSessionId] = useState(0);

  const newSession = useCallback(() => {
    setActiveNav("workspace");
    setCreationPhase("idle");
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

export { NavigationProvider };
