import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { SettingsDropdown } from "@/components/chat/input/SettingsDropdown";

const navigate = vi.fn();

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({ navigate }),
}));

vi.mock("@/components/ui/menu-item", () => ({
  MenuItem: ({ onSelect }: { children: React.ReactNode; onSelect?: () => void }) => (
    <div role="menuitemradio" onClick={onSelect}>
      Open runtime settings
    </div>
  ),
}));

vi.mock("@/components/ui/dropdown", () => ({
  Dropdown: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: () => null,
}));

vi.mock("@/components/ui/icon-button", () => ({
  IconButton: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
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

    const menuItem = container.querySelector('[role="menuitemradio"]');

    act(() => {
      menuItem?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
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

    const menuItem = container.querySelector('[role="menuitemradio"]');

    act(() => {
      menuItem?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(navigate).toHaveBeenCalledWith({
      to: "/settings",
      search: { section: "runtime" },
    });

    act(() => {
      root.unmount();
    });
  });
});
