/**
 * Simple markdown parser for terminal output.
 * Converts markdown to OpenTUI-compatible styled elements.
 */

type MarkdownSegment =
  | { type: "text"; content: string }
  | { type: "code"; content: string }
  | { type: "link"; content: string; url: string };

function parseInlineMarkdown(text: string): MarkdownSegment[] {
  const segments: MarkdownSegment[] = [];
  let remaining = text;

  while (remaining.length > 0) {
    // Inline code (`...`)
    const inlineCodeMatch = remaining.match(/^`([^`]+)`/);
    if (inlineCodeMatch && inlineCodeMatch[1] !== undefined) {
      segments.push({ type: "code", content: inlineCodeMatch[1] });
      remaining = remaining.slice(inlineCodeMatch[0].length);
      continue;
    }

    // Link [...](...)
    const linkMatch = remaining.match(/^\[([^\]]+)\]\(([^)]+)\)/);
    if (linkMatch && linkMatch[1] !== undefined && linkMatch[2] !== undefined) {
      segments.push({ type: "link", content: linkMatch[1], url: linkMatch[2] });
      remaining = remaining.slice(linkMatch[0].length);
      continue;
    }

    // Regular text - find next special character
    const nextSpecial = remaining.search(/[`\[\]]/);
    if (nextSpecial === -1) {
      segments.push({ type: "text", content: remaining });
      break;
    } else if (nextSpecial === 0) {
      segments.push({ type: "text", content: remaining[0] ?? "" });
      remaining = remaining.slice(1);
    } else {
      segments.push({ type: "text", content: remaining.slice(0, nextSpecial) });
      remaining = remaining.slice(nextSpecial);
    }
  }

  return segments;
}

export interface MarkdownProps {
  content: string;
  baseColor?: string;
  accentColor?: string;
  codeBg?: string;
}

export function parseMarkdown({ content, baseColor = "#cccccc", accentColor = "#7aa2f7", codeBg = "#1c1c1c" }: MarkdownProps) {
  // Check for fenced code blocks first
  const codeBlockMatch = content.match(/^```(\w*)\n?([\s\S]*?)```$/m);
  if (codeBlockMatch) {
    const language = codeBlockMatch[1] || "text";
    const code = codeBlockMatch[2]?.trim() || "";

    // If entire content is a code block, return just the code
    const isOnlyCodeBlock = content.trim().startsWith("```") && content.trim().endsWith("```");
    if (isOnlyCodeBlock) {
      return [
        <box key="code-block" backgroundColor={codeBg} padding={1} marginTop={1} marginBottom={1} flexDirection="column">
          <text fg={accentColor}><strong>{language || "code"}</strong></text>
          <text fg={baseColor}>{code}</text>
        </box>
      ];
    }
  }

  // Otherwise parse as regular markdown
  const lines = content.split(/\n/);
  const elements: ReturnType<typeof renderLine>[] = [];
  let inCodeBlock = false;
  let codeBlockLines: string[] = [];
  let codeLanguage = "";
  let lineIndex = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line) continue;

    // Check for code block start
    if (line.startsWith("```")) {
      if (!inCodeBlock) {
        // Start of code block
        inCodeBlock = true;
        codeLanguage = line.slice(3).trim() || "text";
        codeBlockLines = [];
        continue;
      } else {
        // End of code block - render it
        const code = codeBlockLines.join("\n");
        elements.push(
          <box key={`code-${lineIndex++}`} backgroundColor={codeBg} padding={1} marginTop={1} marginBottom={1} flexDirection="column">
            <text fg={accentColor}><strong>{codeLanguage || "code"}</strong></text>
            <text fg={baseColor}>{code}</text>
          </box>
        );
        inCodeBlock = false;
        codeBlockLines = [];
        codeLanguage = "";
        continue;
      }
    }

    if (inCodeBlock) {
      codeBlockLines.push(line);
      continue;
    }

    // Skip empty lines at boundaries
    if (!line && elements.length === 0) continue;

    const rendered = renderLine(line, baseColor, accentColor, lineIndex++);
    if (rendered) {
      elements.push(rendered);
    }
  }

  // Handle unclosed code block
  if (inCodeBlock && codeBlockLines.length > 0) {
    const code = codeBlockLines.join("\n");
    elements.push(
      <box key={`code-${lineIndex++}`} backgroundColor={codeBg} padding={1} marginTop={1} marginBottom={1} flexDirection="column">
        <text fg={accentColor}><strong>{codeLanguage || "code"}</strong></text>
        <text fg={baseColor}>{code}</text>
      </box>
    );
  }

  return elements;
}

function renderLine(line: string, baseColor: string, accentColor: string, key: number) {
  // Check for headings
  const headingMatch = line.match(/^(#{1,6})\s+(.*)/);
  if (headingMatch) {
    const text = headingMatch[2] || "";
    return <text key={key} fg={accentColor}>{text}</text>;
  }

  // Check for list items
  const listMatch = line.match(/^(\s*)[-*+]\s+(.*)/);
  if (listMatch) {
    const indent = listMatch[1]?.length || 0;
    const text = listMatch[2] || "";
    return (
      <text key={key}>
        {" ".repeat(indent)}
        <span fg={accentColor}>• </span>
        {renderInline(line, baseColor, accentColor)}
      </text>
    );
  }

  // Check for numbered list
  const numberedMatch = line.match(/^(\s*)(\d+)\.\s+(.*)/);
  if (numberedMatch) {
    const indent = numberedMatch[1]?.length || 0;
    const num = numberedMatch[2] || "1";
    const text = numberedMatch[3] || "";
    return (
      <text key={key}>
        {" ".repeat(indent)}
        <span fg={accentColor}>{num}. </span>
        {renderInline(text, baseColor, accentColor)}
      </text>
    );
  }

  // Check for blockquote
  if (line.startsWith("> ")) {
    const text = line.slice(2);
    return (
      <text key={key} fg="#888888">
        {" │ "}{text}
      </text>
    );
  }

  // Check for horizontal rule
  if (line.match(/^[-*_]{3,}$/)) {
    return <text key={key} fg="#333333">{"─".repeat(40)}</text>;
  }

  // Regular line with inline markdown
  return <text key={key}>{renderInline(line, baseColor, accentColor)}</text>;
}

function renderInline(text: string, baseColor: string, accentColor: string) {
  const segments = parseInlineMarkdown(text);

  return segments.map((seg, i) => {
    if (seg.type === "code") {
      return <span key={i} fg={accentColor} bg="#1c1c1c">{` ${seg.content} `}</span>;
    }
    if (seg.type === "link") {
      return <a key={i} href={seg.url} fg={accentColor}>{seg.content}</a>;
    }
    return <span key={i} fg={baseColor}>{seg.content}</span>;
  });
}

export function hasMarkdown(text: string): boolean {
  return /[*_`#\-\[)\]]/.test(text) || text.includes("```");
}
