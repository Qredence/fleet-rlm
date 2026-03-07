import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SettingsDropdown } from "@/components/chat/input/SettingsDropdown";

const navigate = vi.fn();

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({ navigate }),
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

    const button = container.querySelector(
      'button[aria-label="Open runtime settings"]',
    );

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(handleOpenSettings).toHaveBeenCalledOnce();
    const [event] = handleOpenSettings.mock.calls[0] ?? [];
    expect((event as CustomEvent<{ section?: string }>).detail?.section).toBe(
      "runtime",
    );
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

    const button = container.querySelector(
      'button[aria-label="Open runtime settings"]',
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
