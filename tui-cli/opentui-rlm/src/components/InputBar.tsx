/**
 * InputBar - text prompt for user input with /command detection.
 * Polished with elevated background, styled prompt, and themed input.
 * Supports multi-line input (Shift+Enter), undo/redo, and command autocomplete.
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { useRegisterKeyHandler, PRIORITY } from "../context/KeyboardContext";
import { useAppContext } from "../context/AppContext";
import { bg, fg, accent, semantic } from "../theme";
import { loadHistory, addToHistory } from "../hooks/useCommandHistory";
import { hasFileReferences, expandFileReferences } from "../utils/fileReference";
import { useCommandAutocomplete } from "../hooks/useCommandAutocomplete";

const MAX_INPUT_LENGTH = 50000;
const UNDO_HISTORY_SIZE = 50;

interface InputBarProps {
  onSubmit: (text: string) => void;
  onSlashCommand: (command: string, args: string) => void;
  focused?: boolean;
  onFocus?: () => void;
}

export function InputBar({ onSubmit, onSlashCommand, focused = true, onFocus }: InputBarProps) {
  const { state, dispatch } = useAppContext();
  const [value, setValue] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [isNavigatingHistory, setIsNavigatingHistory] = useState(false);
  const [showError, setShowError] = useState(false);
  const [isExpanding, setIsExpanding] = useState(false);

  // Undo/redo state
  const [undoHistory, setUndoHistory] = useState<string[]>([]);
  const [undoIndex, setUndoIndex] = useState(-1);
  const isUndoRedo = useRef(false);

  // Draft persistence
  const draftRef = useRef<string | null>(null);

  // Command autocomplete
  const { complete, resetCompletion } = useCommandAutocomplete();

  useEffect(() => {
    loadHistory().then(setHistory).catch(() => setHistory([]));
  }, []);

  useEffect(() => {
    if (showError) {
      const timer = setTimeout(() => setShowError(false), 1500);
      return () => clearTimeout(timer);
    }
  }, [showError]);

  // Track undo history
  const handleChange = useCallback((newValue: string) => {
    // Enforce max length
    const truncated = newValue.length > MAX_INPUT_LENGTH
      ? newValue.slice(0, MAX_INPUT_LENGTH)
      : newValue;

    setValue(truncated);
    setIsNavigatingHistory(false);

    // Add to undo history (skip if from undo/redo)
    if (!isUndoRedo.current) {
      setUndoHistory(prev => {
        const newHistory = [...prev.slice(undoIndex + 1), truncated];
        return newHistory.slice(-UNDO_HISTORY_SIZE);
      });
      setUndoIndex(prev => Math.min(prev + 1, UNDO_HISTORY_SIZE - 1));
    }
    isUndoRedo.current = false;
  }, [undoIndex]);

  // Draft persistence - save when losing focus, restore when gaining
  useEffect(() => {
    if (!focused && value.trim()) {
      draftRef.current = value;
      dispatch({ type: "SET_INPUT_DRAFT", payload: value });
    }
  }, [focused, value, dispatch]);

  useEffect(() => {
    if (focused && state.inputDraft && !value) {
      setValue(state.inputDraft);
      dispatch({ type: "CLEAR_INPUT_DRAFT" });
    }
  }, [focused, state.inputDraft, value, dispatch]);

  const handleInputSubmit = useCallback(
    async (submittedValue: string) => {
      const trimmed = submittedValue.trim();
      if (!trimmed) {
        setShowError(true);
        return;
      }

      if (state.isProcessing) return;

      if (trimmed.startsWith("/")) {
        const spaceIdx = trimmed.indexOf(" ");
        const command = spaceIdx === -1 ? trimmed : trimmed.slice(0, spaceIdx);
        const args = spaceIdx === -1 ? "" : trimmed.slice(spaceIdx + 1).trim();
        onSlashCommand(command, args);
      } else {
        // Expand @-mentions if present
        let finalContent = trimmed;
        if (hasFileReferences(trimmed)) {
          setIsExpanding(true);
          finalContent = await expandFileReferences(trimmed);
          setIsExpanding(false);
        }
        onSubmit(finalContent);
      }

      const newHistory = await addToHistory(trimmed, history);
      setHistory(newHistory);
      setHistoryIndex(-1);
      setValue("");
      setIsNavigatingHistory(false);

      // Clear undo history on submit
      setUndoHistory([]);
      setUndoIndex(-1);

      // Clear draft
      draftRef.current = null;
      dispatch({ type: "CLEAR_INPUT_DRAFT" });
    },
    [state.isProcessing, onSubmit, onSlashCommand, history, dispatch]
  );

  const handleHistoryKeys = useCallback((key: { name: string }) => {
    if (state.isProcessing) return false;

    if (key.name === "arrowup") {
      if (history.length === 0) return false;

      const newIndex = isNavigatingHistory
        ? Math.min(historyIndex + 1, history.length - 1)
        : 0;

      if (newIndex !== historyIndex) {
        setHistoryIndex(newIndex);
        const histItem = history[history.length - 1 - newIndex] ?? "";
        setValue(histItem);
        setIsNavigatingHistory(true);
      }
      return true;
    }

    if (key.name === "arrowdown") {
      if (!isNavigatingHistory || historyIndex <= 0) {
        setHistoryIndex(-1);
        setValue("");
        setIsNavigatingHistory(false);
        return true;
      }

      const newIndex = historyIndex - 1;
      setHistoryIndex(newIndex);
      const histItem = history[history.length - 1 - newIndex] ?? "";
      setValue(histItem);
      return true;
    }

    return false;
  }, [state.isProcessing, history, historyIndex, isNavigatingHistory]);

  useRegisterKeyHandler("inputHistory", handleHistoryKeys, PRIORITY.INPUT);

  // Undo/Redo handler
  const handleUndoRedo = useCallback((key: { ctrl: boolean; shift: boolean; name: string }) => {
    if (!focused) return false;

    if (key.ctrl && key.name === "z") {
      if (key.shift && undoIndex < undoHistory.length - 1) {
        // Redo
        isUndoRedo.current = true;
        const newIndex = undoIndex + 1;
        setUndoIndex(newIndex);
        setValue(undoHistory[newIndex] ?? "");
        return true;
      } else if (!key.shift && undoIndex > 0) {
        // Undo
        isUndoRedo.current = true;
        const newIndex = undoIndex - 1;
        setUndoIndex(newIndex);
        setValue(undoHistory[newIndex] ?? "");
        return true;
      }
    }

    return false;
  }, [focused, undoHistory, undoIndex]);

  useRegisterKeyHandler("inputUndoRedo", handleUndoRedo, PRIORITY.INPUT + 1);

  // Escape handler - clear selection first, then clear input
  const handleEscape = useCallback((key: { name: string }) => {
    if (!focused) return false;

    if (key.name === "escape") {
      if (value) {
        setValue("");
        setUndoHistory([]);
        setUndoIndex(-1);
        resetCompletion();
        return true;
      }
    }

    return false;
  }, [focused, value, resetCompletion]);

  useRegisterKeyHandler("inputEscape", handleEscape, PRIORITY.INPUT + 2);

  // Tab autocomplete for /commands
  const handleAutocomplete = useCallback((key: { name: string; shift: boolean }) => {
    if (!focused) return false;

    // Tab without shift for autocomplete
    if (key.name === "tab" && !key.shift && value.startsWith("/")) {
      const result = complete(value, value.length);
      if (result.hasMatch && result.completed !== value) {
        setValue(result.completed);
        return true;
      }
    }

    return false;
  }, [focused, value, complete]);

  useRegisterKeyHandler("inputAutocomplete", handleAutocomplete, PRIORITY.INPUT + 3);

  const isDisabled = state.isProcessing;
  const promptColor = showError ? semantic.error : isDisabled ? fg.muted : accent.base;
  const promptText = showError ? "! " : "> ";

  const placeholder = isDisabled
    ? "Processing..."
    : state.connectionState !== "connected"
      ? "Connecting..."
      : "Type a message, @file to reference files, or /help for commands";

  const inputBg = focused ? bg.highlight : bg.elevated;

  return (
    <box
      width="100%"
      backgroundColor={inputBg}
      paddingTop={1}
      paddingBottom={1}
      paddingLeft={2}
      paddingRight={2}
      flexDirection="row"
      border={focused}
      borderColor={focused ? accent.base : undefined}
      onMouseDown={onFocus}
    >
      <text fg={promptColor}>{promptText}</text>
      <box flexGrow={1}>
        <input
          value={value}
          onChange={handleChange}
          onSubmit={(v: unknown) => handleInputSubmit(v as string)}
          placeholder={placeholder}
          focused={focused && !isDisabled}
          backgroundColor={inputBg}
          textColor={fg.primary}
          cursorColor={accent.base}
          focusedBackgroundColor={inputBg}
        />
      </box>
      {history.length > 0 && (
        <text fg={fg.muted} paddingLeft={1}>
          ⬆{history.length}
        </text>
      )}
      {isExpanding && (
        <text fg={accent.base} paddingLeft={1}>
          Expanding...
        </text>
      )}
    </box>
  );
}
