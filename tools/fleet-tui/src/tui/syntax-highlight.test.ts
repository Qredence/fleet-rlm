import { describe, expect, it } from "vitest";

import { highlightCode } from "./syntax-highlight.js";
import type { ThemeColor } from "./theme.js";

describe("highlightCode", () => {
  it("maps upstream highlight.js scopes to semantic pi tokens and decodes entities", () => {
    const seen: ThemeColor[] = [];
    const lines = highlightCode("const answer = '<fleet>'; // done", "javascript", {
      fg(color, text) {
        seen.push(color);
        return `<${color}>${text}</${color}>`;
      },
    });

    expect(seen).toContain("syntaxKeyword");
    expect(seen).toContain("syntaxString");
    expect(seen).toContain("syntaxComment");
    expect(lines.join("\n")).toContain("'<fleet>'");
  });

  it("falls back safely for an unsupported language", () => {
    const seen: ThemeColor[] = [];
    const lines = highlightCode("a < b && c > d", "not-a-language", {
      fg: (color, text) => {
        seen.push(color);
        return text;
      },
    });

    expect(seen).toEqual(["mdCodeBlock"]);
    expect(lines.join("\n")).toContain("a < b && c > d");
  });

  it("does not auto-detect an unlabeled RLM code event", () => {
    const seen: ThemeColor[] = [];
    highlightCode("const answer = 42", undefined, {
      fg: (color, text) => {
        seen.push(color);
        return text;
      },
    });

    expect(seen).toEqual(["mdCodeBlock"]);
  });
});
