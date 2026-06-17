import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { Input } from "@/components/ui/input";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";

describe("Input primitives", () => {
  it("uses the shared subtle border styling for standalone inputs", () => {
    const html = renderToStaticMarkup(<Input aria-label="Example input" />);

    expect(html).toContain('data-slot="input"');
    expect(html).toContain("selection:bg-primary");
    expect(html).toContain("border-input");
    expect(html).toContain("bg-transparent");
    expect(html).toContain("focus-visible:ring-focus");
  });

  it("uses grouped field styling for composed input actions", () => {
    const html = renderToStaticMarkup(
      <InputGroup>
        <InputGroupInput aria-label="Secret input" />
        <InputGroupAddon align="inline-end">
          <InputGroupButton type="button" variant="outline">
            Clear saved value
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>,
    );

    expect(html).toContain('data-slot="input-group"');
    expect(html).toContain("border-input");
    expect(html).toContain("has-[[data-slot=input-group-control]:focus-visible]:border-ring");
    expect(html).toContain('data-slot="input-group-control"');
    expect(html).toContain("has-[&gt;[data-align=inline-end]]:[&amp;&gt;input]:pr-2");
  });
});
