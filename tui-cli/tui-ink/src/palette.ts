import type { OverlayView } from "./types.js";

export interface PaletteItem {
  id: string;
  label: string;
  description: string;
  group: "Navigation" | "Commands" | "Settings";
  nextView?: OverlayView;
  command?: string;
  action?: "show-status" | "list-commands" | "view-model-provider" | "edit-model" | "edit-api-base";
}

export interface ParsedCommandInput {
  command: string;
  args: Record<string, unknown>;
}

export function buildRootPalette(commands: string[]): PaletteItem[] {
  return [
    {
      id: "nav:settings",
      label: "Settings",
      description: "Open nested settings menu",
      group: "Navigation",
      nextView: "palette-settings",
    },
    {
      id: "nav:status",
      label: "Status",
      description: "Show runtime status panel",
      group: "Navigation",
      action: "show-status",
    },
    {
      id: "nav:commands",
      label: "Commands",
      description: "Print available commands to transcript",
      group: "Navigation",
      action: "list-commands",
    },
    ...commands.map((command) => ({
      id: `cmd:${command}`,
      label: `/${command}`,
      description: "Insert command into composer",
      group: "Commands" as const,
      command,
    })),
  ];
}

export function buildSettingsPalette(snapshot: {
  values?: Record<string, string>;
  masked_values?: Record<string, string>;
}): PaletteItem[] {
  const masked = snapshot.masked_values ?? {};
  return [
    {
      id: "settings:view-model-provider",
      label: "View model/provider",
      description: `Model: ${masked.DSPY_LM_MODEL || "<unset>"}`,
      group: "Settings",
      action: "view-model-provider",
    },
    {
      id: "settings:set-model",
      label: "Set DSPY_LM_MODEL",
      description: "Choose planner model identifier",
      group: "Settings",
      action: "edit-model",
    },
    {
      id: "settings:set-api-base",
      label: "Set DSPY_LM_API_BASE",
      description: "Set custom provider endpoint",
      group: "Settings",
      action: "edit-api-base",
    },
  ];
}

export function filterPalette(items: PaletteItem[], query: string): PaletteItem[] {
  const needle = query.trim().toLowerCase();
  if (!needle) {
    return items;
  }
  return items.filter((item) =>
    `${item.label} ${item.description} ${item.group}`.toLowerCase().includes(needle),
  );
}

export function clampIndex(index: number, itemCount: number): number {
  if (itemCount <= 0) {
    return 0;
  }
  return Math.max(0, Math.min(index, itemCount - 1));
}

export function moveIndex(index: number, delta: number, itemCount: number): number {
  return clampIndex(index + delta, itemCount);
}

export function detectMentionQuery(input: string): string | null {
  const mentionMatch = /(?:^|\s)@([^\s]*)$/.exec(input);
  if (!mentionMatch) {
    return null;
  }
  return mentionMatch[1] ?? "";
}

export function applyMentionSelection(input: string, mentionPath: string): string {
  const mentionMatch = /(?:^|\s)@([^\s]*)$/.exec(input);
  if (!mentionMatch || mentionMatch.index === undefined) {
    return input;
  }
  const fullMatch = mentionMatch[0];
  const prefix = input.slice(0, mentionMatch.index);
  const leadingSpace = fullMatch.startsWith(" ") ? " " : "";
  return `${prefix}${leadingSpace}@${mentionPath} `;
}

export function parseCommandInput(line: string): ParsedCommandInput {
  const trimmed = line.trim().replace(/^\/+/, "");
  const [command = "", ...rest] = trimmed.split(/\s+/);
  const rawArgs = rest.join(" ").trim();
  if (!rawArgs) {
    return { command, args: {} };
  }
  if (rawArgs.startsWith("{")) {
    try {
      return { command, args: JSON.parse(rawArgs) as Record<string, unknown> };
    } catch {
      return { command, args: { input: rawArgs } };
    }
  }
  const keyValuePayload = parseKeyValueArgs(rawArgs);
  if (Object.keys(keyValuePayload).length > 0) {
    return { command, args: keyValuePayload };
  }
  return { command, args: { input: rawArgs } };
}

function parseKeyValueArgs(rawArgs: string): Record<string, unknown> {
  const output: Record<string, unknown> = {};
  for (const token of rawArgs.split(/\s+/)) {
    if (!token.includes("=")) {
      return {};
    }
    const [rawKey, ...valueParts] = token.split("=");
    const key = rawKey.trim();
    if (!key) {
      return {};
    }
    output[key] = coerceToken(valueParts.join("="));
  }
  return output;
}

function coerceToken(value: string): unknown {
  const normalized = value.trim();
  if (normalized === "true") {
    return true;
  }
  if (normalized === "false") {
    return false;
  }
  if (normalized === "null") {
    return null;
  }
  const asNumber = Number(normalized);
  if (!Number.isNaN(asNumber) && normalized !== "") {
    return asNumber;
  }
  return normalized;
}
