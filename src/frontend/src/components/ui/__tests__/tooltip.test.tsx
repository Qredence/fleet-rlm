import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

describe("TooltipContent", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("uses the shared popover tooltip styling", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <Tooltip open>
          <TooltipTrigger asChild>
            <button type="button">Trigger</button>
          </TooltipTrigger>
          <TooltipContent forceMount>Shared tooltip</TooltipContent>
        </Tooltip>,
      );
    });

    const tooltip = document.querySelector('[data-slot="tooltip-content"]');

    expect(tooltip).not.toBeNull();
    expect(tooltip?.className).toContain("bg-popover");
    expect(tooltip?.className).toContain("text-popover-foreground");
    expect(tooltip?.className).toContain("border-border-subtle/80");

    act(() => {
      root.unmount();
    });
  });
});
