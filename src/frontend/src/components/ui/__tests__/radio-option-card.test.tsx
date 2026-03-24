import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { RadioOptionCard } from "@/components/ui/radio-option-card";

describe("RadioOptionCard", () => {
  it("only animates the selection indicator when motion is safe", () => {
    const html = renderToStaticMarkup(
      <RadioOptionCard
        selected
        onSelect={() => {}}
        label="Automated tests"
        description="Run quality checks automatically"
      />,
    );

    expect(html).toContain("motion-safe:transition-transform");
    expect(html).toContain("motion-safe:duration-150");
  });
});
