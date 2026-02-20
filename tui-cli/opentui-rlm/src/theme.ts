/**
 * Theme constants for the fleet-rlm TUI.
 *
 * Monochrome palette with a single blue accent.
 * Grays create structure, blue creates focus.
 */

// ── Surfaces ──────────────────────────────────────────────
export const bg = {
  base: "#161616",
  surface: "#1c1c1c",
  elevated: "#232323",
  highlight: "#2a2a2a",
} as const;

// ── Borders ───────────────────────────────────────────────
export const border = {
  dim: "#333333",
  active: "#555555",
} as const;

// ── Text ──────────────────────────────────────────────────
export const fg = {
  primary: "#cccccc",
  secondary: "#888888",
  muted: "#555555",
} as const;

// ── Accent (the only color) ───────────────────────────────
export const accent = {
  base: "#7aa2f7",
  dim: "#4a6da7",
} as const;

// ── Semantic (used sparingly, only for states) ────────────
export const semantic = {
  success: "#73daca",
  warning: "#e0af68",
  error: "#f7768e",
} as const;
