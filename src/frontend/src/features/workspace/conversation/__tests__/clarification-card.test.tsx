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
  it("selects an option and resolves with its label on Confirm", () => {
    const onResolve = vi.fn();
    const { container } = renderClarificationCard(
      {
        question: "What should the assistant focus on?",
        stepLabel: "Question 1 of 1",
        customOptionId: "",
        options: [
          { id: "docs", label: "Project docs" },
          { id: "sandbox", label: "Sandbox runner" },
        ],
      },
      onResolve,
    );

    const confirmButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Confirm"),
    );

    expect(confirmButton?.hasAttribute("disabled")).toBe(true);

    const docsOption = container.querySelector('button[data-id="docs"]');
    act(() => {
      docsOption?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(confirmButton?.hasAttribute("disabled")).toBe(false);

    act(() => {
      confirmButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onResolve).toHaveBeenCalledWith("Project docs");
  });

  it("shows receipt state when data.resolved is true", () => {
    const { container } = renderClarificationCard({
      question: "Which file first?",
      stepLabel: "File selection",
      customOptionId: "",
      options: [{ id: "readme", label: "README.md" }],
      resolved: true,
      resolvedAnswer: "README.md",
    });

    expect(container.querySelector("[data-receipt='true']")).not.toBeNull();
    expect(container.textContent).toContain("README.md");
  });
});
