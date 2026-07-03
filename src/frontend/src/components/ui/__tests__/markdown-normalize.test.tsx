import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";
import { Streamdown as StreamdownRenderer } from "streamdown";

import { normalizeMarkdownContent } from "@/components/ui/markdown-normalize";
import { Streamdown } from "@/components/ui/streamdown";

describe("normalizeMarkdownContent", () => {
  it("splits ATX headings that swallow inline code on the same line", () => {
    const input =
      "# First, let's look at the manifest to understand what files are available print(\"Manifest keys:\", context['manifest'].keys())";

    expect(normalizeMarkdownContent(input)).toBe(
      "# First, let's look at the manifest to understand what files are available\n\n```python\nprint(\"Manifest keys:\", context['manifest'].keys())\n```",
    );
  });

  it("leaves well-formed headings unchanged", () => {
    const input = "# Planning step\n\nprint('hello')";
    expect(normalizeMarkdownContent(input)).toBe(input);
  });

  it("infers python fences for context lookups", () => {
    const input = "# Check workspace context['manifest']";
    expect(normalizeMarkdownContent(input)).toBe(
      "# Check workspace\n\n```python\ncontext['manifest']\n```",
    );
  });

  it("does not split headings containing normal English words like for, from, return, if, while", () => {
    const inputs = [
      "# Guidelines for return values",
      "# What if we run the command?",
      "# Planning step for the implementation",
      "# Read files from the current workspace",
      "# Understanding class action requirements",
    ];
    for (const input of inputs) {
      expect(normalizeMarkdownContent(input)).toBe(input);
    }
  });
});

describe("Streamdown wrapper", () => {
  it("renders heading and fenced code instead of one giant h1", () => {
    const content =
      "# First, let's look at the manifest to understand what files are available print(\"Manifest keys:\", context['manifest'].keys())";

    const html = renderToStaticMarkup(<Streamdown content={content} streaming={false} />);

    expect(html).toContain("<h1");
    expect(html).toContain("First, let");
    const headingHtml = html.match(/<h1[^>]*>[\s\S]*?<\/h1>/)?.[0] ?? "";
    expect(headingHtml).toContain("manifest to understand what files are available");
    expect(headingHtml).not.toContain("print");
    expect(html).toContain('data-streamdown="code-block"');
    expect(html).toMatch(/print\(&quot;Manifest keys:&quot;/);
  });

  it("does not wrap plain prose in headings", () => {
    const html = renderToStaticMarkup(
      <Streamdown
        content="First, let's look at the manifest print('Manifest keys')"
        streaming={false}
      />,
    );

    expect(html).not.toContain('data-streamdown="heading-1"');
    expect(html).toContain("<p");
  });
});

describe("raw Streamdown regression", () => {
  it("shows the unnormalized single-line heading bug", () => {
    const content =
      "# First, let's look at the manifest to understand what files are available print(\"Manifest keys:\")";

    const html = renderToStaticMarkup(
      <StreamdownRenderer mode="static">{content}</StreamdownRenderer>,
    );

    expect(html).toContain('data-streamdown="heading-1"');
    expect(html).toContain("print(&quot;Manifest keys:&quot;)");
  });
});
