import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { ClarificationCard } from "@/features/workspace/conversation/clarification-card";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Root[] = [];

function renderClarificationCard(
  data: NonNullable<ChatMessage["clarificationData"]>,
  onResolve = vi.fn(),
) {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const root = createRoot(container);
  mountedRoots.push(root);

  act(() => {
    root.render(<ClarificationCard data={data} onResolve={onResolve} />);
  });

  return { container, onResolve };
}

afterEach(() => {
  while (mountedRoots.length > 0) {
    const root = mountedRoots.pop();
    if (root) {
      act(() => {
        root.unmount();
      });
    }
  }

  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("ClarificationCard", () => {
  it("reveals a labeled textarea for custom answers and resolves trimmed text", () => {
    const onResolve = vi.fn();
    const { container } = renderClarificationCard(
      {
        question: "What should the assistant focus on?",
        stepLabel: "Question 1 of 1",
        customOptionId: "custom",
        options: [
          { id: "docs", label: "Project docs" },
          { id: "custom", label: "Write your own" },
        ],
      },
      onResolve,
    );
    const customOption = container.querySelector('button[aria-label="Write your own"]');
    const confirmButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Confirm"),
    );

    expect(container.querySelector("textarea")).toBeNull();

    act(() => {
      customOption?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const textarea = container.querySelector("textarea");
    expect(textarea).toBeInstanceOf(HTMLTextAreaElement);
    expect(confirmButton?.hasAttribute("disabled")).toBe(true);

    const textareaId = textarea?.getAttribute("id");
    expect(textareaId).toBeTruthy();

    const label = textareaId ? container.querySelector(`label[for="${textareaId}"]`) : null;
    expect(label).not.toBeNull();
    expect(label?.className).toContain("sr-only");

    act(() => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;

      setValue?.call(textarea, "  Focus on the sandbox runner  ");
      textarea?.dispatchEvent(new Event("input", { bubbles: true }));
      textarea?.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(confirmButton?.hasAttribute("disabled")).toBe(false);

    act(() => {
      confirmButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onResolve).toHaveBeenCalledWith("Focus on the sandbox runner");
  });
});
