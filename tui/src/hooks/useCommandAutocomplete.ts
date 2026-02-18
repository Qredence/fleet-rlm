/**
 * Command autocomplete hook for Tab completion on slash commands.
 */

import { useState, useCallback, useMemo } from "react";
import { getAllCommands } from "../commands/registry";

export interface AutocompleteResult {
  completed: string;
  hasMatch: boolean;
  matches: string[];
}

const SLASH_COMMANDS = [
  "/help",
  "/exit",
  "/quit",
  "/clear",
  "/reset",
  "/trace",
  "/docs",
  "/load",
  "/active",
  "/list",
  "/chunk",
  "/analyze",
  "/summarize",
  "/extract",
  "/semantic",
  "/buffer",
  "/clear-buffer",
  "/save-buffer",
  "/load-volume",
];

export function useCommandAutocomplete() {
  const commands = useMemo(() => {
    const allCommands = getAllCommands();
    const commandIds = allCommands.map(c => `/${c.id}`);
    return [...new Set([...SLASH_COMMANDS, ...commandIds])];
  }, []);

  const [lastCompletion, setLastCompletion] = useState<string>("");

  const complete = useCallback((input: string, cursorPos: number): AutocompleteResult => {
    // Only autocomplete at the start of input
    if (!input.startsWith("/")) {
      return { completed: input, hasMatch: false, matches: [] };
    }

    // Find the command part (before space or end)
    const spaceIdx = input.indexOf(" ");
    const commandPart = spaceIdx === -1 ? input : input.slice(0, spaceIdx);
    const restOfInput = spaceIdx === -1 ? "" : input.slice(spaceIdx);

    // Find matching commands
    const matches = commands.filter(cmd => cmd.startsWith(commandPart));

    if (matches.length === 0) {
      return { completed: input, hasMatch: false, matches: [] };
    }

    // Cycle through matches
    const currentIdx = matches.indexOf(lastCompletion);
    const nextIdx = (currentIdx + 1) % matches.length;
    const nextMatch = matches[nextIdx] ?? matches[0];

    if (!nextMatch) {
      return { completed: input, hasMatch: false, matches: [] };
    }

    setLastCompletion(nextMatch);

    return {
      completed: nextMatch + restOfInput,
      hasMatch: true,
      matches,
    };
  }, [commands, lastCompletion]);

  const resetCompletion = useCallback(() => {
    setLastCompletion("");
  }, []);

  return {
    complete,
    resetCompletion,
    commands,
  };
}
