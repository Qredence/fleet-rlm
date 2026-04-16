import { act, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { WorkspaceChatEmptyState } from "@/features/workspace/conversation/transcript/workspace-chat-empty-state";

function mountEmptyState(props?: Partial<ComponentProps<typeof WorkspaceChatEmptyState>>) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      <WorkspaceChatEmptyState isMobile={false} onSuggestionClick={() => {}} {...props} />,
    );
  });

  return { container, root };
}

describe("WorkspaceChatEmptyState", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders the workspace title block and prompt chips for Daytona-backed execution", () => {
    const html = renderToStaticMarkup(
      <WorkspaceChatEmptyState isMobile={false} onSuggestionClick={() => {}} />,
    );
    // StateNotice header with icon + title + description
    expect(html).toContain("Start a conversation");
    expect(html).toContain("Type a message below");
    expect(html).toContain("begin working with the AI assistant");
    // Updated suggestions aligned with coding/execution tasks
    expect(html).toContain("Build a feature");
    expect(html).toContain("Debug an issue");
    expect(html).toContain("Review changes");
    expect(html).toContain("Explore ideas");
  });

  it("routes suggestion interactions through feature callbacks", () => {
    const onSuggestionClick = vi.fn();
    const { container, root } = mountEmptyState({ onSuggestionClick });

    const buildFeatureButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Build a feature"),
    );

    expect(buildFeatureButton).not.toBeUndefined();

    act(() => {
      buildFeatureButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onSuggestionClick).toHaveBeenCalledWith("Help me build a new feature for my project");

    act(() => {
      root.unmount();
    });
  });

  it("shows mobile-appropriate layout when isMobile is true", () => {
    const html = renderToStaticMarkup(
      <WorkspaceChatEmptyState isMobile={true} onSuggestionClick={() => {}} />,
    );
    // Mobile should still show the core content via StateNotice
    expect(html).toContain("Start a conversation");
    expect(html).toContain("Type a message below");
  });
});
