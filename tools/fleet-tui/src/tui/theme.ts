import { getCapabilities, type MarkdownTheme } from "@earendil-works/pi-tui";

import { highlightCode } from "./syntax-highlight.js";

export type TerminalColorScheme = "dark" | "light";
export type ColorMode = "truecolor" | "256color";

export type ThemeColor =
  | "accent"
  | "border"
  | "borderAccent"
  | "borderMuted"
  | "success"
  | "error"
  | "warning"
  | "muted"
  | "dim"
  | "text"
  | "thinkingText"
  | "userMessageText"
  | "toolTitle"
  | "toolOutput"
  | "mdHeading"
  | "mdLink"
  | "mdLinkUrl"
  | "mdCode"
  | "mdCodeBlock"
  | "mdCodeBlockBorder"
  | "mdQuote"
  | "mdQuoteBorder"
  | "mdHr"
  | "mdListBullet"
  | "syntaxComment"
  | "syntaxKeyword"
  | "syntaxFunction"
  | "syntaxVariable"
  | "syntaxString"
  | "syntaxNumber"
  | "syntaxType"
  | "syntaxOperator"
  | "syntaxPunctuation";

export type ThemeBackground =
  | "selectedBg"
  | "userMessageBg"
  | "toolPendingBg"
  | "toolSuccessBg"
  | "toolErrorBg";

type Palette = Record<ThemeColor | ThemeBackground, string>;

const palettes: Record<TerminalColorScheme, Palette> = {
  dark: {
    accent: "#8abeb7",
    border: "#5f87ff",
    borderAccent: "#00d7ff",
    borderMuted: "#505050",
    success: "#b5bd68",
    error: "#cc6666",
    warning: "#ffff00",
    muted: "#808080",
    dim: "#666666",
    text: "#d4d4d4",
    thinkingText: "#808080",
    selectedBg: "#3a3a4a",
    userMessageBg: "#343541",
    userMessageText: "#d4d4d4",
    toolPendingBg: "#282832",
    toolSuccessBg: "#283228",
    toolErrorBg: "#3c2828",
    toolTitle: "#d4d4d4",
    toolOutput: "#808080",
    mdHeading: "#f0c674",
    mdLink: "#81a2be",
    mdLinkUrl: "#666666",
    mdCode: "#8abeb7",
    mdCodeBlock: "#b5bd68",
    mdCodeBlockBorder: "#808080",
    mdQuote: "#808080",
    mdQuoteBorder: "#808080",
    mdHr: "#808080",
    mdListBullet: "#8abeb7",
    syntaxComment: "#6a9955",
    syntaxKeyword: "#569cd6",
    syntaxFunction: "#dcdcaa",
    syntaxVariable: "#9cdcfe",
    syntaxString: "#ce9178",
    syntaxNumber: "#b5cea8",
    syntaxType: "#4ec9b0",
    syntaxOperator: "#d4d4d4",
    syntaxPunctuation: "#d4d4d4",
  },
  light: {
    accent: "#5a8080",
    border: "#547da7",
    borderAccent: "#5a8080",
    borderMuted: "#b0b0b0",
    success: "#588458",
    error: "#aa5555",
    warning: "#9a7326",
    muted: "#6c6c6c",
    dim: "#767676",
    text: "#1f2328",
    thinkingText: "#6c6c6c",
    selectedBg: "#d0d0e0",
    userMessageBg: "#e8e8e8",
    userMessageText: "#1f2328",
    toolPendingBg: "#e8e8f0",
    toolSuccessBg: "#e8f0e8",
    toolErrorBg: "#f0e8e8",
    toolTitle: "#1f2328",
    toolOutput: "#6c6c6c",
    mdHeading: "#9a7326",
    mdLink: "#547da7",
    mdLinkUrl: "#767676",
    mdCode: "#5a8080",
    mdCodeBlock: "#588458",
    mdCodeBlockBorder: "#6c6c6c",
    mdQuote: "#6c6c6c",
    mdQuoteBorder: "#6c6c6c",
    mdHr: "#6c6c6c",
    mdListBullet: "#588458",
    syntaxComment: "#008000",
    syntaxKeyword: "#0000ff",
    syntaxFunction: "#795e26",
    syntaxVariable: "#001080",
    syntaxString: "#a31515",
    syntaxNumber: "#098658",
    syntaxType: "#267f99",
    syntaxOperator: "#000000",
    syntaxPunctuation: "#000000",
  },
};

export class FleetTheme {
  constructor(
    private readonly palette: Palette,
    private readonly mode: ColorMode,
  ) {}

  fg(color: ThemeColor, text: string): string {
    return `${foreground(this.palette[color], this.mode)}${text}\x1b[39m`;
  }

  bg(color: ThemeBackground, text: string): string {
    return `${background(this.palette[color], this.mode)}${text}\x1b[49m`;
  }

  bold(text: string): string {
    return `\x1b[1m${text}\x1b[22m`;
  }

  italic(text: string): string {
    return `\x1b[3m${text}\x1b[23m`;
  }

  underline(text: string): string {
    return `\x1b[4m${text}\x1b[24m`;
  }

  strikethrough(text: string): string {
    return `\x1b[9m${text}\x1b[29m`;
  }
}

let terminalColorScheme: TerminalColorScheme = "dark";
let activeTheme = createFleetTheme(terminalColorScheme, detectedColorMode());

export function createFleetTheme(scheme: TerminalColorScheme, mode: ColorMode): FleetTheme {
  return new FleetTheme(palettes[scheme], mode);
}

export function getBuiltinPalette(
  scheme: TerminalColorScheme,
): Readonly<Record<ThemeColor | ThemeBackground, string>> {
  return { ...palettes[scheme] };
}

export function getTerminalColorScheme(): TerminalColorScheme {
  return terminalColorScheme;
}

export function setTerminalColorScheme(scheme: TerminalColorScheme): boolean {
  if (terminalColorScheme === scheme) return false;
  terminalColorScheme = scheme;
  activeTheme = createFleetTheme(scheme, detectedColorMode());
  return true;
}

export const theme = {
  fg: (color: ThemeColor, text: string) => activeTheme.fg(color, text),
  bg: (color: ThemeBackground, text: string) => activeTheme.bg(color, text),
  bold: (text: string) => activeTheme.bold(text),
  italic: (text: string) => activeTheme.italic(text),
  underline: (text: string) => activeTheme.underline(text),
  strikethrough: (text: string) => activeTheme.strikethrough(text),
};

export const statusGlyph = {
  success: "✓",
  warning: "!",
  error: "×",
  running: "…",
  idle: "·",
} as const;

export const markdownTheme: MarkdownTheme = {
  heading: (text) => theme.fg("mdHeading", theme.bold(text)),
  link: (text) => theme.fg("mdLink", text),
  linkUrl: (text) => theme.fg("mdLinkUrl", text),
  code: (text) => theme.fg("mdCode", text),
  codeBlock: (text) => theme.fg("mdCodeBlock", text),
  codeBlockBorder: (text) => theme.fg("mdCodeBlockBorder", text),
  quote: (text) => theme.fg("mdQuote", text),
  quoteBorder: (text) => theme.fg("mdQuoteBorder", text),
  hr: (text) => theme.fg("mdHr", text),
  listBullet: (text) => theme.fg("mdListBullet", text),
  bold: (text) => theme.bold(text),
  italic: (text) => theme.italic(text),
  strikethrough: (text) => theme.strikethrough(text),
  underline: (text) => theme.underline(text),
  highlightCode: (code, language) => highlightCode(code, language, theme),
};

export const selectTheme = {
  selectedPrefix: (text: string) => theme.fg("accent", text),
  selectedText: (text: string) => theme.fg("accent", text),
  description: (text: string) => theme.fg("muted", text),
  scrollInfo: (text: string) => theme.fg("muted", text),
  noMatch: (text: string) => theme.fg("muted", text),
};

export const editorTheme = {
  borderColor: (text: string) => theme.fg("borderMuted", text),
  selectList: selectTheme,
};

function detectedColorMode(): ColorMode {
  return getCapabilities().trueColor ? "truecolor" : "256color";
}

function foreground(hex: string, mode: ColorMode): string {
  if (mode === "256color") return `\x1b[38;5;${rgbTo256(hex)}m`;
  const { r, g, b } = hexToRgb(hex);
  return `\x1b[38;2;${r};${g};${b}m`;
}

function background(hex: string, mode: ColorMode): string {
  if (mode === "256color") return `\x1b[48;5;${rgbTo256(hex)}m`;
  const { r, g, b } = hexToRgb(hex);
  return `\x1b[48;2;${r};${g};${b}m`;
}

function hexToRgb(hex: string): { r: number; g: number; b: number } {
  return {
    r: Number.parseInt(hex.slice(1, 3), 16),
    g: Number.parseInt(hex.slice(3, 5), 16),
    b: Number.parseInt(hex.slice(5, 7), 16),
  };
}

function rgbTo256(hex: string): number {
  const { r, g, b } = hexToRgb(hex);
  const cubeValues = [0, 95, 135, 175, 215, 255];
  const nearest = (value: number) =>
    cubeValues.reduce(
      (best, candidate, index) =>
        Math.abs(candidate - value) < Math.abs((cubeValues[best] ?? 0) - value) ? index : best,
      0,
    );
  const ri = nearest(r);
  const gi = nearest(g);
  const bi = nearest(b);
  const cube = 16 + 36 * ri + 6 * gi + bi;
  const gray = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
  const grayIndex = Math.max(0, Math.min(23, Math.round((gray - 8) / 10)));
  const grayValue = 8 + grayIndex * 10;
  const distance = (rr: number, gg: number, bb: number) =>
    (r - rr) ** 2 * 0.299 + (g - gg) ** 2 * 0.587 + (b - bb) ** 2 * 0.114;
  const cubeDistance = distance(cubeValues[ri] ?? 0, cubeValues[gi] ?? 0, cubeValues[bi] ?? 0);
  const grayDistance = distance(grayValue, grayValue, grayValue);
  return Math.max(r, g, b) - Math.min(r, g, b) < 10 && grayDistance < cubeDistance
    ? 232 + grayIndex
    : cube;
}
