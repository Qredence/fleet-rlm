import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { ExecutionModeDropdown } from "@/components/chat/input/ExecutionModeDropdown";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("ExecutionModeDropdown", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders the current mode label", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <ExecutionModeDropdown value="tools_only" onChange={() => {}} />,
      );
    });

    expect(container.textContent).toContain("Tools only");
    expect(container.textContent).not.toContain("Execution mode");
    expect(container.textContent).not.toContain(
      "Use normal tools only and skip RLM delegation helpers.",
    );

    act(() => {
      root.unmount();
    });
  });

  it("notifies when a different execution mode is selected", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onChange = vi.fn();

    act(() => {
      root.render(<ExecutionModeDropdown value="auto" onChange={onChange} />);
    });

    const trigger = container.querySelector(
      'button[aria-label="Execution mode: Auto"]',
    );

    act(() => {
      trigger?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const rlmOnlyOption = Array.from(
      document.querySelectorAll('[role="menuitemradio"]'),
    ).find((item) => item.textContent?.includes("RLM only") ?? false);

    act(() => {
      rlmOnlyOption?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledWith("rlm_only");

    act(() => {
      root.unmount();
    });
  });
});
