/**
 * Theme palette sources for the Fleet TUI.
 *
 * Builtin palettes are type-checked TypeScript (dark/light). Custom themes are
 * JSON files under the Fleet state directory (`FLEET_TUI_STATE_DIR` or
 * `~/.local/share/fleet/tui/themes/*.json`) that may reference `vars` and may
 * override only the tokens they name; anything missing falls back to the dark
 * builtin. A malformed custom theme is ignored, never fatal.
 */

import { mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { watch, type FSWatcher } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

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
  | "toolDiffAdded"
  | "toolDiffRemoved"
  | "toolDiffText"
  | "toolDiffContext"
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
  | "customMessageBg"
  | "toolPendingBg"
  | "toolSuccessBg"
  | "toolErrorBg"
  | "toolPanelBg"
  | "toolDiffAddedBg"
  | "toolDiffRemovedBg";

export type Palette = Record<ThemeColor | ThemeBackground, string>;

/** All tokens every palette must define, in display order. */
export const THEME_TOKENS: readonly (ThemeColor | ThemeBackground)[] = [
  "accent",
  "border",
  "borderAccent",
  "borderMuted",
  "success",
  "error",
  "warning",
  "muted",
  "dim",
  "text",
  "thinkingText",
  "selectedBg",
  "userMessageBg",
  "userMessageText",
  "customMessageBg",
  "toolPendingBg",
  "toolSuccessBg",
  "toolErrorBg",
  "toolPanelBg",
  "toolDiffAddedBg",
  "toolDiffRemovedBg",
  "toolTitle",
  "toolOutput",
  "mdHeading",
  "mdLink",
  "mdLinkUrl",
  "mdCode",
  "mdCodeBlock",
  "mdCodeBlockBorder",
  "mdQuote",
  "mdQuoteBorder",
  "mdHr",
  "mdListBullet",
  "toolDiffAdded",
  "toolDiffRemoved",
  "toolDiffText",
  "toolDiffContext",
  "syntaxComment",
  "syntaxKeyword",
  "syntaxFunction",
  "syntaxVariable",
  "syntaxString",
  "syntaxNumber",
  "syntaxType",
  "syntaxOperator",
  "syntaxPunctuation",
];

/** Quiet operator palettes: one Fleet teal accent over layered graphite/ink surfaces. */
export const darkPalette: Palette = {
  accent: "#65c3ba",
  border: "#65c3ba",
  borderAccent: "#8adfd7",
  borderMuted: "#3f4548",
  success: "#82b58b",
  error: "#d46f7c",
  warning: "#d6a75f",
  muted: "#a0a7aa",
  dim: "#6f777a",
  text: "#e6e9e8",
  thinkingText: "#889093",
  selectedBg: "#243033",
  userMessageBg: "#171d1e",
  userMessageText: "#e6e9e8",
  customMessageBg: "#1b1c22",
  toolPendingBg: "#151a1b",
  toolSuccessBg: "#142019",
  toolErrorBg: "#241719",
  toolPanelBg: "#111617",
  toolDiffAddedBg: "#17351f",
  toolDiffRemovedBg: "#361c20",
  toolTitle: "#e6e9e8",
  toolOutput: "#a0a7aa",
  mdHeading: "#8adfd7",
  mdLink: "#7ab8c8",
  mdLinkUrl: "#6f777a",
  mdCode: "#8adfd7",
  mdCodeBlock: "#a8c7a3",
  mdCodeBlockBorder: "#4b5557",
  mdQuote: "#a0a7aa",
  mdQuoteBorder: "#4b5557",
  mdHr: "#4b5557",
  mdListBullet: "#65c3ba",
  toolDiffAdded: "#9ccc9c",
  toolDiffRemoved: "#d98c8c",
  toolDiffText: "#e6e9e8",
  toolDiffContext: "#a0a7aa",
  syntaxComment: "#6a9955",
  syntaxKeyword: "#569cd6",
  syntaxFunction: "#dcdcaa",
  syntaxVariable: "#9cdcfe",
  syntaxString: "#ce9178",
  syntaxNumber: "#b5cea8",
  syntaxType: "#4ec9b0",
  syntaxOperator: "#d4d4d4",
  syntaxPunctuation: "#d4d4d4",
};

export const lightPalette: Palette = {
  accent: "#2f766f",
  border: "#2f766f",
  borderAccent: "#428f88",
  borderMuted: "#b8c2c0",
  success: "#4f7c58",
  error: "#aa4f5b",
  warning: "#8a672c",
  muted: "#626c6a",
  dim: "#7c8684",
  text: "#1b2423",
  thinkingText: "#626c6a",
  selectedBg: "#d4e3e0",
  userMessageBg: "#eaf0ef",
  userMessageText: "#1b2423",
  customMessageBg: "#f0eef4",
  toolPendingBg: "#e7eeed",
  toolSuccessBg: "#e7f0e8",
  toolErrorBg: "#f3e7e8",
  toolPanelBg: "#f1f5f4",
  toolDiffAddedBg: "#dcebdd",
  toolDiffRemovedBg: "#efdcdf",
  toolTitle: "#1b2423",
  toolOutput: "#626c6a",
  mdHeading: "#2f766f",
  mdLink: "#356f86",
  mdLinkUrl: "#7c8684",
  mdCode: "#2f766f",
  mdCodeBlock: "#4f7c58",
  mdCodeBlockBorder: "#788381",
  mdQuote: "#626c6a",
  mdQuoteBorder: "#788381",
  mdHr: "#788381",
  mdListBullet: "#2f766f",
  toolDiffAdded: "#2e7d32",
  toolDiffRemoved: "#c62828",
  toolDiffText: "#1b2423",
  toolDiffContext: "#626c6a",
  syntaxComment: "#008000",
  syntaxKeyword: "#0000ff",
  syntaxFunction: "#795e26",
  syntaxVariable: "#001080",
  syntaxString: "#a31515",
  syntaxNumber: "#098658",
  syntaxType: "#267f99",
  syntaxOperator: "#000000",
  syntaxPunctuation: "#000000",
};

export const builtinPalettes: Record<string, Palette> = {
  dark: darkPalette,
  light: lightPalette,
};

export function isThemeToken(token: string): token is ThemeColor | ThemeBackground {
  return THEME_TOKENS.includes(token as ThemeColor | ThemeBackground);
}

/** One custom theme file on disk: token overrides with optional var refs. */
export type CustomThemeFile = {
  name?: string;
  vars?: Record<string, string>;
  colors: Record<string, string>;
};

function resolveVarRefs(
  colors: Record<string, string>,
  vars: Record<string, string>,
  visited = new Set<string>(),
): Record<string, string> {
  const resolved: Record<string, string> = {};
  for (const [key, value] of Object.entries(colors)) {
    if (value.startsWith("#") || value === "") {
      resolved[key] = value;
      continue;
    }
    const path = new Set(visited);
    if (path.has(value)) {
      resolved[key] = "#ff00ff"; // circular reference: visibly broken, never fatal
      continue;
    }
    const referenced = vars[value];
    if (referenced === undefined) {
      resolved[key] = "#ff00ff";
      continue;
    }
    path.add(value);
    resolved[key] = resolveVarRefs({ [key]: referenced }, vars, path)[key] ?? "#ff00ff";
  }
  return resolved;
}

export function mergeCustomTheme(file: CustomThemeFile): Palette {
  if (!file || typeof file !== "object" || !file.colors || typeof file.colors !== "object") {
    return darkPalette;
  }
  const resolved = resolveVarRefs(file.colors, file.vars ?? {});
  const merged: Palette = { ...darkPalette };
  for (const [token, value] of Object.entries(resolved)) {
    if (isThemeToken(token) && /^#[0-9a-fA-F]{6}$/.test(value)) {
      merged[token] = value;
    }
  }
  return merged;
}

export function stateDir(): string {
  return process.env.FLEET_TUI_STATE_DIR ?? join(homedir(), ".local/share/fleet/tui");
}

export function themesDir(): string {
  return join(stateDir(), "themes");
}

export function themeSelectionPath(): string {
  return join(stateDir(), "theme");
}

export async function loadCustomThemeNames(): Promise<string[]> {
  try {
    const entries = await readdir(themesDir());
    return entries
      .filter((entry) => entry.endsWith(".json"))
      .map((entry) => entry.slice(0, -".json".length))
      .sort();
  } catch {
    return [];
  }
}

export async function loadCustomTheme(name: string): Promise<Palette | null> {
  try {
    const raw = await readFile(join(themesDir(), `${name}.json`), "utf8");
    const parsed = JSON.parse(raw) as CustomThemeFile;
    return mergeCustomTheme(parsed);
  } catch {
    return null;
  }
}

export async function readThemeSelection(): Promise<string | null> {
  try {
    const value = (await readFile(themeSelectionPath(), "utf8")).trim();
    return value || null;
  } catch {
    return null;
  }
}

export async function writeThemeSelection(name: string): Promise<void> {
  try {
    await mkdir(stateDir(), { recursive: true });
    const target = themeSelectionPath();
    const temporaryPath = `${target}.${process.pid}.part`;
    await writeFile(temporaryPath, name, "utf8");
    await rename(temporaryPath, target);
  } catch {
    // Best-effort persistence; never crash over a theme preference.
  }
}

/**
 * Watches the custom themes directory and notifies callers when theme names change.
 *
 * A watcher failure stops monitoring and is reported once through `onError`.
 *
 * @param onChange - Called with the changed theme name or the current theme names
 * @param onError - Called once when watcher setup or runtime fails
 * @returns A function that stops watching for theme changes
 */
export function watchCustomThemes(
  onChange: (names: string[]) => void,
  onError: (error: unknown) => void = reportWatcherFailure,
): () => void {
  let stopped = false;
  let reported = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let watcher: FSWatcher | undefined;
  const stopWatching = () => {
    if (timer) {
      clearTimeout(timer);
      timer = undefined;
    }
    watcher?.close();
    watcher = undefined;
  };
  const fail = (error: unknown) => {
    if (reported) return;
    reported = true;
    stopWatching();
    if (!stopped) onError(error);
  };
  const schedule = (name: string | null) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = undefined;
      if (stopped || reported) return;
      void loadCustomThemeNames().then((names) => {
        if (stopped || reported) return;
        onChange(name ? [name] : names);
      });
    }, 100);
  };
  void mkdir(themesDir(), { recursive: true })
    .then(() => {
      if (stopped) return;
      try {
        watcher = watch(themesDir(), (_event, filename) =>
          schedule(customThemeNameFromFilename(filename)),
        );
        watcher.once("error", fail);
      } catch (error) {
        fail(error);
        return;
      }
      if (stopped) stopWatching();
    })
    .catch(fail);
  return () => {
    stopped = true;
    stopWatching();
  };
}

/**
 * Reports that custom theme watching is unavailable and hot reload is disabled.
 *
 * @param error - The watcher failure to include in the warning
 */
function reportWatcherFailure(error: unknown): void {
  const detail = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  console.warn(
    `[fleet-tui] Custom theme watching is unavailable; hot reload is disabled until restart. ${detail}`,
  );
}

/**
 * Extracts a custom theme name from a JSON filename.
 *
 * @param filename - The filename to convert into a theme name
 * @returns The filename without its `.json` suffix, or `null` for other filenames or empty names
 */
function customThemeNameFromFilename(filename: string | Buffer | null): string | null {
  if (!filename) return null;
  const entry = filename.toString();
  return entry.endsWith(".json") ? entry.slice(0, -".json".length) || null : null;
}
