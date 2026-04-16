import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { WorkspaceChatMessageItem } from "@/features/workspace/conversation/transcript/workspace-chat-message-item";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

function mountMessage(
  message: ChatMessage,
  options?: {
    onResolveHitl?: (msgId: string, label: string) => void;
    onResolveClarification?: (msgId: string, answer: string) => void;
  },
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      <WorkspaceChatMessageItem
        message={message}
        onResolveHitl={options?.onResolveHitl ?? (() => {})}
        onResolveClarification={options?.onResolveClarification ?? (() => {})}
      />,
    );
  });

  return { container, root };
}

describe("WorkspaceChatMessageItem", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("uses readable tokenized typography for system/status labels", () => {
    const message: ChatMessage = {
      id: "system-1",
      type: "system",
      content: "Backend error: WebSocket connection failed after 5 retries",
    };

    const { container, root } = mountMessage(message);
    const systemLabel = container.querySelector("span");

    expect(systemLabel?.className).toContain("text-xs");
    expect(systemLabel?.className).toContain("font-medium");
    expect(systemLabel?.className).toContain("tracking-wide");
    expect(systemLabel?.className).toContain("text-muted-foreground/70");

    act(() => {
      root.unmount();
    });
  });

  it("forwards HITL confirmation actions without changing message semantics", () => {
    const onResolveHitl = vi.fn();
    const message: ChatMessage = {
      id: "hitl-1",
      type: "hitl",
      content: "Approval required",
      hitlData: {
        question: "Approve the change?",
        actions: [
          { label: "Approve", variant: "primary" },
          { label: "Reject", variant: "secondary" },
        ],
      },
    };

    const { container, root } = mountMessage(message, { onResolveHitl });
    const approveButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Approve"),
    );

    expect(container.textContent).toContain("Approve the change?");
    expect(approveButton).not.toBeUndefined();

    act(() => {
      approveButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onResolveHitl).toHaveBeenCalledWith("hitl-1", "Approve");

    act(() => {
      root.unmount();
    });
  });

  it("forwards clarification answers through the extracted message shell", () => {
    const onResolveClarification = vi.fn();
    const message: ChatMessage = {
      id: "clar-1",
      type: "clarification",
      content: "Need clarification",
      clarificationData: {
        question: "Which file should I inspect first?",
        stepLabel: "Question 1 of 1",
        customOptionId: "custom",
        options: [
          { id: "readme", label: "README.md" },
          { id: "custom", label: "Write your own" },
        ],
      },
    };

    const { container, root } = mountMessage(message, {
      onResolveClarification,
    });
    const radioButton = container.querySelector('button[aria-label="README.md"]');
    const confirmButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Confirm"),
    );

    expect(container.textContent).toContain("Which file should I inspect first?");
    expect(confirmButton?.hasAttribute("disabled")).toBe(true);

    act(() => {
      radioButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(confirmButton?.hasAttribute("disabled")).toBe(false);

    act(() => {
      confirmButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onResolveClarification).toHaveBeenCalledWith("clar-1", "README.md");

    act(() => {
      root.unmount();
    });
  });
});
