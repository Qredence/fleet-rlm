import { useState, useMemo, useEffect } from "react";
import { useKeyboard } from "@opentui/react";
import { getAllCommands, getCommandAction } from "../commands/registry";
import { fuzzySearch } from "../utils/fuzzy";
import { bg, border, fg, accent } from "../theme";

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
  onSlashCommand: (command: string, args: string) => void;
  onClearChat: () => void;
  onToggleSidebar: () => void;
  onCopyLast: () => void;
  onCancel: () => void;
}

export function CommandPalette({
  isOpen,
  onClose,
  onSlashCommand,
  onClearChat,
  onToggleSidebar,
  onCopyLast,
  onCancel,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const allCommands = useMemo(() => getAllCommands(), []);

  const filteredCommands = useMemo(() => {
    if (!query.trim()) return allCommands.slice(0, 10);
    const results = fuzzySearch(
      query,
      allCommands,
      (cmd) => `${cmd.label} ${cmd.description || ""} ${cmd.keywords?.join(" ") || ""}`
    );
    return results.slice(0, 10).map((r) => r.item);
  }, [query, allCommands]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  useKeyboard((key) => {
    if (!isOpen) return;
    if (key.name === "escape") {
      onClose();
      setQuery("");
    }
    if (key.name === "arrowup") {
      setSelectedIndex((i) => Math.max(0, i - 1));
    }
    if (key.name === "arrowdown") {
      setSelectedIndex((i) => Math.min(filteredCommands.length - 1, i + 1));
    }
    if (key.name === "return") {
      const cmd = filteredCommands[selectedIndex];
      if (cmd) {
        const action = getCommandAction(cmd);
        if (action.type === "slash") {
          onSlashCommand(action.command || "", action.args || "");
        } else {
          switch (action.command) {
            case "clear":
              onClearChat();
              break;
            case "sidebar":
              onToggleSidebar();
              break;
            case "copy":
              onCopyLast();
              break;
            case "cancel":
              onCancel();
              break;
          }
        }
        onClose();
        setQuery("");
      }
    }
  });

  if (!isOpen) return null;

  return (
    <box
      position="absolute"
      bottom={4}
      left="50%"
      width={60}
      backgroundColor={bg.surface}
      border
      borderStyle="rounded"
      borderColor={border.active}
      flexDirection="column"
    >
      <box padding={1} backgroundColor={bg.highlight}>
        <input
          value={query}
          onChange={setQuery}
          placeholder="Search commands..."
          focused
          backgroundColor={bg.elevated}
          textColor={fg.primary}
        />
      </box>
      <box flexDirection="column" paddingLeft={1} paddingRight={1}>
        {filteredCommands.map((cmd, idx) => (
          <box
            key={cmd.id}
            paddingTop={0}
            paddingBottom={0}
            backgroundColor={idx === selectedIndex ? bg.highlight : undefined}
          >
            <text>
              <span fg={idx === selectedIndex ? accent.base : fg.primary}>
                {idx === selectedIndex ? "▸ " : "  "}
              </span>
              <span fg={fg.primary}>{cmd.label}</span>
              <span fg={fg.muted}> - {cmd.description}</span>
              {cmd.shortcut && <span fg={accent.dim}> ({cmd.shortcut})</span>}
            </text>
          </box>
        ))}
      </box>
    </box>
  );
}
