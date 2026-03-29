import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

describe("ToggleGroup", () => {
  it("supports the shared card variant for option-card layouts", () => {
    const html = renderToStaticMarkup(
      <ToggleGroup type="single" value="dark" variant="card" className="flex-col">
        <ToggleGroupItem value="light">Light</ToggleGroupItem>
        <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
      </ToggleGroup>,
    );

    expect(html).toContain('data-variant="card"');
    expect(html).toContain("flex-col");
    expect(html).toContain("rounded-xl");
  });
});
