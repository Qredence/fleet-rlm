import { describe, expect, it, vi } from "vite-plus/test";

import { toAgentChatMessages } from "@/features/workspace/conversation/agent-chat-adapter";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

function adapter(messages: ChatMessage[]) {
  return toAgentChatMessages(messages, {
    onResolveHitl: vi.fn(),
    onResolveClarification: vi.fn(),
  });
}

describe("toAgentChatMessages", () => {
  it("maps assistant text, reasoning, and tool parts to Agent Elements message parts", () => {
    const messages = adapter([
      { id: "u1", type: "user", content: "run tests" },
      {
        id: "trace1",
        type: "trace",
        content: "",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "I should inspect the command output." }],
            isStreaming: false,
          },
          {
            kind: "tool",
            title: "bash",
            toolType: "bash",
            state: "output-available",
            input: { command: "pnpm test" },
            output: "passed",
          },
        ],
      },
      { id: "a1", type: "assistant", content: "All set", streaming: false },
    ]);

    expect(messages[0]?.role).toBe("user");
    expect(messages[1]?.role).toBe("assistant");
    expect(messages[1]?.parts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: "tool-Thinking" }),
        expect.objectContaining({ type: "tool-Bash", output: { result: "passed" } }),
      ]),
    );
    expect(messages[2]?.parts).toContainEqual({ type: "text", text: "All set" });
  });

  it("maps RLM route, sandbox, delegation, and MCP rows to Agent Elements parts", () => {
    const messages = adapter([
      {
        id: "trace-rlm",
        type: "trace",
        content: "",
        renderParts: [
          {
            kind: "status_note",
            text: "Route: url_document_rlm | source: https://example.com",
            tone: "neutral",
          },
          {
            kind: "sandbox",
            title: "summary",
            state: "output-available",
            code: "print('Example Domain')",
            output: "Example Domain",
            language: "python",
          },
          {
            kind: "tool",
            title: "delegate_to_rlm",
            toolType: "delegate_to_rlm",
            state: "output-available",
            input: { task: "summarize fetched document" },
            output: { status: "completed" },
          },
          {
            kind: "tool",
            title: "mcp__docs__fetch",
            toolType: "mcp__docs__fetch",
            state: "output-available",
            input: { url: "https://example.com" },
            output: { text: "Example Domain" },
          },
        ],
      },
    ]);

    expect(messages).toHaveLength(1);
    expect(messages[0]?.parts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "tool-Status",
          output: expect.objectContaining({
            message: expect.stringContaining("url_document_rlm"),
          }),
        }),
        expect.objectContaining({
          type: "tool-Bash",
          input: expect.objectContaining({
            command: "print('Example Domain')",
            language: "python",
          }),
          output: { stdout: "Example Domain" },
        }),
        expect.objectContaining({ type: "tool-Agent" }),
        expect.objectContaining({
          type: "tool-mcp__docs__fetch",
          output: { text: "Example Domain" },
        }),
      ]),
    );
  });

  it("maps HITL approval to tool-Question with resolved output", () => {
    const messages = adapter([
      {
        id: "hitl-1",
        type: "hitl",
        content: "Approval needed",
        hitlData: {
          question: "Approve command?",
          resolved: true,
          resolvedLabel: "Approve",
          actions: [
            { label: "Approve", variant: "primary" },
            { label: "Reject", variant: "secondary" },
          ],
        },
      },
    ]);

    expect(messages).toHaveLength(1);
    expect(messages[0]?.parts[0]).toMatchObject({
      type: "tool-Question",
      toolCallId: "hitl-1",
      state: "output-available",
      input: {
        questions: [
          {
            kind: "single",
            title: "Approve command?",
            options: [
              { id: "Approve", label: "Approve" },
              { id: "Reject", label: "Reject" },
            ],
          },
        ],
      },
      output: {
        answer: {
          kind: "single",
          selectedIds: ["Approve"],
          text: "Approve",
        },
      },
    });
  });

  it("maps clarification options to tool-Question", () => {
    const messages = adapter([
      {
        id: "clar-1",
        type: "clarification",
        content: "Which path?",
        clarificationData: {
          question: "Which path?",
          stepLabel: "Clarification needed",
          customOptionId: "",
          options: [
            { id: "src", label: "src/" },
            { id: "tests", label: "tests/" },
          ],
        },
      },
    ]);

    expect(messages[0]?.parts[0]).toMatchObject({
      type: "tool-Question",
      toolCallId: "clar-1",
      state: "call",
      input: {
        questions: [
          {
            kind: "single",
            title: "Which path?",
            allowCustom: true,
          },
        ],
      },
    });
  });
});
