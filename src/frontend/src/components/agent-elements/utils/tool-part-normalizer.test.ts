import { describe, expect, it } from "vite-plus/test";

import { normalizeAssistantToolParts } from "@/components/agent-elements/utils/tool-part-normalizer";

describe("normalizeAssistantToolParts", () => {
  it("groups consecutive lightweight thinking and tool rows", () => {
    const parts = normalizeAssistantToolParts([
      {
        type: "tool-Thinking",
        toolCallId: "thinking-1",
        state: "output-available",
        input: { thought: "Inspect repository" },
      },
      {
        type: "tool-Tool",
        toolCallId: "tool-1",
        state: "output-available",
        input: { description: "Inspect document headings" },
        toolName: "tool",
      },
      {
        type: "tool-Tool",
        toolCallId: "tool-2",
        state: "output-available",
        input: { description: "Analyze repository content" },
        toolName: "tool",
      },
    ]);

    expect(parts).toHaveLength(1);
    expect(parts[0]).toMatchObject({
      type: "tool-Group",
      nestedTools: [
        expect.objectContaining({ type: "tool-Thinking" }),
        expect.objectContaining({ type: "tool-Tool" }),
        expect.objectContaining({ type: "tool-Tool" }),
      ],
    });
  });
});
