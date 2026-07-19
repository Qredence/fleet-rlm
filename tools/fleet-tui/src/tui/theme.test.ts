import { describe, expect, it } from "vitest";

import {
  createFleetTheme,
  editorTheme,
  getBuiltinPalette,
  getTerminalColorScheme,
  selectTheme,
  setTerminalColorScheme,
} from "./theme.js";

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
    });
  });

  it("matches upstream SelectList and Editor styling", () => {
    const accent = createFleetTheme("dark", "256color").fg("accent", "selected");
    const muted = createFleetTheme("dark", "256color").fg("muted", "detail");
    const border = createFleetTheme("dark", "256color").fg("borderMuted", "│");
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
