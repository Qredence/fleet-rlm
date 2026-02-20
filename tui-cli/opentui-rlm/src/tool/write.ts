import { z } from "zod";
import { File } from "../file";
import { Tool, truncateOutput } from "./tool";

const WriteParamsSchema = z.object({
  path: z.string().describe("File path to write"),
  content: z.string().describe("Content to write"),
  overwrite: z.boolean().default(false).describe("Overwrite existing file"),
});

export const WriteTool = Tool.define("write", {
  name: "Write",
  description: "Write content to a file",
  parameters: WriteParamsSchema,
  execute: async (args, context) => {
    context.setMetadata("tool", "write");
    context.setMetadata("path", args.path);

    const fileExists = await File.exists(args.path);
    let oldContent: string | null = null;

    if (fileExists) {
      if (!args.overwrite) {
        throw new Error(`File already exists: ${args.path}. Use overwrite: true to replace.`);
      }
      const existing = await File.read(args.path);
      if (!existing.isBinary) {
        oldContent = existing.content;
      }
    }

    await Bun.write(args.path, args.content);

    const diff = oldContent
      ? generateDiff(args.path, oldContent, args.content)
      : `Created new file: ${args.path}`;

    return {
      title: `Write: ${args.path}`,
      metadata: {
        path: args.path,
        existed: fileExists,
        size: args.content.length,
        lines: args.content.split("\n").length,
      },
      output: truncateOutput(diff),
    };
  },
});

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
