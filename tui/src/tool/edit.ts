import { z } from "zod";
import { File } from "../file";
import { Tool, truncateOutput } from "./tool";

const EditParamsSchema = z.object({
  path: z.string().describe("File path to edit"),
  oldString: z.string().describe("Text to find and replace"),
  newString: z.string().describe("Replacement text"),
  replaceAll: z.boolean().default(false).describe("Replace all occurrences"),
});

export const EditTool = Tool.define("edit", {
  name: "Edit",
  description: "Find and replace text in files with cascading strategies",
  parameters: EditParamsSchema,
  execute: async (args, context) => {
    context.setMetadata("tool", "edit");
    context.setMetadata("path", args.path);

    const content = await File.read(args.path, { limit: 100000 });

    if (content.isBinary) {
      throw new Error(`Cannot edit binary file: ${args.path}`);
    }

    let result = attemptEdit(
      content.content,
      args.oldString,
      args.newString,
      args.replaceAll
    );

    if (!result.success) {
      result = attemptWhitespaceNormalized(
        content.content,
        args.oldString,
        args.newString,
        args.replaceAll
      );
    }

    if (!result.success) {
      result = attemptIndentationFlexible(
        content.content,
        args.oldString,
        args.newString,
        args.replaceAll
      );
    }

    if (!result.success) {
      result = attemptMultiOccurrence(
        content.content,
        args.oldString,
        args.newString,
        args.replaceAll
      );
    }

    if (!result.success) {
      throw new Error(`Could not find text to replace in ${args.path}`);
    }

    await Bun.write(args.path, result.content);

    const diff = generateDiff(args.path, content.content, result.content);

    return {
      title: `Edit: ${args.path}`,
      metadata: {
        path: args.path,
        strategy: result.strategy,
        replacements: result.replacements,
      },
      output: truncateOutput(diff),
    };
  },
});

interface EditResult {
  success: boolean;
  content: string;
  strategy: string;
  replacements: number;
}

function attemptEdit(
  content: string,
  oldStr: string,
  newStr: string,
  replaceAll: boolean
): EditResult {
  if (replaceAll) {
    const count = (content.match(new RegExp(escapeRegex(oldStr), "g")) || []).length;
    if (count === 0) return { success: false, content, strategy: "simple", replacements: 0 };
    return {
      success: true,
      content: content.split(oldStr).join(newStr),
      strategy: "simple",
      replacements: count,
    };
  }

  const index = content.indexOf(oldStr);
  if (index === -1) return { success: false, content, strategy: "simple", replacements: 0 };
  return {
    success: true,
    content: content.slice(0, index) + newStr + content.slice(index + oldStr.length),
    strategy: "simple",
    replacements: 1,
  };
}

function attemptWhitespaceNormalized(
  content: string,
  oldStr: string,
  newStr: string,
  replaceAll: boolean
): EditResult {
  const normalizedOld = normalizeWhitespace(oldStr);
  const lines = content.split("\n");
  let matches = 0;

  for (let i = 0; i < lines.length; i++) {
    if (normalizeWhitespace(lines[i]!) === normalizedOld) {
      matches++;
      lines[i] = newStr;
      if (!replaceAll) break;
    }
  }

  if (matches === 0) return { success: false, content, strategy: "whitespace", replacements: 0 };
  return {
    success: true,
    content: lines.join("\n"),
    strategy: "whitespace",
    replacements: matches,
  };
}

function attemptIndentationFlexible(
  content: string,
  oldStr: string,
  newStr: string,
  replaceAll: boolean
): EditResult {
  const oldLines = oldStr.split("\n");
  const contentLines = content.split("\n");
  let matches = 0;
  let result = contentLines.join("\n");

  for (let i = 0; i <= contentLines.length - oldLines.length; i++) {
    const segment = contentLines.slice(i, i + oldLines.length);
    if (matchesWithFlexibleIndentation(segment, oldLines)) {
      matches++;
      const replacement = applyIndentation(newStr, segment[0]!);
      const before = contentLines.slice(0, i).join("\n");
      const after = contentLines.slice(i + oldLines.length).join("\n");
      result = before + (before ? "\n" : "") + replacement + (after ? "\n" : "") + after;
      if (!replaceAll) break;
    }
  }

  if (matches === 0) return { success: false, content, strategy: "indentation", replacements: 0 };
  return {
    success: true,
    content: result,
    strategy: "indentation",
    replacements: matches,
  };
}

function attemptMultiOccurrence(
  content: string,
  oldStr: string,
  newStr: string,
  replaceAll: boolean
): EditResult {
  const occurrences = findOccurrences(content, oldStr);
  if (occurrences.length === 0) return { success: false, content, strategy: "multi", replacements: 0 };

  let result = content;
  const replaceCount = replaceAll ? occurrences.length : 1;

  for (let i = replaceCount - 1; i >= 0; i--) {
    const pos = occurrences[i]!;
    result = result.slice(0, pos) + newStr + result.slice(pos + oldStr.length);
  }

  return {
    success: true,
    content: result,
    strategy: "multi",
    replacements: replaceCount,
  };
}

function normalizeWhitespace(str: string): string {
  return str.replace(/\s+/g, " ").trim();
}

function matchesWithFlexibleIndentation(contentLines: string[], targetLines: string[]): boolean {
  if (contentLines.length !== targetLines.length) return false;

  const baseIndent = getIndentLevel(contentLines[0]!);
  const targetBaseIndent = getIndentLevel(targetLines[0]!);
  const indentDiff = baseIndent - targetBaseIndent;

  for (let i = 0; i < contentLines.length; i++) {
    const contentLine = contentLines[i]!.trim();
    const targetLine = targetLines[i]!.trim();
    if (contentLine !== targetLine) return false;
  }

  return true;
}

function getIndentLevel(line: string): number {
  const match = line.match(/^(\s*)/);
  return match ? match[1]!.length : 0;
}

function applyIndentation(text: string, referenceLine: string): string {
  const indent = referenceLine.match(/^(\s*)/)?.[1] || "";
  return text
    .split("\n")
    .map((line) => (line.trim() ? indent + line : line))
    .join("\n");
}

function findOccurrences(content: string, pattern: string): number[] {
  const positions: number[] = [];
  let pos = 0;
  while ((pos = content.indexOf(pattern, pos)) !== -1) {
    positions.push(pos);
    pos += 1;
  }
  return positions;
}

function generateDiff(path: string, oldContent: string, newContent: string): string {
  const oldLines = oldContent.split("\n");
  const newLines = newContent.split("\n");
  const changes: string[] = [];

  let oldIdx = 0;
  let newIdx = 0;

  while (oldIdx < oldLines.length || newIdx < newLines.length) {
    const oldLine = oldLines[oldIdx];
    const newLine = newLines[newIdx];

    if (oldLine === newLine) {
      oldIdx++;
      newIdx++;
    } else if (newIdx < newLines.length && !oldLines.slice(oldIdx).includes(newLine!)) {
      changes.push(`\x1b[32m+ ${newLine}\x1b[0m`);
      newIdx++;
    } else if (oldIdx < oldLines.length && !newLines.slice(newIdx).includes(oldLine!)) {
      changes.push(`\x1b[31m- ${oldLine}\x1b[0m`);
      oldIdx++;
    } else {
      if (oldLine !== undefined) {
        changes.push(`\x1b[31m- ${oldLine}\x1b[0m`);
      }
      if (newLine !== undefined) {
        changes.push(`\x1b[32m+ ${newLine}\x1b[0m`);
      }
      oldIdx++;
      newIdx++;
    }
  }

  return changes.length > 0 ? changes.join("\n") : "No changes";
}

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
