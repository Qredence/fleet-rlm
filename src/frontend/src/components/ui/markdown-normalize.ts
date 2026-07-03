const INLINE_CODE_AFTER_HEADING =
  /\b(print|import|from|def|class|if|for|while|return|context\[|await|async)\b/;

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
      const codeIndex = body.search(INLINE_CODE_AFTER_HEADING);
      if (codeIndex === -1) return line;

      const headingText = body.slice(0, codeIndex).trimEnd();
      const codeText = body.slice(codeIndex).trim();
      if (!codeText) return line;
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
