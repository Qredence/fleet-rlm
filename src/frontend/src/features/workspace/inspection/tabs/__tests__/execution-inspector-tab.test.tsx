import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { buildAssistantContentModel } from "@/features/workspace/conversation/assistant-content/model";
import { ExecutionInspectorRow } from "@/features/workspace/inspection/execution-inspector-rows";
import type { AssistantTurnDisplayItem } from "@/lib/workspace/chat-display-items";
import type { ChatMessage, ChatRenderPart } from "@/lib/workspace/workspace-types";

function makeAssistantTurn(renderParts: ChatRenderPart[]): AssistantTurnDisplayItem {
  const message: ChatMessage = {
    id: "assistant-message",
    type: "assistant",
    content: "Done",
    phase: 1,
    renderParts,
  };

  return {
    kind: "assistant_turn",
    key: "assistant-turn",
    turnId: "turn-1",
    message,
    isPendingShell: false,
    reasoningItems: [],
    trajectoryItems: [],
    attachedToolSessions: [],
    attachedTraceParts: [],
  };
}

describe("ExecutionInspectorRow", () => {
  it("renders Agent Elements bash labels instead of ChainOfThought structure", () => {
    const model = buildAssistantContentModel(
      makeAssistantTurn([
        {
          kind: "sandbox",
          title: "python",
          state: "output-available",
          code: "print('hello')",
          output: "hello",
          language: "python",
        },
      ]),
    );

    const section = model.execution.sections[0];
    expect(section).toBeDefined();

    const html = renderToStaticMarkup(
      <ExecutionInspectorRow section={section!} messageId={model.item.turnId} />,
    );

    expect(html).toContain("Ran command");
    expect(html).not.toContain("ChainOfThought");
    expect(html).not.toContain("chain-of-thought");
  });
});
