import type { PaletteCommand } from "../types/protocol";

export interface CommandAction {
  type: "slash" | "action";
  command?: string;
  args?: string;
  shortcut?: string;
}

export const SLASH_COMMANDS: PaletteCommand[] = [
  {
    id: "/help",
    label: "/help",
    description: "Show available commands",
    category: "commands",
    keywords: ["help", "commands"],
  },
  {
    id: "/exit",
    label: "/exit",
    description: "Exit the application",
    category: "commands",
    keywords: ["exit", "quit"],
    shortcut: "Ctrl+Q",
  },
  {
    id: "/quit",
    label: "/quit",
    description: "Exit the application",
    category: "commands",
    keywords: ["exit", "quit"],
    shortcut: "Ctrl+Q",
  },
  {
    id: "/clear",
    label: "/clear",
    description: "Clear chat history",
    category: "commands",
    keywords: ["clear", "history"],
    shortcut: "Ctrl+L",
  },
  {
    id: "/reset",
    label: "/reset",
    description: "Reset session (history + buffers)",
    category: "commands",
    keywords: ["reset", "session"],
  },
  {
    id: "/trace",
    label: "/trace [mode]",
    description: "Set trace mode (compact/verbose/off)",
    category: "commands",
    keywords: ["trace", "debug"],
  },
  {
    id: "/docs",
    label: "/docs <path>",
    description: "Load a document",
    category: "commands",
    keywords: ["document", "load", "file"],
  },
  {
    id: "/load",
    label: "/load <path> [alias]",
    description: "Load document with optional alias",
    category: "commands",
    keywords: ["load", "document"],
  },
  {
    id: "/list",
    label: "/list",
    description: "List loaded documents",
    category: "commands",
    keywords: ["list", "documents"],
  },
  {
    id: "/buffer",
    label: "/buffer <name>",
    description: "Read sandbox buffer contents",
    category: "commands",
    keywords: ["buffer", "read"],
  },
  {
    id: "/analyze",
    label: "/analyze <query>",
    description: "Analyze active document",
    category: "commands",
    keywords: ["analyze", "query"],
  },
  {
    id: "/summarize",
    label: "/summarize <focus>",
    description: "Summarize active document",
    category: "commands",
    keywords: ["summarize", "summary"],
  },
];

export const QUICK_ACTIONS: PaletteCommand[] = [
  {
    id: "action:clear",
    label: "Clear Chat",
    description: "Clear transcript history",
    category: "actions",
    shortcut: "Ctrl+L",
    keywords: ["clear", "history"],
  },
  {
    id: "action:sidebar",
    label: "Toggle Sidebar",
    description: "Show/hide inspector panel",
    category: "actions",
    shortcut: "Ctrl+B",
    keywords: ["sidebar", "panel"],
  },
  {
    id: "action:copy",
    label: "Copy Last Response",
    description: "Copy last assistant message",
    category: "actions",
    shortcut: "Ctrl+Y",
    keywords: ["copy", "clipboard"],
  },
  {
    id: "action:cancel",
    label: "Cancel Turn",
    description: "Cancel current processing",
    category: "actions",
    shortcut: "Ctrl+C",
    keywords: ["cancel", "stop"],
  },
];

export function getAllCommands(): PaletteCommand[] {
  return [...SLASH_COMMANDS, ...QUICK_ACTIONS];
}

export function getCommandAction(command: PaletteCommand): CommandAction {
  if (command.id.startsWith("/")) {
    return { type: "slash", command: command.id };
  }
  if (command.id.startsWith("action:")) {
    return { type: "action", command: command.id.replace("action:", "") };
  }
  return { type: "action", command: command.id };
}
