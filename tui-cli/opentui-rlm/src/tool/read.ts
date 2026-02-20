import { z } from "zod";
import { File } from "../file";
import { Tool, truncateOutput } from "./tool";

const ReadParamsSchema = z.object({
  path: z.string().describe("File path to read"),
  offset: z.number().default(0).describe("Line offset (0-indexed)"),
  limit: z.number().default(2000).describe("Maximum lines to read"),
});

export const ReadTool = Tool.define("read", {
  name: "Read",
  description: "Read file contents with line numbers",
  parameters: ReadParamsSchema,
  execute: async (args, context) => {
    context.setMetadata("tool", "read");
    context.setMetadata("path", args.path);

    const content = await File.read(args.path, {
      offset: args.offset,
      limit: args.limit,
    });

    if (content.isBinary) {
      return {
        title: `Read: ${args.path}`,
        metadata: {
          path: args.path,
          binary: true,
          mimeType: content.mimeType,
        },
        output: content.encoding === "base64" ? `[Binary image data: ${content.mimeType}]` : "[Binary file]",
      };
    }

    const lines = content.content.split("\n");
    const numbered = lines
      .map((line, i) => {
        const lineNum = (content.startLine ?? 0) + i + 1;
        return `${lineNum.toString().padStart(4, " ")}: ${line}`;
      })
      .join("\n");

    const header = content.truncated
      ? `Lines ${content.startLine ?? 0}-${(content.startLine ?? 0) + lines.length} of ${content.lineCount}\n\n`
      : "";

    return {
      title: `Read: ${args.path}`,
      metadata: {
        path: args.path,
        lines: lines.length,
        totalLines: content.lineCount,
        truncated: content.truncated,
      },
      output: truncateOutput(header + numbered),
    };
  },
});
