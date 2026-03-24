import { describe, expect, it } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";

import { SkillMarkdown } from "@/screens/volumes/volumes-canvas-panel";

describe("SkillMarkdown link sanitization", () => {
  it("renders absolute https links as clickable anchors", () => {
    const html = renderToStaticMarkup(
      <SkillMarkdown content="[OpenAI](https://openai.com)" />,
    );

    expect(html).toContain('href="https://openai.com/"');
    expect(html).toContain('rel="noopener noreferrer nofollow"');
  });

  it("blocks javascript links from becoming anchors", () => {
    const html = renderToStaticMarkup(
      <SkillMarkdown content="[Danger](javascript:alert(1))" />,
    );

    expect(html).toContain("Danger");
    expect(html).not.toContain("<a");
  });

  it("renders malformed links as readable non-clickable text", () => {
    const html = renderToStaticMarkup(
      <SkillMarkdown content="[Broken](not-a-valid-url)" />,
    );

    expect(html).toContain("Broken");
    expect(html).not.toContain("<a");
  });
});
