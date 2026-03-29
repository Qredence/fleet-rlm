import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vite-plus/test";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("PopoverContent", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("keeps the popover positioner above sticky composer layers", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <Popover open>
          <PopoverTrigger asChild>
            <button type="button">Trigger</button>
          </PopoverTrigger>
          <PopoverContent forceMount>Runtime modes</PopoverContent>
        </Popover>,
      );
    });

    const dialog = document.querySelector('[role="dialog"]');

    expect(dialog).not.toBeNull();
    expect(dialog?.parentElement).not.toBeNull();
    expect(dialog?.parentElement?.className).toContain("z-50");

    act(() => {
      root.unmount();
    });
  });
});
