/** Fleet's achromatic terminal palette and visual vocabulary. */

export const theme = {
  ink: "#f4f4f4",
  muted: "#8a8a8a",
  faint: "#5f5f5f",
  paper: "#ffffff",
  background: "#111111",
  rule: "#555555",
} as const;

export const ansi = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  boldOff: "\x1b[22m",
  dim: "\x1b[2m",
  dimOff: "\x1b[22m",
  italic: "\x1b[3m",
  italicOff: "\x1b[23m",
  white: "\x1b[97m",
  gray: "\x1b[90m",
  black: "\x1b[30m",
  bgWhite: "\x1b[47m",
} as const;

export const statusGlyph = {
  success: "✓",
  warning: "!",
  error: "×",
  running: "…",
  idle: "·",
} as const;
