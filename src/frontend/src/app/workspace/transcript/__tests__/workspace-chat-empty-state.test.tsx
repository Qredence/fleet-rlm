import { act, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { WorkspaceChatEmptyState } from "@/app/workspace/transcript/workspace-chat-empty-state";

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

  it("renders the workspace title block and prompt chips from the Figma empty state", () => {
    const html = renderToStaticMarkup(
      <WorkspaceChatEmptyState isMobile={false} onSuggestionClick={() => {}} />,
    );
    expect(html).toContain("Let&#x27;s get to work, how can I help?");
    expect(html).toContain("Start with a task or jump into a saved session");
    expect(html).toContain("Help me write");
    expect(html).toContain("Summarize text");
    expect(html).toContain("Analyze image");
    expect(html).toContain("More");
  });

  it("routes suggestion interactions through feature callbacks", () => {
    const onSuggestionClick = vi.fn();
    const { container, root } = mountEmptyState({ onSuggestionClick });

    const helpMeWriteButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Help me write"),
    );

    expect(helpMeWriteButton).not.toBeUndefined();

    act(() => {
      helpMeWriteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onSuggestionClick).toHaveBeenCalledWith("Help me write");

    act(() => {
      root.unmount();
    });
  });
});
