import { z } from "zod";
import { Ripgrep } from "../file/ripgrep";
import { Tool } from "./tool";
import { truncateOutput } from "./tool";

const GrepParamsSchema = z.object({
  pattern: z.string().describe("Search pattern"),
  path: z.string().default(".").describe("Directory to search in"),
  caseSensitive: z.boolean().default(false).describe("Case sensitive search"),
  wholeWord: z.boolean().default(false).describe("Match whole words only"),
  regex: z.boolean().default(true).describe("Treat pattern as regex"),
  include: z.array(z.string()).optional().describe("File patterns to include"),
  exclude: z.array(z.string()).optional().describe("File patterns to exclude"),
});

export const GrepTool = Tool.define("grep", {
  name: "Grep",
  description: "Search for patterns in files using ripgrep",
  parameters: GrepParamsSchema,
  execute: async (args, context) => {
    context.setMetadata("tool", "grep");
    context.setMetadata("pattern", args.pattern);

    const fileMatches = new Map<
      string,
      Array<{
        line: number;
        text: string;
        submatches: Array<{ text: string; start: number; end: number }>;
      }>
    >();

    const generator = Ripgrep.search(args.pattern, args.path, {
      caseSensitive: args.caseSensitive,
      wholeWord: args.wholeWord,
      regex: args.regex,
      include: args.include,
      exclude: args.exclude,
      maxResults: 100,
    });

    for await (const result of generator) {
      if (context.abort.aborted) break;

      if (result.type === "match") {
        const { path, line_number, lines, submatches } = result.data;
        const filePath = path.text;

        if (!fileMatches.has(filePath)) {
          fileMatches.set(filePath, []);
        }

        fileMatches.get(filePath)!.push({
          line: line_number,
          text: lines.text,
          submatches: submatches.map((m) => ({
            text: m.match.text,
            start: m.start,
            end: m.end,
          })),
        });
      }
    }

    const results: string[] = [];
    const sortedFiles = Array.from(fileMatches.entries()).sort((a, b) => a[0].localeCompare(b[0]));

    for (const [filePath, matches] of sortedFiles) {
      results.push(`${filePath}:`);
      for (const match of matches) {
        const line = match.line.toString().padStart(4, " ");
        const highlighted = highlightLine(match.text, match.submatches);
        results.push(`  ${line}: ${highlighted}`);
      }
      results.push("");
    }

    const output = results.join("\n").trim() || "No matches found";

    return {
      title: `Grep: ${args.pattern}`,
      metadata: {
        pattern: args.pattern,
        files: fileMatches.size,
        matches: Array.from(fileMatches.values()).reduce((acc, m) => acc + m.length, 0),
      },
      output: truncateOutput(output),
    };
  },
});

function highlightLine(
  text: string,
  submatches: Array<{ text: string; start: number; end: number }>
): string {
  if (submatches.length === 0) return text;

  let result = "";
  let lastIndex = 0;

  for (const match of submatches.sort((a, b) => a.start - b.start)) {
    result += text.slice(lastIndex, match.start);
    result += `\x1b[31m\x1b[1m${text.slice(match.start, match.end)}\x1b[0m`;
    lastIndex = match.end;
  }

  result += text.slice(lastIndex);
  return result;
}
