import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AttachmentDropdown } from "@/components/chat/input/AttachmentDropdown";

vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuItem: ({
    children,
    onClick,
  }: {
    children: ReactNode;
    onClick?: () => void;
  }) => <button onClick={onClick}>{children}</button>,
}));

describe("AttachmentDropdown", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("surfaces unsupported uploads immediately when binary upload is disabled", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onUnsupportedSelect = vi.fn();

    act(() => {
      root.render(
        <AttachmentDropdown
          uploadsEnabled={false}
          onUnsupportedSelect={onUnsupportedSelect}
        />,
      );
    });

    const menuItem = Array.from(container.querySelectorAll("button")).find(
      (button) =>
        button.textContent?.includes("Add images, PDFs or CSVs") ?? false,
    );

    expect(menuItem?.textContent).toContain("(coming soon)");

    act(() => {
      menuItem?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onUnsupportedSelect).toHaveBeenCalledOnce();

    act(() => {
      root.unmount();
    });
  });
});
