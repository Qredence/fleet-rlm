const INLINE_CODE_AFTER_HEADING =
  /\b(print\s*\(|import\s+[a-zA-Z_]|from\s+[a-zA-Z_]\w*\s+import|def\s+[a-zA-Z_]|class\s+[a-zA-Z_]|context\s*\[|(?:pnpm|npm|git|curl|cd|ls|cat|bash|sh)\s+)/;

// Gate heading splits to code-like syntax so prose such as "class action" is left alone.
const INLINE_CODE_SYNTAX =
  /(?:\bprint\s*\(|\bimport\s+[a-zA-Z_][\w.]*(?:\s+as\s+[a-zA-Z_]\w*)?(?:\s*,\s*[a-zA-Z_][\w.]*)*|\bfrom\s+[a-zA-Z_][\w.]*\s+import\b|\bdef\s+[a-zA-Z_]\w*\s*\(|\bclass\s+[a-zA-Z_]\w*(?:\([^)]*\))?:|\bcontext\s*\[[^\]]+\]|^(?:pnpm|npm|git|curl|cd|ls|cat|bash|sh)\s+[-./~\w])/;

function looksLikeInlineCode(code: string): boolean {
  return INLINE_CODE_SYNTAX.test(code.trim());
}

function inferFenceLanguage(code: string): string {
  if (/\b(print|import|def|class|context\[)\b/.test(code)) return "python";
  if (/\b(curl|git|npm|pnpm|cd|ls|cat|bash|sh)\b/.test(code)) return "bash";
  return "text";
}

/**
 * ATX headings (`# title`) only span the current line. Model output often appends
 * executable code on the same line, which Streamdown then renders as one giant h1.
 */
export function splitHeadingLinesWithInlineCode(text: string): string {
  return text
    .split("\n")
    .map((line) => {
      const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
      if (!headingMatch) return line;

      const [, markers, body] = headingMatch;
      if (!body) return line;

      const codeIndex = body.search(INLINE_CODE_AFTER_HEADING);
      if (codeIndex === -1) return line;

      const headingText = body.slice(0, codeIndex).trimEnd();
      const codeText = body.slice(codeIndex).trim();
      if (!codeText || !looksLikeInlineCode(codeText)) return line;
      if (!headingText) {
        return `\`\`\`${inferFenceLanguage(codeText)}\n${codeText}\n\`\`\``;
      }

      return `${markers} ${headingText}\n\n\`\`\`${inferFenceLanguage(codeText)}\n${codeText}\n\`\`\``;
    })
    .join("\n");
}

export function normalizeMarkdownContent(text: string): string {
  return splitHeadingLinesWithInlineCode(text);
}
