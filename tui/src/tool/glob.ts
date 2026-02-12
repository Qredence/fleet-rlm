import { z } from "zod";
import { Ripgrep } from "../file/ripgrep";
import { File } from "../file";
import { Tool, truncateOutput } from "./tool";

const GlobParamsSchema = z.object({
  pattern: z.string().describe("Glob pattern (e.g., '*.ts', 'src/**/*.tsx')"),
  path: z.string().default(".").describe("Directory to search in"),
  exclude: z.array(z.string()).optional().describe("Patterns to exclude"),
});

export const GlobTool = Tool.define("glob", {
  name: "Glob",
  description: "Find files matching glob patterns",
  parameters: GlobParamsSchema,
  execute: async (args, context) => {
    context.setMetadata("tool", "glob");
    context.setMetadata("pattern", args.pattern);

    const files = await Ripgrep.files(args.path, [args.pattern], args.exclude);

    const fileInfos = await Promise.all(
      files.slice(0, 100).map(async (file) => {
        const fullPath = args.path === "." ? file : `${args.path}/${file}`;
        const info = await File.info(fullPath);
        return {
          path: file,
          mtime: info?.mtime ?? 0,
        };
      })
    );

    fileInfos.sort((a, b) => b.mtime - a.mtime);

    const results = fileInfos.map((f) => f.path);

    return {
      title: `Glob: ${args.pattern}`,
      metadata: {
        pattern: args.pattern,
        matches: results.length,
        total: files.length,
      },
      output: truncateOutput(results.join("\n") || "No files found"),
    };
  },
});
