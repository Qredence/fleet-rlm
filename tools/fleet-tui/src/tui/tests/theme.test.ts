import { getCapabilities } from "@earendil-works/pi-tui";
import { watch, type FSWatcher } from "node:fs";
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
import { darkPalette, mergeCustomTheme, watchCustomThemes } from "../themes/palette.js";

const tempDirs: string[] = [];

async function withStateDir(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "fleet-theme-"));
  tempDirs.push(dir);
  return dir;
}

/** True when a real fs.watch on this directory actually delivers events. */
function probeFsWatch(dir: string): Promise<boolean> {
  return new Promise((resolve) => {
    let watcher: FSWatcher | undefined;
    let done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try {
        watcher?.close();
      } catch {
        // probe cleanup is best-effort
      }
      resolve(ok);
    };
    const timer = setTimeout(() => finish(false), 2_000);
    try {
      watcher = watch(dir, () => finish(true));
      watcher.once("error", () => finish(false));
    } catch {
      finish(false);
      return;
    }
    void writeFile(join(dir, `.watch-probe-${process.pid}`), "x").catch(() => finish(false));
  });
}

afterEach(async () => {
  stopThemeMonitoring();
  setTerminalBackground(null);
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

describe("Fleet pi theme", () => {
  it("uses the Fleet dark and light semantic colors in truecolor terminals", () => {
    const dark = createFleetTheme("dark", "truecolor");
    const light = createFleetTheme("light", "truecolor");

    expect(dark.fg("accent", "fleet")).toBe("\x1b[38;2;101;195;186mfleet\x1b[39m");
    expect(dark.bg("userMessageBg", "prompt")).toBe("\x1b[48;2;23;29;30mprompt\x1b[49m");
    expect(light.fg("error", "failed")).toBe("\x1b[38;2;170;79;91mfailed\x1b[39m");
    expect(light.bg("toolSuccessBg", "done")).toBe("\x1b[48;2;231;240;232mdone\x1b[49m");
  });

  it("locks every Fleet dark and light semantic token", () => {
    expect(getBuiltinPalette("dark")).toEqual({
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
      toolPendingBg: "#151a1b",
      toolSuccessBg: "#142019",
      toolErrorBg: "#241719",
      customMessageBg: "#1b1c22",
      toolPanelBg: "#111617",
      toolDiffAddedBg: "#17351f",
      toolDiffRemovedBg: "#361c20",
      toolTitle: "#e6e9e8",
      toolOutput: "#a0a7aa",
      toolDiffAdded: "#9ccc9c",
      toolDiffRemoved: "#d98c8c",
      toolDiffText: "#e6e9e8",
      toolDiffContext: "#a0a7aa",
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
      toolPendingBg: "#e7eeed",
      toolSuccessBg: "#e7f0e8",
      toolErrorBg: "#f3e7e8",
      customMessageBg: "#f0eef4",
      toolPanelBg: "#f1f5f4",
      toolDiffAddedBg: "#dcebdd",
      toolDiffRemovedBg: "#efdcdf",
      toolTitle: "#1b2423",
      toolOutput: "#626c6a",
      toolDiffAdded: "#2e7d32",
      toolDiffRemoved: "#c62828",
      toolDiffText: "#1b2423",
      toolDiffContext: "#626c6a",
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

  it("applies Fleet semantics to pi-tui selection and editor styling", () => {
    const mode = getCapabilities().trueColor ? "truecolor" : "256color";
    const selected = createFleetTheme("dark", mode).fg(
      "text",
      createFleetTheme("dark", mode).selectionBackgroundColor()("selected"),
    );
    const muted = createFleetTheme("dark", mode).fg("muted", "detail");
    const border = createFleetTheme("dark", mode).fg("borderMuted", "│");
    setTerminalColorScheme("dark");

    expect(selectTheme.selectedText("selected")).toBe(selected);
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

    // fs.watch can be unavailable on fd-constrained hosts (EMFILE): hot reload
    // is disabled there by design, so only hosts with a working watcher run the
    // live assertion. Probe with a real watch against this exact directory.
    if (!(await probeFsWatch(join(dir, "themes")))) {
      console.warn("skipping hot-reload assertion: fs.watch is unavailable on this host");
      vi.unstubAllEnvs();
      return;
    }

    await setTheme("solar");
    // Rewrite on a retry cadence: an early write can race the async watcher
    // setup, so keep publishing until the debounced reload lands.
    await vi.waitFor(
      async () => {
        await writeFile(path, JSON.stringify({ colors: { accent: "#abcdef" } }));
        expect(theme.fg("accent", "x")).toContain("38;2;171;205;239");
      },
      { timeout: 5_000, interval: 500 },
    );
    await setTheme("dark");
    vi.unstubAllEnvs();
  });

  it("reports a watcher failure exactly once without throwing", async () => {
    const dir = await withStateDir();
    const filePath = join(dir, "themes-state-is-a-file");
    await writeFile(filePath, "state dir blocked by a regular file");
    vi.stubEnv("FLEET_TUI_STATE_DIR", filePath);

    const onChange = vi.fn();
    const onError = vi.fn();
    const stop = watchCustomThemes(onChange, onError);

    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1), { timeout: 2_000 });
    expect(onChange).not.toHaveBeenCalled();
    expect(onError.mock.calls[0]?.[0]).toBeInstanceOf(Error);

    // Later failures (or stopping after the failure) must never re-report or throw.
    stop();
    expect(onError).toHaveBeenCalledTimes(1);
    vi.unstubAllEnvs();
  });

  it("warns once through the default error reporter when no onError is injected", async () => {
    const dir = await withStateDir();
    const filePath = join(dir, "themes-state-blocked");
    await writeFile(filePath, "state dir blocked by a regular file");
    vi.stubEnv("FLEET_TUI_STATE_DIR", filePath);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    const stop = watchCustomThemes(() => undefined);

    await vi.waitFor(
      () =>
        expect(warn).toHaveBeenCalledWith(
          expect.stringContaining("Custom theme watching is unavailable"),
        ),
      { timeout: 2_000 },
    );
    stop();
    warn.mockRestore();
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
    expect(theme.fg("border", "x")).toContain("38;2;47;118;111"); // builtin follows scheme
    await setTheme("dark");
    vi.unstubAllEnvs();
  });
});

describe("adaptive contrast", () => {
  it("blends surfaces away from a close terminal background", () => {
    const dark = createFleetTheme("dark", "truecolor");
    const plain = dark.surfaceBackgroundColor("userMessageBg")("x");
    // Terminal background almost identical to the surface: must blend.
    setTerminalBackground({ r: 23, g: 29, b: 30 });
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

  it("blends dark selections toward the contrast endpoint in truecolor terminals", () => {
    const dark = createFleetTheme("dark", "truecolor");
    setTerminalBackground({ r: 23, g: 29, b: 30 });

    expect(dark.selectionBackgroundColor()("row")).toBe("\x1b[48;2;47;58;61mrow\x1b[49m");
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

  it("styles transcript search matches from the active theme", () => {
    setTerminalBackground(null);
    const dark = createFleetTheme("dark", "truecolor");
    const light = createFleetTheme("light", "truecolor");

    // Non-current match: plain underline, current match: selection background.
    expect(dark.getSearchMatchStyle()("hit")).toBe("\x1b[4mhit\x1b[24m");
    expect(dark.getSearchCurrentMatchStyle()("hit")).toBe("\x1b[48;2;36;48;51mhit\x1b[49m");
    expect(light.getSearchCurrentMatchStyle()("hit")).toBe("\x1b[48;2;212;227;224mhit\x1b[49m");

    // The facade resolves against the active theme at call time.
    setTerminalColorScheme("dark");
    expect(theme.searchMatch()("hit")).toBe("\x1b[4mhit\x1b[24m");
    expect(theme.currentSearchMatch()("hit")).toBe(dark.getSearchCurrentMatchStyle()("hit"));
    setTerminalColorScheme("light");
    expect(theme.currentSearchMatch()("hit")).toBe(light.getSearchCurrentMatchStyle()("hit"));
    setTerminalColorScheme("dark");
  });

  it("exposes the settings list theme factories", () => {
    expect(typeof settingsListTheme.label).toBe("function");
    expect(typeof settingsListTheme.value).toBe("function");
    expect(typeof settingsListTheme.description).toBe("function");
    expect(typeof settingsListTheme.cursor).toBe("string");
    expect(typeof settingsListTheme.hint).toBe("function");
  });
});
