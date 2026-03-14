import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { SettingsDropdown } from "@/components/chat/input/SettingsDropdown";

const navigate = vi.fn();

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({ navigate }),
}));

vi.mock("@/components/ui/menubar", () => ({
  Menubar: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarLabel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  MenubarItem: ({ children, onSelect }: { children: ReactNode; onSelect?: () => void }) => (
    <button onClick={onSelect}>{children}</button>
  ),
}));

describe("SettingsDropdown", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    navigate.mockReset();
  });

  it("dispatches the shared settings event for the runtime section", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const handleOpenSettings = vi.fn((event: Event) => {
      event.preventDefault();
    });

    document.addEventListener("open-settings", handleOpenSettings);

    act(() => {
      root.render(<SettingsDropdown />);
    });

    const button = Array.from(container.querySelectorAll("button")).find(
      (candidate) => candidate.textContent?.includes("Open runtime settings") ?? false,
    );

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(handleOpenSettings).toHaveBeenCalledOnce();
    const [event] = handleOpenSettings.mock.calls[0] ?? [];
    expect((event as CustomEvent<{ section?: string }>).detail?.section).toBe("runtime");
    expect(navigate).not.toHaveBeenCalled();

    document.removeEventListener("open-settings", handleOpenSettings);
    act(() => {
      root.unmount();
    });
  });

  it("falls back to the settings route when no dialog listener handles the event", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<SettingsDropdown />);
    });

    const button = Array.from(container.querySelectorAll("button")).find(
      (candidate) => candidate.textContent?.includes("Open runtime settings") ?? false,
    );

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(navigate).toHaveBeenCalledWith("/settings?section=runtime");

    act(() => {
      root.unmount();
    });
  });
});
