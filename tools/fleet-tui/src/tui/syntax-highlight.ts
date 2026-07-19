import hljs from "highlight.js/lib/core.js";
import bash from "highlight.js/lib/languages/bash.js";
import css from "highlight.js/lib/languages/css.js";
import go from "highlight.js/lib/languages/go.js";
import javascript from "highlight.js/lib/languages/javascript.js";
import json from "highlight.js/lib/languages/json.js";
import markdown from "highlight.js/lib/languages/markdown.js";
import python from "highlight.js/lib/languages/python.js";
import rust from "highlight.js/lib/languages/rust.js";
import sql from "highlight.js/lib/languages/sql.js";
import typescript from "highlight.js/lib/languages/typescript.js";
import xml from "highlight.js/lib/languages/xml.js";
import yaml from "highlight.js/lib/languages/yaml.js";

import type { ThemeColor } from "./theme.js";

type SyntaxTheme = {
  fg(color: ThemeColor, text: string): string;
};

for (const [name, language] of Object.entries({
  bash,
  css,
  go,
  javascript,
  json,
  markdown,
  python,
  rust,
  sql,
  typescript,
  xml,
  yaml,
})) {
  hljs.registerLanguage(name, language);
}
hljs.registerAliases(["sh", "shell"], { languageName: "bash" });
hljs.registerAliases(["js", "jsx"], { languageName: "javascript" });
hljs.registerAliases(["ts", "tsx"], { languageName: "typescript" });
hljs.registerAliases(["html"], { languageName: "xml" });
hljs.registerAliases(["md"], { languageName: "markdown" });

const scopeColors: Record<string, ThemeColor> = {
  comment: "syntaxComment",
  quote: "syntaxComment",
  keyword: "syntaxKeyword",
  meta: "syntaxKeyword",
  "selector-tag": "syntaxKeyword",
  title: "syntaxFunction",
  function: "syntaxFunction",
  variable: "syntaxVariable",
  attr: "syntaxVariable",
  attribute: "syntaxVariable",
  property: "syntaxVariable",
  string: "syntaxString",
  regexp: "syntaxString",
  addition: "syntaxString",
  number: "syntaxNumber",
  literal: "syntaxNumber",
  bullet: "syntaxNumber",
  type: "syntaxType",
  built_in: "syntaxType",
  class: "syntaxType",
  operator: "syntaxOperator",
  punctuation: "syntaxPunctuation",
};

export function highlightCode(
  code: string,
  language: string | undefined,
  theme: SyntaxTheme,
): string[] {
  if (!language || !hljs.getLanguage(language)) {
    return code.split("\n").map((line) => theme.fg("mdCodeBlock", line));
  }
  let html: string;
  try {
    html = hljs.highlight(code, { language, ignoreIllegals: true }).value;
  } catch {
    return code.split("\n").map((line) => theme.fg("mdCodeBlock", line));
  }
  return renderHighlightedHtml(html, theme).split("\n");
}

function renderHighlightedHtml(html: string, theme: SyntaxTheme): string {
  const scopes: Array<string | undefined> = [];
  let output = "";
  let buffer = "";
  const flush = () => {
    if (!buffer) return;
    const color = [...scopes].reverse().map(scopeColor).find(Boolean) ?? "mdCodeBlock";
    output += theme.fg(color, decodeHtml(buffer));
    buffer = "";
  };

  for (let index = 0; index < html.length; ) {
    if (html.startsWith("<span", index)) {
      const end = html.indexOf(">", index + 5);
      if (end !== -1) {
        flush();
        const classes = /class=["']([^"']*)["']/.exec(html.slice(index, end + 1))?.[1];
        scopes.push(
          classes
            ?.split(/\s+/)
            .find((value) => value.startsWith("hljs-"))
            ?.slice(5),
        );
        index = end + 1;
        continue;
      }
    }
    if (html.startsWith("</span>", index)) {
      flush();
      scopes.pop();
      index += 7;
      continue;
    }
    buffer += html[index] ?? "";
    index += 1;
  }
  flush();
  return output;
}

function scopeColor(scope: string | undefined): ThemeColor | undefined {
  if (!scope) return undefined;
  return scopeColors[scope] ?? scopeColors[scope.split(/[.-]/, 1)[0] ?? ""];
}

function decodeHtml(value: string): string {
  return value.replaceAll(/&(?:#(\d+)|#x([\da-f]+)|amp|lt|gt|quot|apos);/gi, (entity, dec, hex) => {
    if (dec) return String.fromCodePoint(Number.parseInt(dec, 10));
    if (hex) return String.fromCodePoint(Number.parseInt(hex, 16));
    return (
      { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'" }[
        entity.toLowerCase()
      ] ?? entity
    );
  });
}
