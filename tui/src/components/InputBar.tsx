/**
 * InputBar - text prompt for user input with /command detection.
 * Polished with elevated background, styled prompt, and themed input.
 */

import { useState, useCallback, useEffect } from "react";
import { useKeyboard } from "@opentui/react";
import { useAppContext } from "../context/AppContext";
import { bg, fg, accent, semantic } from "../theme";
import { loadHistory, addToHistory } from "../hooks/useCommandHistory";

interface InputBarProps {
  onSubmit: (text: string) => void;
  onSlashCommand: (command: string, args: string) => void;
}

export function InputBar({ onSubmit, onSlashCommand }: InputBarProps) {
  const { state } = useAppContext();
  const [value, setValue] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [isNavigatingHistory, setIsNavigatingHistory] = useState(false);
  const [showError, setShowError] = useState(false);

  useEffect(() => {
    loadHistory().then(setHistory).catch(() => setHistory([]));
  }, []);

  useEffect(() => {
    if (showError) {
      const timer = setTimeout(() => setShowError(false), 1500);
      return () => clearTimeout(timer);
    }
  }, [showError]);

  const handleChange = useCallback((newValue: string) => {
    setValue(newValue);
    setIsNavigatingHistory(false);
  }, []);

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
        onSubmit(trimmed);
      }

      const newHistory = await addToHistory(trimmed, history);
      setHistory(newHistory);
      setHistoryIndex(-1);
      setValue("");
      setIsNavigatingHistory(false);
    },
    [state.isProcessing, onSubmit, onSlashCommand, history]
  );

  useKeyboard((key) => {
    if (key.name === "arrowup" && !state.isProcessing) {
      if (history.length === 0) return;
      
      const newIndex = isNavigatingHistory 
        ? Math.min(historyIndex + 1, history.length - 1)
        : 0;
      
      if (newIndex !== historyIndex) {
        setHistoryIndex(newIndex);
        const histItem = history[history.length - 1 - newIndex] ?? "";
        setValue(histItem);
        setIsNavigatingHistory(true);
      }
    }

    if (key.name === "arrowdown" && !state.isProcessing) {
      if (!isNavigatingHistory || historyIndex <= 0) {
        setHistoryIndex(-1);
        setValue("");
        setIsNavigatingHistory(false);
        return;
      }

      const newIndex = historyIndex - 1;
      setHistoryIndex(newIndex);
      const histItem = history[history.length - 1 - newIndex] ?? "";
      setValue(histItem);
    }
  });

  const isDisabled = state.isProcessing;
  const promptColor = showError ? semantic.error : isDisabled ? fg.muted : accent.base;
  const promptText = showError ? "! " : "> ";

  const placeholder = isDisabled
    ? "Processing..."
    : state.connectionState !== "connected"
      ? "Connecting..."
      : "Type a message or /help for commands";

  return (
    <box
      width="100%"
      backgroundColor={bg.elevated}
      paddingTop={1}
      paddingBottom={1}
      paddingLeft={2}
      paddingRight={2}
      flexDirection="row"
    >
      <text fg={promptColor}>{promptText}</text>
      <box flexGrow={1}>
        <input
          value={value}
          onChange={handleChange}
          onSubmit={(v: unknown) => handleInputSubmit(v as string)}
          placeholder={placeholder}
          focused={!isDisabled}
          backgroundColor={bg.elevated}
          textColor={fg.primary}
          cursorColor={accent.base}
        />
      </box>
      {history.length > 0 && (
        <text fg={fg.muted} paddingLeft={1}>
          ⬆{history.length}
        </text>
      )}
    </box>
  );
}
