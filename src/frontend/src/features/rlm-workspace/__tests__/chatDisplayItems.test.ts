import { describe, expect, it } from "vitest";
import { buildChatDisplayItems } from "@/features/rlm-workspace/chatDisplayItems";
import type { ChatMessage } from "@/lib/data/types";

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
      expect(items[0].reasoningItems[0]?.part.parts[0]?.text).toBe(
        "Thinking through the request.",
      );
    }
  });

  it("keeps reasoning in place when later tool activity interrupts the turn", () => {
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

    expect(items.map((item) => item.kind)).toEqual([
      "assistant_turn",
      "tool_session",
      "assistant_turn",
    ]);

    const [reasoningTurn, toolSession, assistantTurn] = items;

    expect(reasoningTurn?.kind).toBe("assistant_turn");
    if (reasoningTurn?.kind === "assistant_turn") {
      expect(reasoningTurn.reasoningItems).toHaveLength(1);
      expect(reasoningTurn.message).toBeUndefined();
    }

    expect(toolSession?.kind).toBe("tool_session");

    expect(assistantTurn?.kind).toBe("assistant_turn");
    if (assistantTurn?.kind === "assistant_turn") {
      expect(assistantTurn.message?.content).toBe("Done.");
      expect(assistantTurn.reasoningItems).toHaveLength(0);
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
});
