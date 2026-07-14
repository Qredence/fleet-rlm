/** Multi-line prompt with history (↑/↓) and Ctrl+C handling. */

import { Box, Text, useInput } from "ink";
import { useState, type FC } from "react";
import { theme } from "./theme.js";

export const Prompt: FC<{
  busy: boolean;
  active: boolean;
  onSubmit: (text: string) => void;
  onCancel: () => void;
}> = ({ busy, active, onSubmit, onCancel }) => {
  const [value, setValue] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);

  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      onCancel();
      return;
    }
    if (key.return) {
      const text = value.trim();
      if (!text) return;
      if (text !== history[0]) setHistory([text, ...history].slice(0, 100));
      setHistoryIndex(null);
      setValue("");
      onSubmit(text);
      return;
    }
    if (key.upArrow) {
      if (history.length === 0) return;
      const nextIndex = historyIndex === null ? 0 : Math.min(historyIndex + 1, history.length - 1);
      const item = history[nextIndex];
      if (item === undefined) return;
      setHistoryIndex(nextIndex);
      setValue(item);
      return;
    }
    if (key.downArrow) {
      if (historyIndex === null) return;
      const nextIndex = historyIndex - 1;
      if (nextIndex < 0) {
        setHistoryIndex(null);
        setValue("");
      } else {
        const item = history[nextIndex];
        if (item === undefined) return;
        setHistoryIndex(nextIndex);
        setValue(item);
      }
      return;
    }
    if (key.backspace || key.delete) {
      setValue(value.slice(0, -1));
      return;
    }
    if (input && !key.ctrl && !key.meta) {
      setValue(value + input);
    }
  }, { isActive: active });

  return (
    <Box>
      <Text color={theme.paper} bold>
        {busy ? "…" : "›"}{" "}
      </Text>
      <Text>{value || "(type a prompt, / for commands)"}</Text>
    </Box>
  );
};

export const PromptHelp: FC<{ busy: boolean; active: boolean }> = ({ busy, active }) => (
  <Text color={theme.muted}>
    {busy
      ? "running · Ctrl+C to cancel · PageUp/PageDown: scroll · End: bottom"
      : active
        ? "↑/↓ history · Enter to send · Tab: thread nav · PageUp/PageDown: scroll · End: bottom"
        : "thread nav · ↑/↓/jk move · Enter/Space: toggle · Tab: prompt · End: bottom"}
  </Text>
);
