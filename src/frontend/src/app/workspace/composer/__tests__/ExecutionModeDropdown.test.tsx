import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { ExecutionModeDropdown } from "@/app/workspace/composer/ExecutionModeDropdown";

let onValueChangeRef: ((value: string) => void) | undefined;
let currentValueRef: string | undefined;

const executionLabelByValue: Record<string, string> = {
  auto: "Auto",
  rlm_only: "RLM only",
  tools_only: "Tools only",
};

vi.mock("@/components/ui/select", () => ({
  Select: ({
    children,
    onValueChange,
    value,
  }: {
    children: ReactNode;
    onValueChange?: (value: string) => void;
    value?: string;
  }) => {
    onValueChangeRef = onValueChange;
    currentValueRef = value;
    return <div>{children}</div>;
  },
  SelectTrigger: ({
    children,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
    children: ReactNode;
  }) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
  SelectContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SelectGroup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectValue: ({
    children,
    placeholder,
  }: {
    children?: ReactNode;
    placeholder?: string;
  }) => (
    <span>
      {children ??
        (currentValueRef
          ? executionLabelByValue[currentValueRef]
          : undefined) ??
        placeholder}
    </span>
  ),
  SelectItem: ({ children, value }: { children: ReactNode; value: string }) => (
    <button
      type="button"
      role="menuitemradio"
      onClick={() => onValueChangeRef?.(value)}
    >
      {children}
    </button>
  ),
}));

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("ExecutionModeDropdown", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    onValueChangeRef = undefined;
    currentValueRef = undefined;
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
