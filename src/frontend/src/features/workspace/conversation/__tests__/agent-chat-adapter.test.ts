import { describe, expect, it, vi } from "vite-plus/test";

import { toAgentChatMessages } from "@/features/workspace/conversation/agent-chat-adapter";
import { applyWsFrameToMessages } from "@/lib/workspace/backend-chat-event-adapter";
import type { ChatMessage } from "@/lib/workspace/workspace-types";
import type { WsServerMessage } from "@/lib/rlm-api";

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
        expect.objectContaining({
          type: "tool-Agent",
          input: expect.objectContaining({
            description: "summarize fetched document",
            subagent_type: "delegate_to_rlm",
          }),
        }),
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

  it("maps streaming reasoning to tool-Thinking with input-streaming state", () => {
    const messages = adapter([
      {
        id: "trace-stream",
        type: "trace",
        content: "",
        renderParts: [
          {
            kind: "reasoning",
            label: "Planner",
            parts: [{ type: "text", text: "Still thinking..." }],
            isStreaming: true,
          },
        ],
      },
    ]);

    expect(messages[0]?.parts[0]).toMatchObject({
      type: "tool-Thinking",
      state: "input-streaming",
      input: { thought: "Still thinking...", label: "Planner" },
      output: undefined,
    });
  });

  it("maps backend reasoning frames into Agent Elements ThinkingTool parts", () => {
    const frame: WsServerMessage = {
      type: "event",
      data: {
        kind: "execution_step",
        text: "Recovered action reasoning",
        payload: {
          source_type: "reasoning",
          reasoning_label: "RLM action",
        },
      },
    };

    const applied = applyWsFrameToMessages([], frame);
    const messages = adapter(applied.messages);

    expect(messages[0]?.parts[0]).toMatchObject({
      type: "tool-Thinking",
      state: "input-streaming",
      input: {
        thought: "Recovered action reasoning",
        label: "RLM action",
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

  it("normalizes grep and list_files outputs into SearchTool result rows", () => {
    const messages = adapter([
      {
        id: "trace-search",
        type: "trace",
        content: "",
        renderParts: [
          {
            kind: "tool",
            title: "grep",
            toolType: "grep",
            state: "output-available",
            input: { pattern: "ThinkingTool", path: "src" },
            output: ["src/a.ts", "src/b.ts"],
          },
          {
            kind: "tool",
            title: "list_files",
            toolType: "list_files",
            state: "output-available",
            input: { path: "src/frontend" },
            output: { matches: [{ path: "src/frontend/app.tsx" }] },
          },
        ],
      },
    ]);

    const parts = messages[0]?.parts as Array<{ type?: string; output?: unknown }> | undefined;
    const grepPart = parts?.find((part) => part.type === "tool-Grep");
    const globPart = parts?.find((part) => part.type === "tool-Glob");

    expect(grepPart?.output).toEqual({
      results: [
        { source: "github", title: "src/a.ts", date: "" },
        { source: "github", title: "src/b.ts", date: "" },
      ],
    });
    expect(globPart?.output).toEqual({
      results: [{ source: "github", title: "src/frontend/app.tsx", date: "" }],
    });
  });
});
