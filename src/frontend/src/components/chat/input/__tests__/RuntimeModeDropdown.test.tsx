import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { RuntimeModeDropdown } from "@/components/chat/input/RuntimeModeDropdown";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/ui/menubar", () => ({
  Menubar: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarRadioGroup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarRadioItem: ({
    children,
    onSelect,
  }: {
    children: ReactNode;
    onSelect?: () => void;
    showIndicator?: boolean;
  }) => <button onClick={onSelect}>{children}</button>,
}));

describe("RuntimeModeDropdown", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders the current runtime label", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<RuntimeModeDropdown value="daytona_pilot" onChange={() => {}} />);
    });

    expect(container.textContent).toContain("Daytona pilot");

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

    const daytonaOption = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Daytona pilot") ?? false,
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
