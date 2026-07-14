/** Terminal markdown rendering. Uses `marked` then `cli-highlight` for code blocks. */

import { marked, type Tokens } from "marked";
import { visibleLength, wrapToWidth } from "./format.js";
import { ansi } from "./theme.js";

function renderInline(tokens: Tokens.Generic[]): string {
  return tokens
    .map((token) => {
      switch (token.type) {
        case "strong":
          return `${ansi.bold}${(token as Tokens.Strong).text}${ansi.boldOff}`;
        case "em":
          return `${ansi.italic}${(token as Tokens.Em).text}${ansi.italicOff}`;
        case "codespan": {
          const t = (token as Tokens.Codespan).text;
          return `${ansi.bold}${ansi.white}${t}${ansi.reset}`;
        }
        case "br":
          return "\n";
        case "del":
          return `${ansi.dim}${(token as Tokens.Del).text}${ansi.dimOff}`;
        case "link": {
          const t = token as Tokens.Link;
          return `${ansi.white}${t.text}${ansi.reset} ${ansi.gray}(${t.href})${ansi.reset}`;
        }
        case "image": {
          const t = token as Tokens.Image;
          return `${ansi.gray}![${t.text}](${t.href})${ansi.reset}`;
        }
        case "text":
          return (token as Tokens.Text).text;
        case "escape":
          return (token as Tokens.Escape).text;
        default:
          if ("text" in token) {
            const text = (token as unknown as { text: unknown }).text;
            if (typeof text === "string") return text;
          }
          if ("raw" in token) {
            const raw = (token as unknown as { raw: unknown }).raw;
            if (typeof raw === "string") return raw;
          }
          return "";
      }
    })
    .join("");
}

function renderBlock(token: Tokens.Generic): string[] {
  switch (token.type) {
    case "heading": {
      const t = token as Tokens.Heading;
      const prefix = "#".repeat(Math.min(6, t.depth));
      return [`${ansi.bold}${ansi.white}${prefix} ${renderInline(t.tokens ?? [])}${ansi.reset}`];
    }
    case "paragraph": {
      const t = token as Tokens.Paragraph;
      return [renderInline(t.tokens ?? [])];
    }
    case "blockquote": {
      const t = token as Tokens.Blockquote;
      const inner = renderTokens(t.tokens ?? []).join("\n");
      return inner.split("\n").map((line) => `${ansi.gray}│${ansi.reset} ${line}`);
    }
    case "list": {
      const t = token as Tokens.List;
      return t.items.flatMap((item, index) => {
        const bullet = t.ordered ? `${index + 1}.` : "•";
        const text = renderTokens(item.tokens ?? []).join("\n");
        return text
          .split("\n")
          .map((line, lineIndex) =>
            lineIndex === 0 ? `${ansi.white}${bullet}${ansi.reset} ${line}` : `  ${line}`,
          );
      });
    }
    case "code": {
      const t = token as Tokens.Code;
      return t.text.split("\n");
    }
    case "hr":
      return [`${ansi.gray}${"─".repeat(40)}${ansi.reset}`];
    case "table": {
      const t = token as Tokens.Table;
      const rows = t.rows.map((row) =>
        row.map((cell) => renderInline(cell.tokens ?? [])).join(" │ "),
      );
      const header = t.header
        .map((cell) => `${ansi.bold}${renderInline(cell.tokens ?? [])}${ansi.reset}`)
        .join(" │ ");
      return [
        header,
        `${ansi.gray}${"─".repeat(visibleLength(header) || 12)}${ansi.reset}`,
        ...rows,
      ];
    }
    case "space":
      return [];
    default: {
      const obj = token as unknown as { text?: unknown; tokens?: unknown };
      if (typeof obj.text === "string") return [obj.text];
      if (Array.isArray(obj.tokens)) {
        return renderTokens(obj.tokens as Tokens.Generic[]);
      }
      return [];
    }
  }
}

function renderTokens(tokens: Tokens.Generic[]): string[] {
  const lines: string[] = [];
  for (const token of tokens) {
    lines.push(...renderBlock(token));
  }
  return lines;
}

export function renderMarkdown(input: string, width: number): string {
  if (!input) return "";
  const tokens = marked.lexer(decodeCharacterReferences(input));
  const rendered = renderTokens(tokens as unknown as Tokens.Generic[]).join("\n");
  return decodeCharacterReferences(wrapToWidth(rendered, width).join("\n"));
}

function decodeCharacterReferences(input: string): string {
  const named: Record<string, string> = { amp: "&", apos: "'", gt: ">", lt: "<", quot: '"' };
  return input.replace(/&(?:#(x[0-9a-f]+|\d+)|([a-z]+));/gi, (match, numeric, name) => {
    if (numeric) {
      const hex = String(numeric).toLowerCase().startsWith("x");
      const codePoint = Number.parseInt(
        hex ? String(numeric).slice(1) : String(numeric),
        hex ? 16 : 10,
      );
      return Number.isSafeInteger(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff
        ? String.fromCodePoint(codePoint)
        : match;
    }
    return named[String(name).toLowerCase()] ?? match;
  });
}
