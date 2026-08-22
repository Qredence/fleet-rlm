import {
  getCapabilities,
  type MarkdownTheme,
  type SelectListTheme,
  type SettingsListTheme,
} from "@earendil-works/pi-tui";

import { highlightCode } from "./syntax-highlight.js";
import {
  builtinPalettes,
  darkPalette,
  loadCustomTheme,
  loadCustomThemeNames,
  readThemeSelection,
  type Palette,
  type ThemeBackground,
  type ThemeColor,
  watchCustomThemes,
  writeThemeSelection,
} from "./themes/palette.js";

export type TerminalColorScheme = "dark" | "light";
export type ColorMode = "truecolor" | "256color";

export type { ThemeColor, ThemeBackground, Palette };

type Rgb = { r: number; g: number; b: number };

const SURFACE_MIN_LUMINANCE_DELTA = 12;
const SURFACE_MAX_BLEND_ALPHA = 1;
const SURFACE_BLEND_STEP = 0.01;
const SELECTION_MIN_LUMINANCE_DELTA = 28;
const SELECTION_MAX_BLEND_ALPHA = 0.5;
const SELECTION_BLEND_STEP = 0.05;
const BLACK: Rgb = { r: 0, g: 0, b: 0 };
const WHITE: Rgb = { r: 255, g: 255, b: 255 };

function hexToRgb(hex: string): Rgb {
  return {
    r: Number.parseInt(hex.slice(1, 3), 16),
    g: Number.parseInt(hex.slice(3, 5), 16),
    b: Number.parseInt(hex.slice(5, 7), 16),
  };
}

function rgbToHex(rgb: Rgb): string {
  const channel = (value: number) =>
    Math.max(0, Math.min(255, Math.round(value)))
      .toString(16)
      .padStart(2, "0");
  return `#${channel(rgb.r)}${channel(rgb.g)}${channel(rgb.b)}`;
}

function luminance(rgb: Rgb): number {
  return 0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b;
}

function blendColor(base: Rgb, top: Rgb, alpha: number): Rgb {
  return {
    r: top.r * alpha + base.r * (1 - alpha),
    g: top.g * alpha + base.g * (1 - alpha),
    b: top.b * alpha + base.b * (1 - alpha),
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

function fgAnsi(hex: string, mode: ColorMode): string {
  if (mode === "256color") return `\x1b[38;5;${rgbTo256(hex)}m`;
  const { r, g, b } = hexToRgb(hex);
  return `\x1b[38;2;${r};${g};${b}m`;
}

function bgAnsi(hex: string, mode: ColorMode): string {
  if (mode === "256color") return `\x1b[48;5;${rgbTo256(hex)}m`;
  const { r, g, b } = hexToRgb(hex);
  return `\x1b[48;2;${r};${g};${b}m`;
}

export class FleetTheme {
  constructor(
    private readonly palette: Palette,
    private readonly mode: ColorMode,
  ) {}

  fg(color: ThemeColor, text: string): string {
    return `${fgAnsi(this.palette[color], this.mode)}${text}\x1b[39m`;
  }

  bg(color: ThemeBackground, text: string): string {
    return `${bgAnsi(this.palette[color], this.mode)}${text}\x1b[49m`;
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

  /**
   * Background style for a surface token, blended away from the terminal's
   * actual background when the configured color would be invisible on it.
   */
  surfaceBackgroundColor(color: ThemeBackground): (str: string) => string {
    const surfaceRgb = hexToRgb(this.palette[color]);
    const terminalBg = getTerminalBackground();
    if (!terminalBg) return (str: string) => this.bg(color, str);

    const delta = Math.abs(luminance(surfaceRgb) - luminance(terminalBg));
    if (delta >= SURFACE_MIN_LUMINANCE_DELTA) return (str: string) => this.bg(color, str);

    const top = luminance(terminalBg) >= 128 ? BLACK : WHITE;
    let blended = rgbToHex(surfaceRgb);
    for (
      let alpha = SURFACE_BLEND_STEP;
      alpha <= SURFACE_MAX_BLEND_ALPHA;
      alpha += SURFACE_BLEND_STEP
    ) {
      const candidate = rgbToHex(blendColor(surfaceRgb, top, alpha));
      blended = candidate;
      if (
        Math.abs(luminance(hexToRgb(candidate)) - luminance(terminalBg)) >=
        SURFACE_MIN_LUMINANCE_DELTA
      ) {
        break;
      }
    }
    return (str: string) => `${bgAnsi(blended, this.mode)}${str}\x1b[49m`;
  }

  /**
   * Selection background that guarantees a minimum contrast delta against the
   * terminal background, stepping the blend toward the opposite endpoint until
   * the (quantized) result passes the threshold.
   */
  selectionBackgroundColor(): (str: string) => string {
    const selectedBg = hexToRgb(this.palette.selectedBg);
    const terminalBg = getTerminalBackground();
    if (!terminalBg) return (str: string) => this.bg("selectedBg", str);

    const selectionLuminance = luminance(selectedBg);
    const terminalLuminance = luminance(terminalBg);
    const delta = Math.abs(selectionLuminance - terminalLuminance);
    if (delta >= SELECTION_MIN_LUMINANCE_DELTA) return (str: string) => this.bg("selectedBg", str);

    const endpoints = selectionLuminance >= terminalLuminance ? [WHITE, BLACK] : [BLACK, WHITE];
    let bestColor: string | undefined;
    let bestDelta = delta;
    for (const top of endpoints) {
      const spread = luminance(top) - selectionLuminance;
      if (spread === 0) continue;
      const targetLuminance = terminalLuminance + Math.sign(spread) * SELECTION_MIN_LUMINANCE_DELTA;
      let baseAlpha = Math.min(
        SELECTION_MAX_BLEND_ALPHA,
        (targetLuminance - selectionLuminance) / spread,
      );
      const alphas: number[] = [];
      for (; baseAlpha < SELECTION_MAX_BLEND_ALPHA; baseAlpha += SELECTION_BLEND_STEP) {
        alphas.push(baseAlpha);
      }
      alphas.push(SELECTION_MAX_BLEND_ALPHA);
      for (const alpha of alphas) {
        const adjusted = rgbToHex(blendColor(top, selectedBg, alpha));
        const adjustedRgb = hexToRgb(adjusted);
        const resultDelta = Math.abs(luminance(adjustedRgb) - terminalLuminance);
        if (resultDelta >= SELECTION_MIN_LUMINANCE_DELTA - 1) {
          bestColor = adjusted;
          bestDelta = resultDelta;
          break;
        }
        if (resultDelta > bestDelta) {
          bestColor = adjusted;
          bestDelta = resultDelta;
        }
      }
      if (bestColor !== undefined && bestDelta >= SELECTION_MIN_LUMINANCE_DELTA - 1) break;
    }
    if (bestColor === undefined) return (str: string) => this.bg("selectedBg", str);
    const ansi = bgAnsi(bestColor, this.mode);
    return (str: string) => `${ansi}${str}\x1b[49m`;
  }

  getUserMessageBackgroundColor(): (str: string) => string {
    return this.surfaceBackgroundColor("userMessageBg");
  }

  /** Transcript search (Ctrl+Shift+F) non-current match: a plain theme underline. */
  getSearchMatchStyle(): (str: string) => string {
    return (str: string) => this.underline(str);
  }

  /** Transcript search current match: the Fleet adaptive selection background. */
  getSearchCurrentMatchStyle(): (str: string) => string {
    return this.selectionBackgroundColor();
  }
}

let terminalColorScheme: TerminalColorScheme = "dark";
let themeName = "dark";
let activeTheme = createFleetTheme(terminalColorScheme, detectedColorMode());
let terminalBackground: Rgb | null = null;
let onThemeChangeCallback: (() => void) | undefined;
let customThemeWatcherStop: (() => void) | undefined;
let explicitThemeOverride = false;

/** Actual terminal background RGB from the OSC 11 query, when known. */
export function getTerminalBackground(): Rgb | null {
  return terminalBackground;
}

export function setTerminalBackground(rgb: Rgb | null): void {
  terminalBackground = rgb;
}

export function createFleetTheme(scheme: TerminalColorScheme, mode: ColorMode): FleetTheme {
  return new FleetTheme(builtinPalettes[scheme] ?? darkPalette, mode);
}

export function getBuiltinPalette(
  scheme: TerminalColorScheme,
): Readonly<Record<ThemeColor | ThemeBackground, string>> {
  return { ...(builtinPalettes[scheme] ?? darkPalette) };
}

export function getTerminalColorScheme(): TerminalColorScheme {
  return terminalColorScheme;
}

export function setTerminalColorScheme(scheme: TerminalColorScheme): boolean {
  if (terminalColorScheme === scheme) return false;
  terminalColorScheme = scheme;
  // Custom themes win over the auto scheme; builtins follow the terminal.
  if (themeName === "dark" || themeName === "light") {
    themeName = scheme;
    activeTheme = createFleetTheme(scheme, detectedColorMode());
  }
  return true;
}

export function getThemeName(): string {
  return themeName;
}

/** True when startup selected a valid theme through FLEET_TUI_THEME. */
export function hasExplicitThemeOverride(): boolean {
  return explicitThemeOverride;
}

/** Names of all selectable themes: builtins first, then custom JSON themes. */
export async function getAvailableThemes(): Promise<string[]> {
  const custom = await loadCustomThemeNames();
  return [...Object.keys(builtinPalettes), ...custom.filter((name) => !builtinPalettes[name])];
}

/**
 * Apply a theme by name (builtin or custom). Persists the selection and
 * notifies listeners. A custom theme that fails to load is ignored and the
 * previous theme stays active.
 */
export async function setTheme(name: string): Promise<{ success: boolean; error?: string }> {
  const palette = builtinPalettes[name] ?? (await loadCustomTheme(name));
  if (!palette) {
    return { success: false, error: `Theme not found: ${name}` };
  }
  explicitThemeOverride = false;
  themeName = name;
  activeTheme = new FleetTheme(palette, detectedColorMode());
  if (name === "dark" || name === "light") {
    terminalColorScheme = name;
  }
  void writeThemeSelection(name);
  watchActiveCustomTheme();
  onThemeChangeCallback?.();
  return { success: true };
}

/**
 * Initialize the theme from an explicit env override, a persisted selection,
 * or the terminal scheme (dark by default).
 */
export async function initTheme(override?: string): Promise<void> {
  const persisted = await readThemeSelection();
  const name = override ?? persisted;
  explicitThemeOverride = false;
  let changed = false;
  if (name) {
    const palette = builtinPalettes[name] ?? (await loadCustomTheme(name));
    if (palette) {
      changed = name !== themeName;
      if (changed) {
        themeName = name;
        activeTheme = new FleetTheme(palette, detectedColorMode());
      }
      if (name === "dark" || name === "light") terminalColorScheme = name;
      explicitThemeOverride = override !== undefined;
    }
  }
  watchActiveCustomTheme();
  if (changed) onThemeChangeCallback?.();
}

export function onThemeChange(callback: () => void): void {
  onThemeChangeCallback = callback;
}

/** Watch the active custom theme file so edits hot-reload. */
function watchActiveCustomTheme(): void {
  stopThemeMonitoring();
  if (builtinPalettes[themeName]) return;
  customThemeWatcherStop = watchCustomThemes((names) => {
    if (!names.includes(themeName)) return;
    void setTheme(themeName).catch(() => undefined);
  });
}

/** Stop custom theme filesystem monitoring when the TUI lifecycle ends. */
export function stopThemeMonitoring(): void {
  customThemeWatcherStop?.();
  customThemeWatcherStop = undefined;
}

export const theme = {
  fg: (color: ThemeColor, text: string) => activeTheme.fg(color, text),
  bg: (color: ThemeBackground, text: string) => activeTheme.bg(color, text),
  bold: (text: string) => activeTheme.bold(text),
  italic: (text: string) => activeTheme.italic(text),
  underline: (text: string) => activeTheme.underline(text),
  strikethrough: (text: string) => activeTheme.strikethrough(text),
  surface: (color: ThemeBackground) => activeTheme.surfaceBackgroundColor(color),
  userMessageBackground: () => activeTheme.getUserMessageBackgroundColor(),
  /** Transcript search match styles: resolve against the active theme at call time. */
  searchMatch: () => activeTheme.getSearchMatchStyle(),
  currentSearchMatch: () => activeTheme.getSearchCurrentMatchStyle(),
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

export const selectTheme: SelectListTheme = {
  selectedPrefix: (text) => theme.fg("accent", text),
  selectedText: (text) => theme.fg("accent", text),
  description: (text) => theme.fg("muted", text),
  scrollInfo: (text) => theme.fg("muted", text),
  noMatch: (text) => theme.fg("muted", text),
};

export const editorTheme = {
  borderColor: (text: string) => theme.fg("borderMuted", text),
  selectList: selectTheme,
};

export const settingsListTheme: SettingsListTheme = {
  label: (text, selected) => (selected ? theme.fg("accent", text) : text),
  value: (text, selected) => (selected ? theme.fg("accent", text) : theme.fg("muted", text)),
  description: (text) => theme.fg("dim", text),
  cursor: theme.fg("accent", "› "),
  hint: (text) => theme.fg("dim", text),
};

function detectedColorMode(): ColorMode {
  return getCapabilities().trueColor ? "truecolor" : "256color";
}
