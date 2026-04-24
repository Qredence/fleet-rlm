import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai-elements/reasoning";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Root[] = [];

function renderReasoning({
  isStreaming = false,
  onOpenChange = vi.fn(),
}: {
  isStreaming?: boolean;
  onOpenChange?: (open: boolean) => void;
} = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedRoots.push(root);

  act(() => {
    root.render(
      <Reasoning autoClose={true} isStreaming={isStreaming} onOpenChange={onOpenChange}>
        <ReasoningTrigger />
        <ReasoningContent>Some reasoning content</ReasoningContent>
      </Reasoning>,
    );
  });

  return { container, onOpenChange, root };
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

describe("Reasoning", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("does not auto-close again after manual re-open", () => {
    const onOpenChange = vi.fn();
    const { root } = renderReasoning({ isStreaming: true, onOpenChange });

    // End streaming
    act(() => {
      root.render(
        <Reasoning autoClose={true} isStreaming={false} onOpenChange={onOpenChange}>
          <ReasoningTrigger />
          <ReasoningContent>Some reasoning content</ReasoningContent>
        </Reasoning>,
      );
    });

    // Wait for auto-close
    act(() => {
      vi.advanceTimersByTime(1100);
    });

    // Should have been closed by auto-close
    expect(onOpenChange).toHaveBeenLastCalledWith(false);

    // Manually re-open by simulating a click on trigger
    const trigger = document.querySelector("button");
    act(() => {
      trigger?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Should have been opened
    expect(onOpenChange).toHaveBeenLastCalledWith(true);

    // Clear mock to track any new calls
    onOpenChange.mockClear();

    // Wait again
    act(() => {
      vi.advanceTimersByTime(1100);
    });

    // Should NOT have been called with false again
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("manual close before timer fires prevents auto-close", () => {
    const onOpenChange = vi.fn();
    const { root } = renderReasoning({ isStreaming: true, onOpenChange });

    // End streaming
    act(() => {
      root.render(
        <Reasoning autoClose={true} isStreaming={false} onOpenChange={onOpenChange}>
          <ReasoningTrigger />
          <ReasoningContent>Some reasoning content</ReasoningContent>
        </Reasoning>,
      );
    });

    // Manually close before timer fires
    const trigger = document.querySelector("button");
    act(() => {
      trigger?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Should have been closed manually
    expect(onOpenChange).toHaveBeenLastCalledWith(false);

    // Manually re-open
    act(() => {
      trigger?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // Should have been opened
    expect(onOpenChange).toHaveBeenLastCalledWith(true);

    // Clear mock
    onOpenChange.mockClear();

    // Wait past the auto-close delay
    act(() => {
      vi.advanceTimersByTime(1100);
    });

    // Should NOT have received another false call
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
