import { visibleWidth } from "@earendil-works/pi-tui";
import { beforeEach, describe, expect, it } from "vitest";

import { renderMessage } from "../message-renderer.js";
import type { Message } from "../store.js";
import { setTerminalColorScheme } from "../theme.js";
import { terminalSafeText } from "../terminal-text.js";

describe("renderMessage", () => {
  beforeEach(() => setTerminalColorScheme("dark"));

  it("uses the pi user surface and leaves assistant prose unboxed", () => {
    const user: Message = {
      id: "user",
      kind: "text",
      role: "user",
      text: "Inspect this",
      streaming: false,
      ts: 1,
    };
    const assistant: Message = {
      id: "assistant",
      kind: "text",
      role: "assistant",
      text: "Working on it",
      streaming: false,
      ts: 2,
    };

    const userLines = renderMessage(user, 32);
    const assistantLines = renderMessage(assistant, 32);

    expect(userLines.every((line) => visibleWidth(line) === 32)).toBe(true);
    expect(userLines.join("\n")).toContain("\x1b[48;");
    expect(userLines.join("\n")).toContain("Inspect this");
    expect(assistantLines.join("\n")).not.toContain("\x1b[48;");
    expect(assistantLines.join("\n")).not.toContain("FLEET");
    expect(assistantLines.join("\n")).toContain("Working on it");
  });

  it("pads and wraps ANSI-styled Unicode surfaces by terminal cell width", () => {
    const user: Message = {
      id: "unicode-user",
      kind: "text",
      role: "user",
      text: "界😀e\u0301 interface",
      streaming: false,
      ts: 1,
    };
    const tool: Message = {
      id: "unicode-tool",
      kind: "tool",
      runId: "run",
      toolCallId: "call",
      name: "調査😀",
      input: { query: "界e\u0301" },
      output: "完了✅",
      startedAt: 0,
      endedAt: 1,
      status: "success",
      ts: 2,
    };

    for (const line of [...renderMessage(user, 18), ...renderMessage(tool, 18)]) {
      expect(visibleWidth(line)).toBe(18);
    }
  });

  it("uses distinct pi surfaces for pending, successful, and failed Tools", () => {
    const base = {
      id: "tool",
      kind: "tool" as const,
      runId: "run",
      toolCallId: "call",
      name: "inspect",
      input: {},
      startedAt: 0,
      ts: 1,
    };
    const running = renderMessage({ ...base, status: "running" }, 48).join("\n");
    const success = renderMessage({ ...base, status: "success", endedAt: 1 }, 48).join("\n");
    const failed = renderMessage(
      { ...base, status: "error", error: "failed safely", endedAt: 1 },
      48,
    ).join("\n");
    const backgrounds = [running, success, failed].map((output) => firstAnsi(output, "48"));
    expect(backgrounds.every(Boolean)).toBe(true);
    expect(new Set(backgrounds).size).toBe(3);
    expect(failed).toContain("failed safely");
  });

  it("uses semantic warning and error colors without hiding their diagnostics", () => {
    const warning = renderMessage(
      {
        id: "warning",
        kind: "warning",
        runId: "run",
        code: "retry",
        message: "Try again",
        ts: 1,
      },
      40,
    ).join("\n");
    const error = renderMessage(
      { id: "error", kind: "error", text: "Connection failed", ts: 2 },
      40,
    ).join("\n");

    expect(warning).toContain("\x1b[38;");
    expect(warning).toContain("Try again");
    expect(error).toContain("\x1b[38;");
    expect(error).toContain("Connection failed");
    expect(firstAnsi(warning, "38")).not.toBe(firstAnsi(error, "38"));
  });

  it("renders headings, inline styles, lists, quotes, links, fenced code, and tables as Markdown", () => {
    const message: Message = {
      id: "markdown",
      kind: "text",
      role: "assistant",
      streaming: false,
      text: [
        "# Report",
        "",
        "Use **bold**, *emphasis*, and [Fleet](https://fleet.example).",
        "",
        "- first",
        "- second",
        "",
        "> verified",
        "",
        "```python",
        "print('ok')",
        "```",
        "",
        "| item | value |",
        "| --- | --- |",
        "| answer | 42 |",
      ].join("\n"),
      ts: 1,
    };

    const lines = renderMessage(message, 60);
    const output = stripAnsi(lines.join("\n"));

    expect(output).toContain("Report");
    expect(output).toContain("bold");
    expect(output).toContain("emphasis");
    expect(output).toContain("Fleet");
    expect(output).toContain("- first");
    expect(output).toContain("│ verified");
    expect(output).toContain("```python");
    expect(output).toContain("print('ok')");
    expect(output).toContain("┌─");
    expect(output).toContain("answer");
    expect(lines.every((line) => visibleWidth(line) <= 60)).toBe(true);
  });

  it("keeps partial fenced code stable while assistant Markdown is streaming", () => {
    const message: Message = {
      id: "streaming-markdown",
      kind: "text",
      role: "assistant",
      streaming: true,
      text: "```ts\nconst value = 1;\n``",
      ts: 1,
    };

    const output = stripAnsi(renderMessage(message, 40).join("\n"));
    expect(output).toContain("```ts");
    expect(output).toContain("const value = 1;");
    expect(output).not.toContain("\n  ``\n");
  });

  it("keeps a streaming cursor visible without exceeding narrow terminal widths", () => {
    const message: Message = {
      id: "narrow-stream",
      kind: "text",
      role: "assistant",
      text: "1234567890",
      streaming: true,
      ts: 1,
    };

    for (const width of [1, 2, 3, 8, 20]) {
      const lines = renderMessage(message, width);
      expect(lines.every((line) => visibleWidth(line) <= width)).toBe(true);
      expect(stripAnsi(lines.join("\n"))).toContain("█");
    }
  });

  it("renders partial RLM code and output as live evidence", () => {
    const code: Message = {
      id: "partial-code",
      kind: "code",
      runId: "run",
      step: 1,
      code: "answer =",
      language: "python",
      streaming: true,
      ts: 1,
    };
    const output: Message = {
      id: "partial-output",
      kind: "output",
      runId: "run",
      step: 1,
      output: "partial output",
      streaming: true,
      ts: 2,
    };

    const visible = stripAnsi(
      [...renderMessage(code, 40), ...renderMessage(output, 40)].join("\n"),
    );

    expect(visible).toContain("CODE  step 1 · streaming");
    expect(visible).toContain("OUTPUT  step 1 · streaming");
    expect(visible).toContain("answer =");
    expect(visible).toContain("partial output");
  });

  it("strips terminal control sequences from streamed evidence", () => {
    const message: Message = {
      id: "unsafe-output",
      kind: "output",
      runId: "run",
      step: 1,
      output: "safe\x1b]52;c;secret\x07",
      streaming: false,
      ts: 1,
    };

    const output = renderMessage(message, 48).join("\n");

    expect(output).toContain("safe");
    expect(output).not.toContain("secret");
    expect(output).not.toContain("\x1b]52");
  });

  it("strips Unicode format controls from terminal text", () => {
    expect(terminalSafeText("safe\u200bhidden\u202Edirection")).toBe("safehiddendirection");
  });

  it("renders static complete execution evidence within the terminal width", () => {
    const messages: Message[] = [
      {
        id: "reason",
        kind: "reasoning",
        runId: "run",
        step: 1,
        text: "Inspect **all** evidence.",
        ts: 1,
      },
      { id: "code", kind: "code", runId: "run", step: 1, code: "print('candidate')", ts: 2 },
      {
        id: "output",
        kind: "output",
        runId: "run",
        step: 1,
        output: "candidate\nsecond line",
        ts: 3,
      },
      {
        id: "result",
        kind: "result",
        runId: "run",
        schemaId: "answer",
        schemaVersion: "1",
        value: { digit: "7" },
        narrative: "The answer is **7**.",
        ts: 4,
      },
    ];

    const output = messages.flatMap((message) => renderMessage(message, 52));
    const visible = stripAnsi(output.join("\n"));

    expect(visible).toContain("REASONING");
    expect(visible).toContain("print('candidate')");
    expect(visible).toContain("second line");
    expect(visible).toContain("RESULT");
    expect(visible).toContain("7");
    expect(output.every((line) => visibleWidth(line) <= 52)).toBe(true);
  });

  it("renders full sanitized tool values instead of bounded previews", () => {
    const longValue = "x".repeat(400);
    const message: Message = {
      id: "tool",
      kind: "tool",
      runId: "run",
      toolCallId: "call",
      name: "inspect",
      input: { api_key: "secret", query: longValue },
      output: { result: longValue },
      startedAt: 0,
      endedAt: 1,
      status: "success",
      ts: 1,
    };

    const output = renderMessage(message, 60).join("\n");
    expect(output).toContain("[redacted]");
    expect(output).not.toContain("secret");
    expect(output.match(/x/g)?.length).toBe(800);
  });

  it("wraps long evidence metadata without truncating it", () => {
    const filename = `${"q".repeat(100)}.txt`;
    const message: Message = {
      id: "attachment",
      kind: "attachment",
      runId: "run",
      attachmentId: "attachment-id",
      filename,
      bytes: 42,
      ts: 1,
    };

    const output = renderMessage(message, 24);

    expect(output.join("").match(/q/g)?.length).toBe(100);
    expect(output.every((line) => visibleWidth(line) <= 24)).toBe(true);
  });

  it("renders activated and loaded Skills as distinct lifecycle events", () => {
    const activated: Message = {
      id: "activated",
      kind: "skill",
      runId: "run",
      skillId: "skill",
      name: "inspect",
      phase: "activated",
      version: "2",
      trust: "workspace",
      ts: 1,
    };
    const loaded: Message = {
      id: "loaded",
      kind: "skill",
      runId: "run",
      skillId: "skill",
      name: "inspect",
      phase: "loaded",
      version: "2",
      ts: 2,
    };

    expect(renderMessage(activated, 60).join("\n")).toContain("SKILL ACTIVATED");
    const loadedOutput = renderMessage(loaded, 60).join("\n");
    expect(loadedOutput).toContain("SKILL LOADED");
    expect(loadedOutput).not.toContain("system");
  });

  it("renders the completed execution summary and preserves unknown telemetry", () => {
    const complete: Message = {
      id: "usage",
      kind: "usage",
      runId: "run",
      iterations: 2,
      inputTokens: 1200,
      outputTokens: 300,
      durationMs: 4200,
      observedLmUsage: {},
      executionSummary: {
        iterations: 2,
        subLmCalls: 0,
        hostCapabilityCalls: 1,
        interpreterErrors: 0,
        durationMs: 4200,
      },
      ts: 1,
    };
    const partial: Message = {
      ...complete,
      id: "partial",
      executionSummary: {
        iterations: null,
        subLmCalls: null,
        hostCapabilityCalls: null,
        interpreterErrors: null,
        durationMs: null,
      },
    };

    const completedOutput = stripAnsi(renderMessage(complete, 100).join("\n"));
    const partialOutput = stripAnsi(renderMessage(partial, 100).join("\n"));

    expect(completedOutput).toContain("2 iterations");
    expect(completedOutput).toContain("0 sub-LM");
    expect(completedOutput).toContain("1 host");
    expect(completedOutput).toContain("0 errors");
    expect(partialOutput).toContain("— iterations");
    expect(partialOutput).toContain("— sub-LM");
    expect(partialOutput).toContain("— host");
    expect(partialOutput).toContain("— errors");
  });
});

function stripAnsi(value: string): string {
  return value.replaceAll(new RegExp(`${String.fromCharCode(27)}\\[[\\d;]*m`, "g"), "");
}

function firstAnsi(value: string, layer: "38" | "48"): string | undefined {
  return value.match(new RegExp(`${String.fromCharCode(27)}\\[${layer};[^m]+m`))?.[0];
}
