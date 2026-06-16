function unescapePythonString(s: string): string {
  return s.replace(/\\(.)/g, (_, char) => {
    switch (char) {
      case "n":
        return "\n";
      case "r":
        return "\r";
      case "t":
        return "\t";
      case "b":
        return "\b";
      case "f":
        return "\f";
      case "\\":
        return "\\";
      case "'":
        return "'";
      case '"':
        return '"';
      default:
        return char;
    }
  });
}

export function parseRawSnippet(val: string): string {
  if (!val) return "";
  const strVal = val.trim();

  // If it's a JSON string, try to parse it
  try {
    const parsed = JSON.parse(strVal);
    if (parsed && typeof parsed === "object") {
      if (typeof parsed.code === "string") return parsed.code;
      if (typeof parsed.command === "string") return parsed.command;
      if (typeof parsed.stdout === "string") return parsed.stdout;
    }
  } catch {
    // Ignore JSON parse error and proceed
  }

  // If it looks like a Python dictionary string representation (e.g. starts with { and ends with })
  if (strVal.startsWith("{") && strVal.endsWith("}")) {
    const match = strVal.match(/['"]code['"]\s*:\s*(['"])((?:\\.|[^\\])*?)\1/s);
    if (match && match[2]) {
      return unescapePythonString(match[2]);
    }
    const matchCmd = strVal.match(/['"]command['"]\s*:\s*(['"])((?:\\.|[^\\])*?)\1/s);
    if (matchCmd && matchCmd[2]) {
      return unescapePythonString(matchCmd[2]);
    }
  }

  // Check if it's wrapped in repl_execute call formatting like `Calling tool: repl_execute(...)` or `repl_execute(...)`
  const replExecuteMatch = strVal.match(/^(?:Calling tool:\s*)?repl_execute\((.*)\)$/s);
  if (replExecuteMatch && replExecuteMatch[1]) {
    const inner = replExecuteMatch[1].trim();
    // Try to parse the inner dict
    if (inner.startsWith("{") && inner.endsWith("}")) {
      const match = inner.match(/['"]code['"]\s*:\s*(['"])((?:\\.|[^\\])*?)\1/s);
      if (match && match[2]) {
        return unescapePythonString(match[2]);
      }
    }
  }

  return val;
}

export function dedent(str: string): string {
  const lines = str.split("\n");
  let minIndent: number | null = null;
  for (const line of lines) {
    if (!line.trim()) continue;
    const match = line.match(/^(\s*)/);
    if (match) {
      const indent = match[1] ? match[1].length : 0;
      if (minIndent === null || indent < minIndent) {
        minIndent = indent;
      }
    }
  }
  if (minIndent && minIndent > 0) {
    return lines.map((line) => line.slice(minIndent!)).join("\n");
  }
  return str;
}

export function shouldRepairSerializedPython(code: string): boolean {
  const trimmed = code.trim();
  if (trimmed.includes("\n")) return false;
  if (trimmed.length < 90) return false;
  return /\b(import|from|print\(|def|class|for|if)\b/.test(trimmed) && /\b\w+\s=/.test(trimmed);
}

export function formatSerializedPythonSnippet(code: string): string {
  if (!shouldRepairSerializedPython(code)) return code;

  const lines = code
    .replace(/\s+#\s+/g, "\n# ")
    .replace(/\s+(?=(?:from\s+\w+\s+import|import\s+\w+)\b)/g, "\n")
    .replace(/\s+(?=(?:def|class|for|while|if|elif|else|try|except|finally|with)\b)/g, "\n")
    .replace(/\s+(?=(?:print|return)\s*\()/g, "\n")
    .replace(/\s+(?=[A-Za-z_]\w*\s=\s)/g, "\n")
    .split("\n")
    .map((line) => line.trimEnd())
    .filter(Boolean);

  let blockRemainder = 0;
  return lines
    .map((line, index) => {
      const trimmed = line.trim();
      const formatted = blockRemainder > 0 ? `  ${trimmed}` : trimmed;
      if (blockRemainder > 0) blockRemainder -= 1;
      if (trimmed.endsWith(":")) {
        blockRemainder = lines[index + 1]?.trim()?.startsWith("#") ? 2 : 1;
      }
      return formatted;
    })
    .join("\n");
}

export function cleanAndFormatSnippet(rawCode: string): string {
  let code = parseRawSnippet(rawCode);

  // 1. Remove literal HTML line breaks like <br> (convert to real newlines)
  code = code.replace(/<br\s*\/?>/gi, "\n");

  // 2. Replace smart quotes with plain quotes
  code = code.replace(/[“”]/g, '"').replace(/[‘’]/g, "'");

  // 3. Normalize indentation without changing semantics
  code = dedent(code);

  // 4. Repair common single-line REPL payloads before highlighting.
  code = formatSerializedPythonSnippet(code);

  // 5. Escape nested triple backticks safely
  code = code.replace(/```/g, "\\`\\`\\`");

  return code;
}

export function extractCommandSummary(cmd: string): string {
  const cleaned = cleanAndFormatSnippet(cmd);
  return cleaned
    .split("|")
    .map((s) => s.trim().split(/\s+/)[0] ?? "")
    .filter(Boolean)
    .slice(0, 4)
    .join(", ");
}

export function summarizeField(value: string, fallback?: string): string {
  const normalized = cleanAndFormatSnippet(value).replace(/\s+/g, " ").trim();
  if (fallback?.trim()) return fallback.trim();
  if (!normalized) return "";
  return normalized.length > 72 ? `${normalized.slice(0, 69)}...` : normalized;
}

export function inferCodeLanguage(command: string, explicitLanguage?: string): string {
  const explicit = explicitLanguage?.trim().toLowerCase();
  if (explicit && explicit !== "text" && explicit !== "plain") return explicit;

  const trimmed = command.trim();
  if (/^(import|from|def|class|print\(|async\s+def|if\s+__name__\s*==)/.test(trimmed)) {
    return "python";
  }
  if (/^(const|let|var|import\s+\{|export|function|async\s+function)/.test(trimmed)) {
    return "typescript";
  }
  if (/^(SELECT|WITH|INSERT|UPDATE|DELETE)\b/i.test(trimmed)) {
    return "sql";
  }
  if (/^\s*[{[]/.test(trimmed)) {
    return "json";
  }
  return "bash";
}
