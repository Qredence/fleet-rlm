/**
 * File reference utilities for @-mention parsing and resolution.
 * Supports referencing files/folders in the codebase with @path syntax.
 */

export interface FileReferenceResult {
  path: string;
  content: string;
  type: "file" | "folder" | "error";
  size: number;
  truncated: boolean;
  error?: string;
}

const REPO_ROOT = "/Volumes/Samsung-SSD-T7/Workspaces/Github/qredence/agent-framework/v0.5/_WORLD/_RLM/fleet-rlm-dspy";
const MAX_FILE_SIZE = 50 * 1024; // 50KB
const BINARY_EXTENSIONS = [
  ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
  ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
  ".zip", ".tar", ".gz", ".rar", ".7z",
  ".exe", ".dll", ".so", ".dylib",
  ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv",
  ".ttf", ".otf", ".woff", ".woff2",
];

/**
 * Parse all @-mentions from input text.
 * Matches @path where path is a valid file/folder reference.
 */
export function parseFileReferences(input: string): string[] {
  const matches: string[] = [];
  // Match @ at start of word, followed by path-like characters
  const regex = /@([a-zA-Z0-9_/.\-]+)/g;
  let match;

  while ((match = regex.exec(input)) !== null) {
    // Check it's a standalone @ (not part of email or URL)
    const precedingChar = input[match.index - 1];
    if (!precedingChar || /\s/.test(precedingChar)) {
      matches.push(match[1]!);
    }
  }

  return matches;
}

/**
 * Check if a file extension suggests binary content.
 */
function isBinaryFile(path: string): boolean {
  const ext = path.toLowerCase().split(".").pop() || "";
  return BINARY_EXTENSIONS.some((e) => e.slice(1) === ext);
}

/**
 * Format a file reference result for display in expanded message.
 */
function formatFileReference(result: FileReferenceResult): string {
  const lines: string[] = [];

  // Header
  const icon = result.type === "folder" ? "📁" : "📄";
  const sizeStr = result.size > 1024
    ? `${(result.size / 1024).toFixed(1)}KB`
    : `${result.size}B`;

  lines.push(`──────────────────────────────────────`);
  lines.push(`${icon} ${result.path} (${sizeStr})`);
  lines.push(`──────────────────────────────────────`);

  if (result.error) {
    lines.push(`[ERROR] ${result.error}`);
    return lines.join("\n");
  }

  if (result.type === "folder") {
    lines.push(result.content || "(empty directory)");
  } else if (result.truncated) {
    lines.push(result.content);
    lines.push(`\n[... truncated, ${MAX_FILE_SIZE / 1024}KB limit ...]`);
  } else {
    lines.push(result.content);
  }

  return lines.join("\n");
}

/**
 * Resolve a single file reference path.
 */
export async function resolveFileReference(
  path: string,
  baseDir?: string
): Promise<FileReferenceResult> {
  const resolvedPath = baseDir
    ? `${baseDir}/${path}`.replace(/\/+/g, "/")
    : `${REPO_ROOT}/${path}`.replace(/\/+/g, "/");

  try {
    // Check if path exists using Bun
    const file = Bun.file(resolvedPath);
    const exists = await file.exists();

    if (!exists) {
      return {
        path,
        content: "",
        type: "error",
        size: 0,
        truncated: false,
        error: `File not found: ${path}`,
      };
    }

    // Check if it's a directory by trying to read as text
    const stat = await file.stat();
    if (stat.isDirectory()) {
      // List directory contents using fs module
      try {
        const fs = require('fs');
        const entries = fs.readdirSync(resolvedPath);
        const content = entries.slice(0, 50).join("\n") + (entries.length > 50 ? "\n..." : "");
        return {
          path,
          content,
          type: "folder",
          size: entries.length,
          truncated: entries.length > 50,
        };
      } catch {
        return {
          path,
          content: "(unable to list directory)",
          type: "folder",
          size: 0,
          truncated: false,
        };
      }
    }

    // Check for binary files
    if (isBinaryFile(resolvedPath)) {
      return {
        path,
        content: "",
        type: "file",
        size: stat.size,
        truncated: false,
        error: `[binary file: ${path}] - cannot display binary content`,
      };
    }

    // Read file content
    const content = await file.text();
    const truncated = content.length > MAX_FILE_SIZE;

    return {
      path,
      content: truncated ? content.slice(0, MAX_FILE_SIZE) : content,
      type: "file",
      size: stat.size,
      truncated,
    };
  } catch (error) {
    return {
      path,
      content: "",
      type: "error",
      size: 0,
      truncated: false,
      error: `Error reading ${path}: ${error instanceof Error ? error.message : "Unknown error"}`,
    };
  }
}

/**
 * Expand all @-mentions in input text with file contents.
 */
export async function expandFileReferences(input: string): Promise<string> {
  const references = parseFileReferences(input);

  if (references.length === 0) {
    return input;
  }

  // Build the expanded message
  const parts: string[] = [];
  let lastIndex = 0;

  // Replace @mentions with expanded content
  const regex = /@([a-zA-Z0-9_/.\-]+)/g;
  let match;

  while ((match = regex.exec(input)) !== null) {
    const precedingChar = input[match.index - 1];
    if (precedingChar && !/\s/.test(precedingChar)) {
      continue;
    }

    // Add text before this match
    const textBefore = input.slice(lastIndex, match.index);
    parts.push(textBefore);

    // Resolve and add file content
    const ref = match[1]!;
    const result = await resolveFileReference(ref);
    parts.push(formatFileReference(result));

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  parts.push(input.slice(lastIndex));

  return parts.join("");
}

/**
 * Check if input contains any @-mentions.
 */
export function hasFileReferences(input: string): boolean {
  return parseFileReferences(input).length > 0;
}
