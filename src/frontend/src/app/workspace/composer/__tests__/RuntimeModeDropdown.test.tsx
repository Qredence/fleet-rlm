import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { RuntimeModeDropdown } from "@/app/workspace/composer/RuntimeModeDropdown";

let onValueChangeRef: ((value: string) => void) | undefined;

vi.mock("@/components/ui/select", () => ({
  Select: ({
    children,
    onValueChange,
  }: {
    children: ReactNode;
    onValueChange?: (value: string) => void;
  }) => {
    onValueChangeRef = onValueChange;
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
  SelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectGroup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children: ReactNode; value: string }) => (
    <button type="button" role="menuitemradio" onClick={() => onValueChangeRef?.(value)}>
      {children}
    </button>
  ),
}));

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("RuntimeModeDropdown", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    onValueChangeRef = undefined;
  });

  it("renders the current runtime label", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<RuntimeModeDropdown value="daytona_pilot" onChange={() => {}} />);
    });

    expect(container.textContent).toContain("Daytona");

    act(() => {
      root.unmount();
    });
  });

  it("notifies when a different runtime mode is selected", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onChange = vi.fn();

    act(() => {
      root.render(<RuntimeModeDropdown value="modal_chat" onChange={onChange} />);
    });

    const trigger = container.querySelector('button[aria-label="Runtime mode: Modal chat"]');

    act(() => {
      trigger?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const daytonaOption = Array.from(document.querySelectorAll('[role="menuitemradio"]')).find(
      (item) => item.textContent?.includes("Daytona") ?? false,
    );

    act(() => {
      daytonaOption?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledWith("daytona_pilot");

    act(() => {
      root.unmount();
    });
  });
});
