/** Shared keybinding-hint formatting for the Fleet TUI. */

import { fleetKeybindings } from "./keybindings.js";
import { theme } from "./theme.js";

export type FleetBinding = "fleet.interrupt" | "fleet.toggleFold" | "fleet.exit";

function formatKeyPart(part: string): string {
  const normalized = part === "escape" ? "esc" : part;
  switch (normalized) {
    case "up":
      return "↑";
    case "down":
      return "↓";
    case "left":
      return "←";
    case "right":
      return "→";
    case "pageup":
      return "PgUp";
    case "pagedown":
      return "PgDn";
    case "home":
      return "Home";
    case "end":
      return "End";
    default:
      return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  }
}

function formatKeybinding(parts: readonly string[]): string {
  return parts.map(formatKeyPart).join("+");
}

/** Render a dim `key label` hint for one registered fleet keybinding. */
export function keyHint(binding: FleetBinding, label: string): string {
  return theme.fg("dim", `${keyText(binding)} ${label}`);
}

export function keyText(binding: FleetBinding): string {
  return formatKeybinding(fleetKeybindings.getKeys(binding));
}
