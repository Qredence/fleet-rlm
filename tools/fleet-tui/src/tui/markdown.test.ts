import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./markdown.js";

describe("renderMarkdown", () => {
  it("wraps plain text to the requested width", () => {
    const rendered = renderMarkdown("the quick brown fox jumps over the lazy dog", 12);
    expect(rendered.split("\n").length).toBeGreaterThan(1);
  });

  it("renders fenced code without a line-number gutter", () => {
    const rendered = renderMarkdown("```python\nprint(1)\n```", 60);
    expect(rendered).not.toMatch(/│/);
    expect(rendered).toContain("print");
  });

  it("renders achromatic bold headings", () => {
    const rendered = renderMarkdown("# Hello", 60);
    expect(rendered).toContain("# Hello");
  });

  it("decodes character references without interpreting HTML tags", () => {
    expect(renderMarkdown("The answer is &#39;7&#39; and <b>literal</b>.", 80)).toContain(
      "The answer is '7' and <b>literal</b>.",
    );
  });

  it("emits only achromatic ANSI colors", () => {
    const rendered = renderMarkdown(
      "# Title\n\n- **bold** and `code`\n\n```python\nprint('seven')\n```",
      80,
    );
    const colorPattern = new RegExp(
      `${String.fromCharCode(27)}\\[(3[0-7]|9[0-7]|4[0-7]|10[0-7])m`,
      "g",
    );
    const colorCodes = [...rendered.matchAll(colorPattern)].map((match) => match[1]);
    expect(
      colorCodes.every((code) => ["37", "39", "40", "47", "90", "97"].includes(code ?? "")),
    ).toBe(true);
  });
});
