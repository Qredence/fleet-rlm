import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WorkspaceChatMessageItem } from "@/features/workspace/conversation/transcript/workspace-chat-message-item";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

describe("WorkspaceChatMessageItem", () => {
  it("streams assistant markdown while message is still in progress", () => {
    const message: ChatMessage = {
      id: "assistant-1",
      type: "assistant",
      content: "Hello **world**",
      streaming: true,
      phase: 1,
    };

    render(
      <WorkspaceChatMessageItem
        message={message}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
      />,
    );

    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });
});
