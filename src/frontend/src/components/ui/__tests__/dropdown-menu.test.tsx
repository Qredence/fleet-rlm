import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

describe("DropdownMenuItem", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("composes onClick and onSelect handlers", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onClick = vi.fn();
    const onSelect = vi.fn();

    act(() => {
      root.render(
        <DropdownMenu open>
          <DropdownMenuTrigger>Trigger</DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem onClick={onClick} onSelect={onSelect}>
              Item
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>,
      );
    });

    const item = document.querySelector('[data-slot="dropdown-menu-item"]');
    expect(item).not.toBeNull();

    act(() => {
      item?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onClick).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledTimes(1);

    act(() => {
      root.unmount();
    });
  });
});
