import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ChatStatus } from "ai";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { AgentChat } from "@/components/agent-elements/agent-chat";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Root[] = [];
const readyStatus = "ready" as ChatStatus;

function renderCenteredEmptyChat() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedRoots.push(root);

  act(() => {
    root.render(
      <AgentChat
        messages={[]}
        onSend={vi.fn()}
        status={readyStatus}
        onStop={vi.fn()}
        emptyStatePosition="center"
        emptySuggestionsPlacement="empty"
        emptyState={<div data-slot="welcome-content">Start a conversation</div>}
        classNames={{ inputBar: "px-4 pb-6" }}
        suggestions={[{ id: "build", label: "Build a feature", value: "Build a feature" }]}
        slots={{
          InputBar: ({ className }) => (
            <div className={String(className ?? "")} data-slot="input-bar">
              Composer
            </div>
          ),
        }}
      />,
    );
  });

  return container;
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

describe("AgentChat centered empty state", () => {
  it("keeps the welcome content centered without moving the input bar into it", () => {
    const container = renderCenteredEmptyChat();

    const root = container.firstElementChild;
    const centeredContent = container.querySelector(".items-center.justify-center");
    const inputBar = container.querySelector('[data-slot="input-bar"]');

    expect(centeredContent).not.toBeNull();
    expect(centeredContent?.textContent).toContain("Start a conversation");
    expect(centeredContent?.textContent).toContain("Build a feature");
    expect(centeredContent?.contains(inputBar)).toBe(false);
    expect(inputBar?.parentElement).toBe(root);
    expect(inputBar?.className).toContain("px-4 pb-6");
    expect(inputBar?.className).not.toContain("px-0 pb-0");
  });
});
