/**
 * InputBar - text prompt for user input with /command detection.
 * Polished with elevated background, styled prompt, and themed input.
 */

import { useState, useCallback } from "react";
import { useAppContext } from "../context/AppContext";
import { bg, fg, accent } from "../theme";

interface InputBarProps {
  onSubmit: (text: string) => void;
  onSlashCommand: (command: string, args: string) => void;
}

export function InputBar({ onSubmit, onSlashCommand }: InputBarProps) {
  const { state } = useAppContext();
  const [value, setValue] = useState("");

  const handleChange = useCallback((newValue: string) => {
    setValue(newValue);
  }, []);

  const handleInputSubmit = useCallback(
    (submittedValue: string) => {
      const trimmed = submittedValue.trim();
      if (!trimmed || state.isProcessing) return;

      if (trimmed.startsWith("/")) {
        const spaceIdx = trimmed.indexOf(" ");
        const command = spaceIdx === -1 ? trimmed : trimmed.slice(0, spaceIdx);
        const args = spaceIdx === -1 ? "" : trimmed.slice(spaceIdx + 1).trim();
        onSlashCommand(command, args);
      } else {
        onSubmit(trimmed);
      }

      setValue("");
    },
    [state.isProcessing, onSubmit, onSlashCommand],
  );

  const isDisabled = state.isProcessing;
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
      <text fg={isDisabled ? fg.muted : accent.base}>{"> "}</text>
      <box flexGrow={1}>
        <input
          value={value}
          onChange={handleChange}
          onSubmit={(v: any) => handleInputSubmit(v)}
          placeholder={placeholder}
          focused={!isDisabled}
          backgroundColor={bg.elevated}
          textColor={fg.primary}
          cursorColor={accent.base}
        />
      </box>
    </box>
  );
}
