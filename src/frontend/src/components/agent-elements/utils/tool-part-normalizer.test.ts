import { describe, expect, it } from "vite-plus/test";

import { normalizeAssistantToolParts } from "@/components/agent-elements/utils/tool-part-normalizer";

describe("normalizeAssistantToolParts", () => {
  it("keeps consecutive status, thinking, search, read, and command rows inline", () => {
    const parts = [
      {
        type: "tool-Status",
        toolCallId: "trace-1:status:0",
        state: "output-available",
        input: { message: "Calling tool find_files" },
      },
      {
        type: "tool-Thinking",
        toolCallId: "trace-1:thinking:1",
        state: "input-streaming",
        input: { thought: "Inspect repository" },
      },
      {
        type: "tool-Grep",
        toolCallId: "trace-1:find_files:2",
        state: "output-available",
        input: { query: "ToolGroup" },
        output: { results: [{ source: "github", title: "src/a.ts", date: "" }] },
        toolName: "find_files",
      },
      {
        type: "tool-Read",
        toolCallId: "trace-1:read_file_slice:3",
        state: "output-available",
        input: { file_path: "src/a.ts" },
        output: { result: "export const value = true" },
        toolName: "read_file_slice",
      },
      {
        type: "tool-Bash",
        toolCallId: "trace-1:repl_execute:4",
        state: "call",
        input: { command: "repl_execute" },
        toolName: "repl_execute",
      },
    ];

    const normalized = normalizeAssistantToolParts(parts);

    expect(normalized).toHaveLength(parts.length);
    expect(normalized).toEqual(parts);
    expect(normalized.some((part: any) => part.type === "tool-Group")).toBe(false);
  });

  it("preserves explicit tool groups and normalizes nested structured values", () => {
    const normalized = normalizeAssistantToolParts([
      {
        type: "tool-Group",
        toolCallId: "explicit-group",
        state: "input-streaming",
        input: '{"description":"Runtime activity"}',
        nestedTools: [
          {
            type: "tool-Status",
            toolCallId: "status-1",
            state: "output-available",
            input: '{"message":"Preparing workspace"}',
            output: '{"message":"Preparing workspace"}',
          },
          {
            type: "tool-Grep",
            toolCallId: "grep-1",
            state: "output-available",
            input: { query: "skills" },
            output: { results: [{ source: "github", title: "AGENTS.md", date: "" }] },
          },
        ],
      },
    ]);

    expect(normalized).toHaveLength(1);
    expect(normalized[0]).toMatchObject({
      type: "tool-Group",
      toolCallId: "explicit-group",
      input: { description: "Runtime activity" },
      nestedTools: [
        expect.objectContaining({
          type: "tool-Status",
          input: { message: "Preparing workspace" },
          output: { message: "Preparing workspace" },
        }),
        expect.objectContaining({ type: "tool-Grep" }),
      ],
    });
  });

  it("keeps real subagent parents and child tools as separate parts", () => {
    const parts = [
      {
        type: "tool-Agent",
        toolCallId: "agent-parent",
        state: "call",
        input: { description: "Inspect skill usage", subagent_type: "delegate_to_rlm" },
        toolName: "delegate_to_rlm",
      },
      {
        type: "tool-Grep",
        toolCallId: "agent-parent:trace-2:find_files:1",
        state: "output-available",
        input: { query: "skills" },
        output: { results: [{ source: "github", title: "AGENTS.md", date: "" }] },
        toolName: "find_files",
      },
    ];

    expect(normalizeAssistantToolParts(parts)).toEqual(parts);
  });

  it("parses JSON input, output, and result without changing row shape", () => {
    const normalized = normalizeAssistantToolParts([
      {
        type: "tool-Tool",
        toolCallId: "tool-1",
        state: "output-available",
        input: '{"description":"Inspect document headings"}',
        output: '{"ok":true}',
        result: '{"count":2}',
        toolName: "tool",
      },
    ]);

    expect(normalized).toEqual([
      {
        type: "tool-Tool",
        toolCallId: "tool-1",
        state: "output-available",
        input: { description: "Inspect document headings" },
        output: { ok: true },
        result: { count: 2 },
        toolName: "tool",
      },
    ]);
  });
});
