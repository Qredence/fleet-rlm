/**
 * Centralized keyboard handling context.
 * Provides priority-based keyboard event routing to prevent conflicts.
 */

import { createContext, useContext, useCallback, useRef, useEffect, type ReactNode } from "react";
import { useKeyboard } from "@opentui/react";

export interface KeyEvent {
  name: string;
  sequence: string;
  ctrl: boolean;
  shift: boolean;
  meta: boolean;
  option: boolean;
  eventType: "press" | "release" | "repeat";
  repeated: boolean;
}

export type KeyHandler = (key: KeyEvent) => boolean | void;

export type FocusPane = "input" | "chat" | "sidebar";

const FOCUS_ORDER: FocusPane[] = ["input", "chat", "sidebar"];

interface RegisteredHandler {
  id: string;
  handler: KeyHandler;
  priority: number;
}

interface KeyboardContextValue {
  registerHandler: (id: string, handler: KeyHandler, priority?: number) => void;
  unregisterHandler: (id: string) => void;
}

const KeyboardContext = createContext<KeyboardContextValue | null>(null);

export function useKeyboardContext(): KeyboardContextValue {
  const context = useContext(KeyboardContext);
  if (!context) {
    throw new Error("useKeyboardContext must be used within KeyboardProvider");
  }
  return context;
}

export function useRegisterKeyHandler(id: string, handler: KeyHandler, priority: number = 0): void {
  const { registerHandler, unregisterHandler } = useKeyboardContext();

  useEffect(() => {
    registerHandler(id, handler, priority);
    return () => unregisterHandler(id);
  }, [id, handler, priority, registerHandler, unregisterHandler]);
}

export function useFocusNavigation(
  focusedPane: FocusPane,
  setFocusedPane: (pane: FocusPane) => void,
  sidebarVisible: boolean,
  isProcessing: boolean
): void {
  const handleFocusNavigation = useCallback((key: KeyEvent) => {
    if (key.name !== "tab") return false;

    const visibleOrder = FOCUS_ORDER.filter(p => sidebarVisible || p !== "sidebar");
    const currentIdx = visibleOrder.indexOf(focusedPane);

    if (currentIdx === -1) return false;

    const nextIdx = key.shift
      ? (currentIdx - 1 + visibleOrder.length) % visibleOrder.length
      : (currentIdx + 1) % visibleOrder.length;

    const nextPane = visibleOrder[nextIdx];

    if (!nextPane) return false;

    // Lock focus to chat during processing
    if (isProcessing && nextPane !== "chat" && focusedPane !== "chat") {
      return true;
    }

    setFocusedPane(nextPane);
    return true;
  }, [focusedPane, setFocusedPane, sidebarVisible, isProcessing]);

  useRegisterKeyHandler("focusNavigation", handleFocusNavigation, PRIORITY.GLOBAL);
}

export function getNextFocusOnSidebarToggle(
  currentFocus: FocusPane,
  sidebarNowVisible: boolean
): FocusPane {
  if (sidebarNowVisible) return currentFocus;
  if (currentFocus === "sidebar") return "chat";
  return currentFocus;
}

interface KeyboardProviderProps {
  children: ReactNode;
}

export function KeyboardProvider({ children }: KeyboardProviderProps) {
  const handlersRef = useRef<Map<string, RegisteredHandler>>(new Map());

  const registerHandler = useCallback((id: string, handler: KeyHandler, priority: number = 0) => {
    handlersRef.current.set(id, { id, handler, priority });
  }, []);

  const unregisterHandler = useCallback((id: string) => {
    handlersRef.current.delete(id);
  }, []);

  useKeyboard((key) => {
    const handlers = Array.from(handlersRef.current.values());
    handlers.sort((a, b) => b.priority - a.priority);

    for (const registered of handlers) {
      const result = registered.handler(key as KeyEvent);
      if (result === true) {
        break;
      }
    }
  });

  return (
    <KeyboardContext.Provider value={{ registerHandler, unregisterHandler }}>
      {children}
    </KeyboardContext.Provider>
  );
}

export const PRIORITY = {
  PALETTE: 100,
  MODAL: 90,
  INPUT: 80,
  PANE: 50,
  GLOBAL: 10,
} as const;
