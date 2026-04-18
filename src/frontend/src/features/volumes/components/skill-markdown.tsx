import type { CSSProperties, ReactNode } from "react";
import { useMemo } from "react";
import { Separator } from "@/components/ui/separator";

const BOLD_STYLE = {
  fontWeight: "var(--font-weight-semibold)",
} as CSSProperties;

const RELAXED_LINE_HEIGHT_STYLE = {
  lineHeight: "var(--line-height-relaxed)",
} as CSSProperties;

const MARKDOWN_HEADING_STYLES: Record<number, { className: string }> = {
  1: { className: "mb-4 mt-6 text-foreground typo-h2" },
  2: { className: "mb-3 mt-5 text-foreground typo-h3" },
  3: { className: "mb-2 mt-4 text-foreground typo-h4 font-semibold" },
};

function safeHref(href: string): string | undefined {
  try {
    const parsed = new URL(href);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.toString();
    }
    return undefined;
  } catch {
    return undefined;
  }
}

function parseInline(text: string): ReactNode[] {
  const result: ReactNode[] = [];
  const inlineRegex =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*]+\*)|(_[^_]+_)|(\[([^\]]+)\]\(((?:[^()\\]|\\.|\([^()]*\))*)\))/g;

  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = inlineRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      result.push(text.slice(lastIndex, match.index));
    }

    if (match[1]) {
      const code = match[1].slice(1, -1);
      result.push(
        <code
          key={match.index}
          className="rounded bg-muted px-1.5 py-0.5 text-foreground typo-mono"
        >
          {code}
        </code>,
      );
    } else if (match[2]) {
      result.push(
        <strong key={match.index} className="text-foreground" style={BOLD_STYLE}>
          {match[2].slice(2, -2)}
        </strong>,
      );
    } else if (match[3]) {
      result.push(
        <strong key={match.index} className="text-foreground" style={BOLD_STYLE}>
          {match[3].slice(2, -2)}
        </strong>,
      );
    } else if (match[4]) {
      result.push(<em key={match.index}>{match[4].slice(1, -1)}</em>);
    } else if (match[5]) {
      result.push(<em key={match.index}>{match[5].slice(1, -1)}</em>);
    } else if (match[6]) {
      const href = safeHref(match[8] ?? "");
      if (href) {
        result.push(
          <a
            key={match.index}
            href={href}
            className="cursor-pointer text-accent hover:underline"
            target="_blank"
            rel="noopener noreferrer nofollow"
          >
            {match[7]}
          </a>,
        );
      } else {
        result.push(match[7] ?? "");
      }
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    result.push(text.slice(lastIndex));
  }

  return result;
}

interface Block {
  type: "heading" | "paragraph" | "code" | "blockquote" | "ul" | "ol" | "hr";
  level?: number;
  content: string;
  items?: string[];
  lang?: string;
}

function parseBlocks(md: string): Block[] {
  const lines = md.split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i] ?? "";

    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !(lines[i] ?? "").startsWith("```")) {
        const codeLine = lines[i];
        if (codeLine == null) break;
        codeLines.push(codeLine);
        i++;
      }
      blocks.push({ type: "code", content: codeLines.join("\n"), lang });
      i++;
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push({ type: "hr", content: "" });
      i++;
      continue;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      const hashes = headingMatch[1];
      const headingContent = headingMatch[2];
      if (!hashes || !headingContent) {
        i++;
        continue;
      }
      blocks.push({
        type: "heading",
        level: hashes.length,
        content: headingContent,
      });
      i++;
      continue;
    }

    if (line.startsWith("> ")) {
      const quoteLines: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith("> ")) {
        const quoteLine = lines[i];
        if (quoteLine == null) break;
        quoteLines.push(quoteLine.slice(2));
        i++;
      }
      blocks.push({ type: "blockquote", content: quoteLines.join("\n") });
      continue;
    }

    if (/^[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*+]\s+/.test(lines[i] ?? "")) {
        const itemLine = lines[i];
        if (itemLine == null) break;
        items.push(itemLine.replace(/^[-*+]\s+/, ""));
        i++;
      }
      blocks.push({ type: "ul", content: "", items });
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i] ?? "")) {
        const itemLine = lines[i];
        if (itemLine == null) break;
        items.push(itemLine.replace(/^\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ type: "ol", content: "", items });
      continue;
    }

    if (line.trim() === "") {
      i++;
      continue;
    }

    const paraLines: string[] = [];
    while (
      i < lines.length &&
      (lines[i] ?? "").trim() !== "" &&
      !(lines[i] ?? "").startsWith("#") &&
      !(lines[i] ?? "").startsWith("```") &&
      !(lines[i] ?? "").startsWith("> ") &&
      !/^[-*+]\s+/.test(lines[i] ?? "") &&
      !/^\d+\.\s+/.test(lines[i] ?? "") &&
      !/^(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i] ?? "")
    ) {
      const paraLine = lines[i];
      if (paraLine == null) break;
      paraLines.push(paraLine);
      i++;
    }
    if (paraLines.length > 0) {
      blocks.push({ type: "paragraph", content: paraLines.join(" ") });
    }
  }

  return blocks;
}

function renderBlock(block: Block, index: number): ReactNode {
  switch (block.type) {
    case "heading": {
      const cfg = MARKDOWN_HEADING_STYLES[block.level ?? 1] ?? MARKDOWN_HEADING_STYLES[3]!;
      const Tag = `h${block.level ?? 1}` as "h1" | "h2" | "h3";
      return (
        <Tag key={index} className={cfg.className}>
          {parseInline(block.content)}
        </Tag>
      );
    }
    case "paragraph":
      return (
        <p key={index} className="mb-3 text-muted-foreground typo-label-regular">
          {parseInline(block.content)}
        </p>
      );
    case "code":
      return (
        <pre key={index} className="mb-4 overflow-x-auto">
          <code className="block overflow-x-auto rounded-lg bg-muted p-4 text-foreground typo-mono">
            {block.content}
          </code>
        </pre>
      );
    case "blockquote":
      return (
        <blockquote
          key={index}
          className="my-4 border-l-4 border-accent pl-4 italic text-muted-foreground typo-label-regular"
        >
          {parseInline(block.content)}
        </blockquote>
      );
    case "ul":
      return (
        <ul
          key={index}
          className="mb-3 ml-5 flex list-disc flex-col gap-1.5 text-muted-foreground typo-label-regular"
        >
          {block.items?.map((item, itemIndex) => (
            <li key={itemIndex} style={RELAXED_LINE_HEIGHT_STYLE}>
              {parseInline(item)}
            </li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol
          key={index}
          className="mb-3 ml-5 flex list-decimal flex-col gap-1.5 text-muted-foreground typo-label-regular"
        >
          {block.items?.map((item, itemIndex) => (
            <li key={itemIndex} style={RELAXED_LINE_HEIGHT_STYLE}>
              {parseInline(item)}
            </li>
          ))}
        </ol>
      );
    case "hr":
      return <Separator key={index} className="my-4" />;
    default:
      return null;
  }
}

interface SkillMarkdownProps {
  content: string;
}

export function SkillMarkdown({ content }: SkillMarkdownProps) {
  const blocks = useMemo(() => parseBlocks(content), [content]);
  return <div>{blocks.map((block, index) => renderBlock(block, index))}</div>;
}
