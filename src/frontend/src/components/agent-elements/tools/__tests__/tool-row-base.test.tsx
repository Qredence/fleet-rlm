import { act } from "react";
import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { ToolGroup } from "@/components/agent-elements/tools/tool-group";
import { ToolRowBase } from "@/components/agent-elements/tools/tool-row-base";

vi.mock("lottie-react", () => ({
  default: () => <div data-testid="lottie-animation" />,
}));

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Root[] = [];

function render(ui: ReactNode) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedRoots.push(root);

  act(() => {
    root.render(ui);
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

describe("ToolRowBase", () => {
  it("uses SpiralLoader as the default pending icon", () => {
    const container = render(
      <ToolRowBase shimmerLabel="Running tool" completeLabel="Tool" isAnimating={true} />,
    );

    const loader = container.querySelector('[data-agent-elements-loader="spiral"]');
    expect(loader).not.toBeNull();
    expect(loader?.getAttribute("aria-hidden")).toBe("true");
    expect(container.textContent).toContain("Running tool");
  });

  it("does not render a loader for completed rows without an icon", () => {
    const container = render(
      <ToolRowBase shimmerLabel="Running tool" completeLabel="Tool" isAnimating={false} />,
    );

    expect(container.querySelector('[data-agent-elements-loader="spiral"]')).toBeNull();
    expect(container.textContent).toContain("Tool");
  });

  it("keeps explicit pending icons instead of replacing them with SpiralLoader", () => {
    const container = render(
      <ToolRowBase
        icon={<span data-testid="explicit-icon" />}
        shimmerLabel="Running tool"
        completeLabel="Tool"
        isAnimating={true}
      />,
    );

    expect(container.querySelector('[data-agent-elements-loader="spiral"]')).toBeNull();
    expect(container.querySelector('[data-testid="explicit-icon"]')).not.toBeNull();
  });
});

describe("ToolGroup pending row", () => {
  it("keeps the Running activity label and uses the default SpiralLoader", () => {
    const container = render(
      <ToolGroup
        part={{
          type: "tool-Group",
          toolCallId: "activity-1",
          state: "input-streaming",
          input: { description: "Runtime activity" },
        }}
        nestedTools={[
          {
            type: "tool-Status",
            toolCallId: "status-1",
            state: "input-streaming",
            input: { message: "Preparing workspace" },
          },
        ]}
        completeLabel="Execution activity"
        shimmerLabel="Running activity"
        interruptedLabel="Activity interrupted"
        chatStatus="streaming"
      />,
    );

    expect(container.textContent).toContain("Running activity");
    expect(container.querySelector('[data-agent-elements-loader="spiral"]')).not.toBeNull();
  });

  it("keeps completed grouped activity complete while the assistant turn is streaming", () => {
    const container = render(
      <ToolGroup
        part={{
          type: "tool-Group",
          toolCallId: "activity-2",
          state: "output-available",
          input: { description: "Runtime activity" },
        }}
        nestedTools={[
          {
            type: "tool-Status",
            toolCallId: "status-2",
            state: "output-available",
            input: { message: "Prepared workspace" },
            output: { message: "Prepared workspace" },
          },
        ]}
        completeLabel="Execution activity"
        shimmerLabel="Running activity"
        interruptedLabel="Activity interrupted"
        chatStatus="streaming"
      />,
    );

    expect(container.textContent).toContain("Execution activity");
    expect(container.textContent).not.toContain("Running activity");
    expect(container.querySelector('[data-agent-elements-loader="spiral"]')).toBeNull();
  });
});
