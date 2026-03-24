import { describe, expect, it } from "vite-plus/test";
import {
  buildChatDisplayItems,
  buildPendingAssistantTurnId,
} from "@/lib/workspace/chat-display-items";
import type { ChatMessage } from "@/screens/workspace/use-workspace";

describe("buildChatDisplayItems", () => {
  it("attaches contiguous reasoning directly to the following assistant turn", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-reasoning",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Thinking through the request." }],
            isStreaming: false,
          },
        ],
      },
      {
        id: "assistant-final",
        type: "assistant",
        content: "Here is the answer.",
        streaming: false,
      },
    ];

    const items = buildChatDisplayItems(messages);

    expect(items).toHaveLength(1);
    expect(items[0]?.kind).toBe("assistant_turn");
    if (items[0]?.kind === "assistant_turn") {
      expect(items[0].message?.id).toBe("assistant-final");
      expect(items[0].reasoningItems).toHaveLength(1);
      expect(items[0].reasoningItems[0]?.part.parts[0]?.text).toBe("Thinking through the request.");
    }
  });

  it("attaches trailing tool activity and adjacent reasoning to the following assistant turn", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-reasoning",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "First thought." }],
            isStreaming: false,
          },
        ],
      },
      {
        id: "trace-tool",
        type: "trace",
        content: "tool",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "search_files",
            toolType: "search_files",
            state: "running",
            input: { pattern: "workspace" },
          },
        ],
      },
      {
        id: "assistant-final",
        type: "assistant",
        content: "Done.",
        streaming: false,
      },
    ];

    const items = buildChatDisplayItems(messages);

    expect(items).toHaveLength(1);
    expect(items[0]?.kind).toBe("assistant_turn");
    if (items[0]?.kind === "assistant_turn") {
      expect(items[0].message?.content).toBe("Done.");
      expect(items[0].reasoningItems).toHaveLength(1);
      expect(items[0].attachedToolSessions).toHaveLength(1);
      expect(items[0].attachedToolSessions[0]?.items[0]?.toolName).toBe("search_files");
    }
  });

  it("merges trailing summary reasoning into the completed assistant turn", () => {
    const messages: ChatMessage[] = [
      {
        id: "assistant-final",
        type: "assistant",
        content: "Done.",
        streaming: false,
      },
      {
        id: "trace-final-reasoning",
        type: "trace",
        content: "summary",
        traceSource: "summary",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Final reasoning: kept together." }],
            isStreaming: false,
          },
        ],
      },
    ];

    const items = buildChatDisplayItems(messages);

    expect(items).toHaveLength(1);
    expect(items[0]?.kind).toBe("assistant_turn");
    if (items[0]?.kind === "assistant_turn") {
      expect(items[0].message?.id).toBe("assistant-final");
      expect(items[0].reasoningItems).toHaveLength(1);
      expect(items[0].reasoningItems[0]?.part.parts[0]?.text).toBe(
        "Final reasoning: kept together.",
      );
    }
  });

  it("keeps trace-only tool sessions standalone when there is no assistant response", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-tool",
        type: "trace",
        content: "tool",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "search_files",
            toolType: "search_files",
            state: "running",
            input: { pattern: "workspace" },
          },
        ],
      },
    ];

    const items = buildChatDisplayItems(messages);

    expect(items).toHaveLength(1);
    expect(items[0]?.kind).toBe("tool_session");
  });

  it("creates a pending assistant shell for the active turn and folds in live trace data", () => {
    const messages: ChatMessage[] = [
      {
        id: "user-1",
        type: "user",
        content: "Inspect the workspace",
      },
      {
        id: "trace-reasoning",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Scanning the workspace layout." }],
            isStreaming: true,
          },
          {
            kind: "task",
            title: "Inspecting files",
            status: "in_progress",
            items: [{ id: "task-1", text: "Opening workspace files" }],
          },
        ],
      },
    ];

    const items = buildChatDisplayItems(messages, {
      showPendingAssistantShell: true,
    });

    expect(items).toHaveLength(2);
    expect(items[1]?.kind).toBe("assistant_turn");
    if (items[1]?.kind === "assistant_turn") {
      expect(items[1].isPendingShell).toBe(true);
      expect(items[1].turnId).toBe(buildPendingAssistantTurnId("user-1"));
      expect(items[1].reasoningItems).toHaveLength(1);
      expect(items[1].attachedTraceParts).toHaveLength(1);
      expect(items[1].attachedTraceParts[0]?.part.kind).toBe("task");
    }
  });

  it("attaches live execution traces to an existing assistant turn instead of leaving standalone rows", () => {
    const messages: ChatMessage[] = [
      {
        id: "user-1",
        type: "user",
        content: "Continue",
      },
      {
        id: "assistant-live",
        type: "assistant",
        content: "",
        streaming: true,
      },
      {
        id: "trace-task",
        type: "trace",
        content: "task",
        traceSource: "live",
        renderParts: [
          {
            kind: "task",
            title: "Inspecting file tree",
            status: "in_progress",
            items: [{ id: "task-1", text: "Reading files" }],
          },
        ],
      },
    ];

    const items = buildChatDisplayItems(messages);

    expect(items).toHaveLength(2);
    expect(items[1]?.kind).toBe("assistant_turn");
    if (items[1]?.kind === "assistant_turn") {
      expect(items[1].message?.id).toBe("assistant-live");
      expect(items[1].attachedTraceParts).toHaveLength(1);
      expect(items[1].attachedTraceParts[0]?.part.kind).toBe("task");
    }
  });
});
