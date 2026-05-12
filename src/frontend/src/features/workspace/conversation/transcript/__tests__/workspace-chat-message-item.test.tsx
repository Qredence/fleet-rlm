import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vite-plus/test";
import { WorkspaceChatMessageItem } from "@/features/workspace/conversation/transcript/workspace-chat-message-item";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("WorkspaceChatMessageItem", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("streams assistant markdown while message is still in progress", () => {
    const message: ChatMessage = {
      id: "assistant-1",
      type: "assistant",
      content: "Hello **world**",
      streaming: true,
      phase: 1,
    };

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <WorkspaceChatMessageItem
          message={message}
          onResolveHitl={() => {}}
          onResolveClarification={() => {}}
        />,
      );
    });

    expect(container.textContent).toContain("Hello world");

    act(() => {
      root.unmount();
    });
  });
});
