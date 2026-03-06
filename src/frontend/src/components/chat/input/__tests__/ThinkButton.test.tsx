import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ThinkButton } from "@/components/chat/input/ThinkButton";

describe("ThinkButton", () => {
  it("uses high-contrast accent styles when enabled", () => {
    const html = renderToStaticMarkup(
      <ThinkButton enabled onToggle={vi.fn()} />,
    );

    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain("text-accent bg-accent/15 hover:bg-accent/20");
  });

  it("uses muted styles when disabled", () => {
    const html = renderToStaticMarkup(
      <ThinkButton enabled={false} onToggle={vi.fn()} />,
    );

    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain("text-muted-foreground");
  });
});
