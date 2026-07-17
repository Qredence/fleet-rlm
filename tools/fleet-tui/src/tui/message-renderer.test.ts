import { visibleWidth } from "@earendil-works/pi-tui";
import { describe, expect, it } from "vitest";

import { renderMessage } from "./message-renderer.js";
import type { Message } from "./store.js";

describe("renderMessage", () => {
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

    expect(output.join("\n")).toContain("REASONING");
    expect(output.join("\n")).toContain("print('candidate')");
    expect(output.join("\n")).toContain("second line");
    expect(output.join("\n")).toContain("RESULT");
    expect(output.join("\n")).toContain("7");
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
});
