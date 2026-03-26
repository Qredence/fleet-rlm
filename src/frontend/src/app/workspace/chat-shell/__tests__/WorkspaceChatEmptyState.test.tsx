import { act, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { WorkspaceChatEmptyState } from "@/app/workspace/chat-shell/WorkspaceChatEmptyState";

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

  it("renders feature-local suggestions and the shell-navigation hint", () => {
    const html = renderToStaticMarkup(
      <WorkspaceChatEmptyState isMobile={false} onSuggestionClick={() => {}} />,
    );
    expect(html).toContain("What can I help you build?");
    expect(html).toContain("Architecture pass");
    expect(html).toContain("Document brief");
    expect(html).toContain("Python runner");
    expect(html).toContain("Critique my work");
    expect(html).not.toContain("Operator workspace");
    expect(html).toContain("Use the left rail to jump between recent sessions");
  });

  it("routes suggestion interactions through feature callbacks", () => {
    const onSuggestionClick = vi.fn();
    const { container, root } = mountEmptyState({ onSuggestionClick });

    const architectureButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Architecture pass"),
    );

    expect(architectureButton).not.toBeUndefined();

    act(() => {
      architectureButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onSuggestionClick).toHaveBeenCalledWith(
      "Analyze a codebase and extract its architecture",
    );

    act(() => {
      root.unmount();
    });
  });
});
