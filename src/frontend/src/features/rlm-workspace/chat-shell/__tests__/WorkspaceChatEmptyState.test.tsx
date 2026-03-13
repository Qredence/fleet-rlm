import { act, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkspaceChatEmptyState } from "@/features/rlm-workspace/chat-shell/WorkspaceChatEmptyState";

function mountEmptyState(
  props?: Partial<ComponentProps<typeof WorkspaceChatEmptyState>>,
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      <WorkspaceChatEmptyState
        isMobile={false}
        onSuggestionClick={() => {}}
        showHistory={false}
        hasHistory={false}
        {...props}
      />,
    );
  });

  return { container, root };
}

describe("WorkspaceChatEmptyState", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders feature-local suggestions and the history affordance", () => {
    const html = renderToStaticMarkup(
      <WorkspaceChatEmptyState
        isMobile={false}
        onSuggestionClick={() => {}}
        showHistory={false}
        hasHistory={true}
      />,
    );

    expect(html).toContain("Agentic Fleet Session");
    expect(html).toContain("Architecture pass");
    expect(html).toContain("Document brief");
    expect(html).toContain("Python runner");
    expect(html).toContain("Critique my work");
    expect(html).toContain("View recent conversations");
  });

  it("routes suggestion and history interactions through feature callbacks", () => {
    const onSuggestionClick = vi.fn();
    const onToggleHistory = vi.fn();
    const { container, root } = mountEmptyState({
      onSuggestionClick,
      onToggleHistory,
      hasHistory: true,
    });

    const architectureButton = Array.from(
      container.querySelectorAll("button"),
    ).find((button) => button.textContent?.includes("Architecture pass"));
    const historyButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("View recent conversations"),
    );

    expect(architectureButton).not.toBeUndefined();
    expect(historyButton).not.toBeUndefined();

    act(() => {
      architectureButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      historyButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onSuggestionClick).toHaveBeenCalledWith(
      "Analyze a codebase and extract its architecture",
    );
    expect(onToggleHistory).toHaveBeenCalledTimes(1);

    act(() => {
      root.unmount();
    });
  });
});
