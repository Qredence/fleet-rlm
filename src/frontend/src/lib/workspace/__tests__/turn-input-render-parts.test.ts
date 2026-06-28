import { describe, expect, it } from "vitest";
import { chatRenderPartToAgentToolPart } from "@/lib/workspace/agent-tool-parts";
import type { ChatRenderPart } from "@/lib/workspace/workspace-types";

describe("chatRenderPartToAgentToolPart - turn input rows", () => {
  const messageId = "msg-1";

  describe("request_row", () => {
    it("maps to tool-RequestRow with output-available state", () => {
      const part: ChatRenderPart = {
        kind: "request_row",
        label: "User request",
        value: "Write a function to calculate fibonacci",
        preview: "Write a function...",
      };

      const result = chatRenderPartToAgentToolPart(part, messageId, 0);

      expect(result).not.toBeNull();
      expect(result!.type).toBe("tool-RequestRow");
      expect(result!.state).toBe("output-available");
      expect(result!.input).toEqual({
        label: "User request",
        value: "Write a function to calculate fibonacci",
        preview: "Write a function...",
      });
      expect(result!.output).toEqual({
        label: "User request",
        value: "Write a function to calculate fibonacci",
      });
    });
  });

  describe("skills_row", () => {
    it("maps to tool-SkillsRow with skills array", () => {
      const part: ChatRenderPart = {
        kind: "skills_row",
        label: "Active skills",
        skills: ["web-search", "code-interpreter"],
        preview: "2 skills selected",
      };

      const result = chatRenderPartToAgentToolPart(part, messageId, 1);

      expect(result).not.toBeNull();
      expect(result!.type).toBe("tool-SkillsRow");
      expect(result!.state).toBe("output-available");
      expect(result!.input).toEqual({
        label: "Active skills",
        skills: ["web-search", "code-interpreter"],
        preview: "2 skills selected",
      });
      expect(result!.output).toEqual({
        label: "Active skills",
        skills: ["web-search", "code-interpreter"],
      });
    });

    it("handles empty skills array", () => {
      const part: ChatRenderPart = {
        kind: "skills_row",
        label: "Active skills",
        skills: [],
      };

      const result = chatRenderPartToAgentToolPart(part, messageId, 1);

      expect(result).not.toBeNull();
      expect(result!.type).toBe("tool-SkillsRow");
      expect(result!.input).toEqual({
        label: "Active skills",
        skills: [],
        preview: undefined,
      });
    });
  });

  describe("history_row", () => {
    it("maps to tool-HistoryRow with turn count", () => {
      const part: ChatRenderPart = {
        kind: "history_row",
        label: "Conversation history",
        turnCount: 5,
        value: "5 prior turns",
        preview: "5 turns",
      };

      const result = chatRenderPartToAgentToolPart(part, messageId, 2);

      expect(result).not.toBeNull();
      expect(result!.type).toBe("tool-HistoryRow");
      expect(result!.state).toBe("output-available");
      expect(result!.input).toEqual({
        label: "Conversation history",
        turnCount: 5,
        value: "5 prior turns",
        preview: "5 turns",
      });
      expect(result!.output).toEqual({
        label: "Conversation history",
        turnCount: 5,
        value: "5 prior turns",
      });
    });

    it("handles zero turn count", () => {
      const part: ChatRenderPart = {
        kind: "history_row",
        label: "History",
        turnCount: 0,
      };

      const result = chatRenderPartToAgentToolPart(part, messageId, 2);

      expect(result).not.toBeNull();
      expect(result!.type).toBe("tool-HistoryRow");
      expect(result!.input).toEqual({
        label: "History",
        turnCount: 0,
        value: undefined,
        preview: undefined,
      });
    });
  });

  describe("core_memory_row", () => {
    it("maps to tool-CoreMemoryRow with value and preview", () => {
      const part: ChatRenderPart = {
        kind: "core_memory_row",
        label: "Core memory",
        value: "User prefers TypeScript and React. Works on web applications.",
        preview: "User preferences...",
      };

      const result = chatRenderPartToAgentToolPart(part, messageId, 3);

      expect(result).not.toBeNull();
      expect(result!.type).toBe("tool-CoreMemoryRow");
      expect(result!.state).toBe("output-available");
      expect(result!.input).toEqual({
        label: "Core memory",
        value: "User prefers TypeScript and React. Works on web applications.",
        preview: "User preferences...",
      });
      expect(result!.output).toEqual({
        label: "Core memory",
        value: "User prefers TypeScript and React. Works on web applications.",
      });
    });
  });

  describe("context_row", () => {
    it("maps to tool-ContextRow with value and preview", () => {
      const part: ChatRenderPart = {
        kind: "context_row",
        label: "Workspace context",
        value: "Project: Fleet RLM\nFiles: 15 modified\nBranch: feature/turn-inputs",
        preview: "Project context...",
      };

      const result = chatRenderPartToAgentToolPart(part, messageId, 4);

      expect(result).not.toBeNull();
      expect(result!.type).toBe("tool-ContextRow");
      expect(result!.state).toBe("output-available");
      expect(result!.input).toEqual({
        label: "Workspace context",
        value: "Project: Fleet RLM\nFiles: 15 modified\nBranch: feature/turn-inputs",
        preview: "Project context...",
      });
      expect(result!.output).toEqual({
        label: "Workspace context",
        value: "Project: Fleet RLM\nFiles: 15 modified\nBranch: feature/turn-inputs",
      });
    });
  });

  describe("unknown kinds", () => {
    it("returns null for unknown kinds (legacy behavior preserved)", () => {
      const part = {
        kind: "unknown_kind",
        data: "some data",
      } as unknown as ChatRenderPart;

      const result = chatRenderPartToAgentToolPart(part, messageId, 5);

      expect(result).toBeNull();
    });
  });

  describe("stable tool call IDs", () => {
    it("generates stable IDs based on kind and index", () => {
      const requestPart: ChatRenderPart = {
        kind: "request_row",
        label: "Request",
        value: "test",
      };

      const result1 = chatRenderPartToAgentToolPart(requestPart, messageId, 0);
      const result2 = chatRenderPartToAgentToolPart(requestPart, messageId, 0);
      const result3 = chatRenderPartToAgentToolPart(requestPart, messageId, 1);

      expect(result1!.toolCallId).toBe(result2!.toolCallId);
      expect(result1!.toolCallId).not.toBe(result3!.toolCallId);
      expect(result1!.toolCallId).toContain("request_row");
    });
  });
});
