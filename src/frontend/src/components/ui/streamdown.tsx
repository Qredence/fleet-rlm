/**
 * Streamdown — Streaming Markdown Renderer
 *
 * A progressive markdown renderer for AI chat interfaces.
 * Uses a lightweight inline parser (zero external deps), styled entirely
 * with CSS variables from /src/styles/theme.css.
 *
 * Features:
 *   - Typewriter streaming effect with configurable speed
 *   - Blinking cursor indicator while streaming
 *   - Bold, italic, inline code, code blocks, headings, lists, blockquotes
 *   - All typography via CSS variables (no Tailwind text-* classes)
 *   - Dark mode automatic via token references
 *   - prefers-reduced-motion: skips to full content instantly
 */

import {
  useState,
  useEffect,
  useRef,
  useMemo,
  type CSSProperties,
  type ReactNode,
} from "react";
import { cn } from "@/components/ui/utils";

// ── Types ───────────────────────────────────────────────────────────
interface StreamdownProps {
  /** The full markdown content to render (or stream progressively) */
  content: string;
  /** Whether to animate the text streaming in */
  streaming?: boolean;
  /** Characters revealed per tick (default: 3) */
  speed?: number;
  /** Tick interval in ms (default: 16 — ~60fps) */
  interval?: number;
  /** Called when streaming completes */
  onComplete?: () => void;
  /** Additional className for the wrapper */
  className?: string;
}

// ── Shared inline styles (CSS variable references) ──────────────────
const typoStyles = {
  base: {
    fontFamily: "var(--font-family)",
    fontSize: "var(--text-label)",
    fontWeight: "var(--font-weight-regular)",
    lineHeight: "1.6",
    color: "var(--foreground)",
  } as CSSProperties,
  heading: (level: 1 | 2 | 3 | 4): CSSProperties => {
    const map: Record<number, CSSProperties> = {
      1: {
        fontSize: "var(--text-h2)",
        fontWeight: "var(--font-weight-semibold)",
        lineHeight: "1.3",
        marginTop: "1.25em",
        marginBottom: "0.5em",
      },
      2: {
        fontSize: "var(--text-h3)",
        fontWeight: "var(--font-weight-semibold)",
        lineHeight: "1.3",
        marginTop: "1em",
        marginBottom: "0.4em",
      },
      3: {
        fontSize: "var(--text-h4)",
        fontWeight: "var(--font-weight-semibold)",
        lineHeight: "1.4",
        marginTop: "0.75em",
        marginBottom: "0.3em",
      },
      4: {
        fontSize: "var(--text-label)",
        fontWeight: "var(--font-weight-medium)",
        lineHeight: "1.4",
        marginTop: "0.5em",
        marginBottom: "0.25em",
      },
    };
    return {
      ...map[level],
      fontFamily: "var(--font-family)",
      color: "var(--foreground)",
    };
  },
  paragraph: {
    fontFamily: "var(--font-family)",
    fontSize: "var(--text-label)",
    fontWeight: "var(--font-weight-regular)",
    lineHeight: "1.6",
    color: "var(--foreground)",
    marginBottom: "0.5em",
  } as CSSProperties,
  strong: {
    fontWeight: "var(--font-weight-medium)",
    color: "var(--foreground)",
  } as CSSProperties,
  inlineCode: {
    fontFamily: "var(--font-family-mono)",
    fontSize: "var(--text-caption)",
    fontWeight: "var(--font-weight-regular)",
    lineHeight: "1.5",
    backgroundColor: "var(--muted)",
    color: "var(--accent)",
    padding: "2px 6px",
    borderRadius: "var(--radius-sm)",
  } as CSSProperties,
  codeBlock: {
    fontFamily: "var(--font-family-mono)",
    fontSize: "var(--text-caption)",
    fontWeight: "var(--font-weight-regular)",
    lineHeight: "1.5",
    backgroundColor: "var(--muted)",
    color: "var(--foreground)",
    padding: "12px 16px",
    borderRadius: "var(--radius)",
    border: "1px solid var(--border-subtle)",
    overflowX: "auto" as const,
    marginTop: "0.5em",
    marginBottom: "0.75em",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  } as CSSProperties,
  listItem: {
    fontFamily: "var(--font-family)",
    fontSize: "var(--text-label)",
    fontWeight: "var(--font-weight-regular)",
    lineHeight: "1.6",
    color: "var(--muted-foreground)",
    marginBottom: "0.15em",
  } as CSSProperties,
  blockquote: {
    borderLeft: "3px solid var(--border)",
    paddingLeft: "12px",
    color: "var(--muted-foreground)",
    fontStyle: "italic" as const,
    margin: "0.5em 0",
  } as CSSProperties,
  hr: {
    border: "none",
    borderTop: "1px solid var(--border-subtle)",
    margin: "1em 0",
  } as CSSProperties,
};

// ── Inline text parser ──────────────────────────────────────────────
// Parses **bold**, *italic*, `code`, and plain text within a single line.
function parseInline(text: string, lineKey: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Regex: bold (**text**), italic (*text*), inline code (`text`)
  const regex = /(\*\*(.+?)\*\*|\*(.+?)\*|`([^`]+)`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let partIndex = 0;

  while ((match = regex.exec(text)) !== null) {
    // Text before the match
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    if (match[2]) {
      // Bold: **text**
      nodes.push(
        <strong key={`${lineKey}-b${partIndex}`} style={typoStyles.strong}>
          {match[2]}
        </strong>,
      );
    } else if (match[3]) {
      // Italic: *text*
      nodes.push(
        <em key={`${lineKey}-i${partIndex}`} style={{ fontStyle: "italic" }}>
          {match[3]}
        </em>,
      );
    } else if (match[4]) {
      // Inline code: `text`
      nodes.push(
        <code key={`${lineKey}-c${partIndex}`} style={typoStyles.inlineCode}>
          {match[4]}
        </code>,
      );
    }

    lastIndex = match.index + match[0].length;
    partIndex++;
  }

  // Remaining text
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes.length > 0 ? nodes : [text];
}

// ── Block-level parser ──────────────────────────────────────────────
function parseMarkdown(content: string): ReactNode[] {
  const lines = content.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i] ?? "";

    // Fenced code block
    if (line.startsWith("```")) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !(lines[i] ?? "").startsWith("```")) {
        const codeLine = lines[i];
        if (codeLine == null) break;
        codeLines.push(codeLine);
        i++;
      }
      i++; // skip closing ```
      blocks.push(
        <pre key={`block-${blocks.length}`} style={typoStyles.codeBlock}>
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // Horizontal rule
    if (/^---+$/.test(line.trim()) || /^\*\*\*+$/.test(line.trim())) {
      blocks.push(<hr key={`block-${blocks.length}`} style={typoStyles.hr} />);
      i++;
      continue;
    }

    // Headings
    const headingMatch = line.match(/^(#{1,4})\s+(.+)/);
    if (headingMatch) {
      const hashes = headingMatch[1];
      const headingText = headingMatch[2];
      if (!hashes || !headingText) {
        i++;
        continue;
      }
      const level = hashes.length as 1 | 2 | 3 | 4;
      const Tag = `h${level}` as const;
      blocks.push(
        <Tag key={`block-${blocks.length}`} style={typoStyles.heading(level)}>
          {parseInline(headingText, `h-${blocks.length}`)}
        </Tag>,
      );
      i++;
      continue;
    }

    // Blockquote
    if (line.startsWith("> ")) {
      const quoteLines: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith("> ")) {
        const quoteLine = lines[i];
        if (quoteLine == null) break;
        quoteLines.push(quoteLine.slice(2));
        i++;
      }
      blocks.push(
        <blockquote
          key={`block-${blocks.length}`}
          style={typoStyles.blockquote}
        >
          {quoteLines.map((ql, qi) => (
            <p key={qi} style={typoStyles.paragraph}>
              {parseInline(ql, `bq-${blocks.length}-${qi}`)}
            </p>
          ))}
        </blockquote>,
      );
      continue;
    }

    // Unordered list (-, *, •)
    if (/^[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*•]\s+/.test(lines[i] ?? "")) {
        const itemLine = lines[i];
        if (itemLine == null) break;
        items.push(itemLine.replace(/^[-*•]\s+/, ""));
        i++;
      }
      blocks.push(
        <ul
          key={`block-${blocks.length}`}
          className="ml-4 mb-2 space-y-0.5 list-disc"
        >
          {items.map((item, ii) => (
            <li key={ii} style={typoStyles.listItem}>
              {parseInline(item, `li-${blocks.length}-${ii}`)}
            </li>
          ))}
        </ul>,
      );
      continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i] ?? "")) {
        const itemLine = lines[i];
        if (itemLine == null) break;
        items.push(itemLine.replace(/^\d+\.\s+/, ""));
        i++;
      }
      blocks.push(
        <ol
          key={`block-${blocks.length}`}
          className="ml-4 mb-2 space-y-0.5 list-decimal"
        >
          {items.map((item, ii) => (
            <li key={ii} style={typoStyles.listItem}>
              {parseInline(item, `oli-${blocks.length}-${ii}`)}
            </li>
          ))}
        </ol>,
      );
      continue;
    }

    // Empty line → spacer
    if (!line.trim()) {
      blocks.push(<div key={`block-${blocks.length}`} className="h-1" />);
      i++;
      continue;
    }

    // Default: paragraph
    blocks.push(
      <p key={`block-${blocks.length}`} style={typoStyles.paragraph}>
        {parseInline(line, `p-${blocks.length}`)}
      </p>,
    );
    i++;
  }

  return blocks;
}

// ── Streaming hook ──────────────────────────────────────────────────
function useStreamText(
  content: string,
  streaming: boolean,
  speed: number,
  interval: number,
  onComplete?: () => void,
) {
  const [displayed, setDisplayed] = useState(streaming ? "" : content);
  const [isStreaming, setIsStreaming] = useState(streaming);
  const indexRef = useRef(0);
  const completeCalledRef = useRef(false);

  // Check reduced motion preference
  const prefersReduced = useRef(
    typeof window !== "undefined"
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : false,
  );

  // Reset when content changes
  useEffect(() => {
    if (streaming && !prefersReduced.current) {
      indexRef.current = 0;
      completeCalledRef.current = false;
      setDisplayed("");
      setIsStreaming(true);
    } else {
      setDisplayed(content);
      setIsStreaming(false);
      if (!completeCalledRef.current) {
        completeCalledRef.current = true;
        onComplete?.();
      }
    }
  }, [content, streaming, onComplete]);

  // Streaming tick
  useEffect(() => {
    if (!isStreaming) return;

    const timer = setInterval(() => {
      indexRef.current = Math.min(indexRef.current + speed, content.length);
      setDisplayed(content.slice(0, indexRef.current));

      if (indexRef.current >= content.length) {
        clearInterval(timer);
        setIsStreaming(false);
        if (!completeCalledRef.current) {
          completeCalledRef.current = true;
          onComplete?.();
        }
      }
    }, interval);

    return () => clearInterval(timer);
  }, [isStreaming, content, speed, interval, onComplete]);

  return { displayed, isStreaming };
}

// ── Blinking cursor ─────────────────────────────────────────────────
function StreamCursor() {
  return (
    <span
      className="inline-block w-[2px] h-[1em] bg-accent ml-0.5 align-text-bottom"
      style={{
        animation: "streamdown-blink 1s step-end infinite",
      }}
      aria-hidden="true"
    />
  );
}

// ── Main component ──────────────────────────────────────────────────
function Streamdown({
  content,
  streaming = false,
  speed = 3,
  interval = 16,
  onComplete,
  className,
}: StreamdownProps) {
  const { displayed, isStreaming } = useStreamText(
    content,
    streaming,
    speed,
    interval,
    onComplete,
  );

  const parsed = useMemo(() => parseMarkdown(displayed), [displayed]);

  return (
    <div className={cn("streamdown-root", className)} style={typoStyles.base}>
      {parsed}
      {isStreaming && <StreamCursor />}
    </div>
  );
}

export { Streamdown };
export type { StreamdownProps };
