import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { useActionButtons } from "../use-action-buttons";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("useActionButtons", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("releases execution lock when onBeforeAction throws", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    const onAction = vi.fn();
    const onBeforeAction = vi.fn().mockRejectedValue(new Error("boom"));

    const runActionRef = {
      current: null as ((id: string) => Promise<void>) | null,
    };

    function TestHarness() {
      const { runAction } = useActionButtons({
        actions: [{ id: "a1", label: "Action 1" }],
        onAction,
        onBeforeAction,
      });
      runActionRef.current = runAction;
      return <div />;
    }

    await act(async () => {
      root.render(<TestHarness />);
    });

    // First call: onBeforeAction throws — lock should still be released
    await act(async () => {
      await runActionRef.current!("a1").catch(() => {
        // intentional: swallow the thrown error
      });
    });

    // Second call: should succeed because lock was released
    onBeforeAction.mockResolvedValue(true);
    await act(async () => {
      await runActionRef.current!("a1");
    });

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith("a1");

    act(() => {
      root.unmount();
    });
    document.body.removeChild(container);
  });
});
