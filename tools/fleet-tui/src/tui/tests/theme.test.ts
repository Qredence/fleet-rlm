import { getCapabilities } from "@earendil-works/pi-tui";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createFleetTheme,
  editorTheme,
  FleetTheme,
  getAvailableThemes,
  getBuiltinPalette,
  getTerminalColorScheme,
  getThemeName,
  initTheme,
  selectTheme,
  setTerminalBackground,
  setTerminalColorScheme,
  setTheme,
  settingsListTheme,
  stopThemeMonitoring,
  theme,
} from "../theme.js";
import { darkPalette, mergeCustomTheme } from "../themes/palette.js";

const tempDirs: string[] = [];

async function withStateDir(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "fleet-theme-"));
  tempDirs.push(dir);
  return dir;
}

afterEach(async () => {
  stopThemeMonitoring();
  setTerminalBackground(null);
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

describe("Fleet pi theme", () => {
  it("uses the upstream dark and light semantic colors in truecolor terminals", () => {
    const dark = createFleetTheme("dark", "truecolor");
    const light = createFleetTheme("light", "truecolor");

    expect(dark.fg("accent", "fleet")).toBe("\x1b[38;2;138;190;183mfleet\x1b[39m");
    expect(dark.bg("userMessageBg", "prompt")).toBe("\x1b[48;2;52;53;65mprompt\x1b[49m");
    expect(light.fg("error", "failed")).toBe("\x1b[38;2;170;85;85mfailed\x1b[39m");
    expect(light.bg("toolSuccessBg", "done")).toBe("\x1b[48;2;232;240;232mdone\x1b[49m");
  });

  it("locks every upstream dark and light semantic token", () => {
    expect(getBuiltinPalette("dark")).toEqual({
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
      customMessageBg: "#2d2838",
      toolPanelBg: "#23232b",
      toolDiffAddedBg: "#1f3a26",
      toolDiffRemovedBg: "#3a2323",
      toolTitle: "#d4d4d4",
      toolOutput: "#808080",
      toolDiffAdded: "#9ccc9c",
      toolDiffRemoved: "#d98c8c",
      toolDiffText: "#d4d4d4",
      toolDiffContext: "#808080",
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
    });
    expect(getBuiltinPalette("light")).toEqual({
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
      customMessageBg: "#f0ecf6",
      toolPanelBg: "#f2f2f6",
      toolDiffAddedBg: "#ddf0dd",
      toolDiffRemovedBg: "#f0dddd",
      toolTitle: "#1f2328",
      toolOutput: "#6c6c6c",
      toolDiffAdded: "#2e7d32",
      toolDiffRemoved: "#c62828",
      toolDiffText: "#1f2328",
      toolDiffContext: "#6c6c6c",
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
    });
  });

  it("matches upstream SelectList and Editor styling", () => {
    const mode = getCapabilities().trueColor ? "truecolor" : "256color";
    const accent = createFleetTheme("dark", mode).fg("accent", "selected");
    const muted = createFleetTheme("dark", mode).fg("muted", "detail");
    const border = createFleetTheme("dark", mode).fg("borderMuted", "│");
    setTerminalColorScheme("dark");

    expect(selectTheme.selectedText("selected")).toBe(accent);
    expect(selectTheme.description("detail")).toBe(muted);
    expect(selectTheme.scrollInfo("detail")).toBe(muted);
    expect(selectTheme.noMatch("detail")).toBe(muted);
    expect(editorTheme.borderColor("│")).toBe(border);
  });

  it("maps semantic colors to ANSI-256 and keeps foreground resets isolated", () => {
    const dark = createFleetTheme("dark", "256color");
    const ansiEscape = String.fromCharCode(27);

    expect(dark.fg("accent", "fleet")).toMatch(new RegExp(`^${ansiEscape}\\[38;5;\\d+mfleet`));
    const nested = dark.bg("toolErrorBg", dark.fg("error", "failed"));
    expect(nested.startsWith(`${ansiEscape}[48;5;`)).toBe(true);
    expect(nested).toContain(`${ansiEscape}[38;5;`);
    expect(nested.endsWith(`${ansiEscape}[39m${ansiEscape}[49m`)).toBe(true);
  });

  it("switches the active terminal scheme explicitly", () => {
    setTerminalColorScheme("light");
    expect(getTerminalColorScheme()).toBe("light");
    setTerminalColorScheme("dark");
    expect(getTerminalColorScheme()).toBe("dark");
  });
});

describe("theme selection", () => {
  it("lists builtins plus custom JSON themes and switches between them", async () => {
    const dir = await withStateDir();
    await mkdir(join(dir, "themes"), { recursive: true });
    await writeFile(
      join(dir, "themes", "solar.json"),
      JSON.stringify({
        name: "solar",
        vars: { accentVar: "#ff8800" },
        colors: { accent: "accentVar", border: "#123456" },
      }),
    );
    vi.stubEnv("FLEET_TUI_STATE_DIR", dir);

    expect(await getAvailableThemes()).toEqual(["dark", "light", "solar"]);
    const result = await setTheme("solar");
    expect(result.success).toBe(true);
    expect(getThemeName()).toBe("solar");
    expect(theme.fg("accent", "x")).toContain("38;2;255;136;0"); // var resolved

    await setTheme("dark");
    expect(getThemeName()).toBe("dark");
    vi.unstubAllEnvs();
  });

  it("ignores unknown and malformed themes without failing", async () => {
    const dir = await withStateDir();
    await mkdir(join(dir, "themes"), { recursive: true });
    await writeFile(join(dir, "themes", "broken.json"), "{not json");
    vi.stubEnv("FLEET_TUI_STATE_DIR", dir);

    const result = await setTheme("broken");
    expect(result.success).toBe(false);
    expect(getThemeName()).toBe("dark");
    vi.unstubAllEnvs();
  });

  it("initializes from an explicit override", async () => {
    await initTheme("light");
    expect(getThemeName()).toBe("light");
    expect(getTerminalColorScheme()).toBe("light");
    await initTheme("dark");
    expect(getThemeName()).toBe("dark");
  });

  it("resolves shared variables independently and ignores empty color overrides", () => {
    const palette = mergeCustomTheme({
      vars: { base: "#123456" },
      colors: { accent: "base", border: "base", error: "" },
    });

    expect(palette.accent).toBe("#123456");
    expect(palette.border).toBe("#123456");
    expect(palette.error).toBe(darkPalette.error);
  });

  it("hot-reloads a custom theme when its JSON file changes", async () => {
    const dir = await withStateDir();
    await mkdir(join(dir, "themes"), { recursive: true });
    const path = join(dir, "themes", "solar.json");
    await writeFile(path, JSON.stringify({ colors: { accent: "#123456" } }));
    vi.stubEnv("FLEET_TUI_STATE_DIR", dir);

    await setTheme("solar");
    await new Promise((resolve) => setTimeout(resolve, 100));
    await writeFile(path, JSON.stringify({ colors: { accent: "#abcdef" } }));

    await vi.waitFor(() => expect(theme.fg("accent", "x")).toContain("38;2;171;205;239"), {
      timeout: 2_000,
    });
    await setTheme("dark");
    vi.unstubAllEnvs();
  });

  it("custom themes win over the auto terminal scheme; builtins follow it", async () => {
    const dir = await withStateDir();
    await mkdir(join(dir, "themes"), { recursive: true });
    await writeFile(
      join(dir, "themes", "solar.json"),
      JSON.stringify({ colors: { border: "#123456" } }),
    );
    vi.stubEnv("FLEET_TUI_STATE_DIR", dir);
    await setTheme("solar");
    setTerminalColorScheme("light");
    expect(getTerminalColorScheme()).toBe("light");
    expect(theme.fg("border", "x")).toContain("38;2;18;52;86"); // solar stays active
    await setTheme("dark");
    setTerminalColorScheme("light");
    expect(getTerminalColorScheme()).toBe("light");
    expect(theme.fg("border", "x")).toContain("38;2;84;125;167"); // builtin follows scheme
    await setTheme("dark");
    vi.unstubAllEnvs();
  });
});

describe("adaptive contrast", () => {
  it("blends surfaces away from a close terminal background", () => {
    const dark = createFleetTheme("dark", "truecolor");
    const plain = dark.surfaceBackgroundColor("userMessageBg")("x");
    // Terminal background almost identical to the surface: must blend.
    setTerminalBackground({ r: 52, g: 53, b: 65 });
    const blended = dark.surfaceBackgroundColor("userMessageBg")("x");
    expect(blended).not.toBe(plain);
    expect(blended.endsWith("x\x1b[49m")).toBe(true);
    // Distant terminal background: plain token wins.
    setTerminalBackground({ r: 255, g: 255, b: 255 });
    expect(dark.surfaceBackgroundColor("userMessageBg")("x")).toBe(plain);
  });

  it("guarantees a minimum contrast delta for selections", () => {
    const dark = createFleetTheme("dark", "truecolor");
    setTerminalBackground({ r: 58, g: 58, b: 74 }); // ~ selectedBg #3a3a4a
    const styled = dark.selectionBackgroundColor()("row");
    expect(styled.startsWith("\x1b[48;2;")).toBe(true);
    expect(styled.endsWith("row\x1b[49m")).toBe(true);
  });

  it("blends a near-midpoint surface until it clears the minimum delta", () => {
    const custom = new FleetTheme({ ...darkPalette, userMessageBg: "#8b8b8b" }, "truecolor");
    setTerminalBackground({ r: 128, g: 128, b: 128 });

    const styled = custom.surfaceBackgroundColor("userMessageBg")("row");
    const match = styled.match(
      new RegExp(`${String.fromCharCode(27)}\\[48;2;(\\d+);(\\d+);(\\d+)m`),
    );
    expect(match).not.toBeNull();
    const [, red, green, blue] = match ?? [];
    const surfaceLuminance = 0.299 * Number(red) + 0.587 * Number(green) + 0.114 * Number(blue);
    expect(Math.abs(surfaceLuminance - 128)).toBeGreaterThanOrEqual(12);
  });

  it("exposes the settings list theme factories", () => {
    expect(typeof settingsListTheme.label).toBe("function");
    expect(typeof settingsListTheme.value).toBe("function");
    expect(typeof settingsListTheme.description).toBe("function");
    expect(typeof settingsListTheme.cursor).toBe("string");
    expect(typeof settingsListTheme.hint).toBe("function");
  });
});
