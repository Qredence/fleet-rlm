import { z } from "zod";

export const MatchSchema = z.object({
  path: z.object({
    text: z.string(),
  }),
  lines: z.object({
    text: z.string(),
  }),
  line_number: z.number(),
  absolute_offset: z.number(),
  submatches: z.array(
    z.object({
      match: z.object({
        text: z.string(),
      }),
      start: z.number(),
      end: z.number(),
    })
  ),
});

export const BeginSchema = z.object({
  type: z.literal("begin"),
  data: z.object({
    path: z.object({
      text: z.string(),
    }),
  }),
});

export const MatchLineSchema = z.object({
  type: z.literal("match"),
  data: MatchSchema,
});

export const EndSchema = z.object({
  type: z.literal("end"),
  data: z.object({
    path: z.object({
      text: z.string(),
    }),
    binary_offset: z.number().optional(),
    stats: z.object({
      elapsed: z.object({
        secs: z.number(),
        nanos: z.number(),
        human: z.string().optional(),
      }),
    }),
  }),
});

export const SummarySchema = z.object({
  type: z.literal("summary"),
  data: z.object({
    elapsed_total: z.object({
      secs: z.number(),
      nanos: z.number(),
      human: z.string().optional(),
    }),
    stats: z.object({
      matched_lines: z.number(),
      matches: z.number(),
    }),
  }),
});

export const RipgrepResultSchema = z.union([
  BeginSchema,
  MatchLineSchema,
  EndSchema,
  SummarySchema,
]);

export type RipgrepResult = z.infer<typeof RipgrepResultSchema>;
export type RipgrepMatch = z.infer<typeof MatchSchema>;
export interface FileMatch {
  path: string;
  matches: Array<{
    line: number;
    text: string;
    submatches: Array<{ text: string; start: number; end: number }>;
  }>;
}

export interface TreeNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: TreeNode[];
}

export class Ripgrep {
  private static binaryPath: string | null = null;

  static async detect(): Promise<string | null> {
    if (this.binaryPath) return this.binaryPath;

    const platform = process.platform;
    const name = platform === "win32" ? "rg.exe" : "rg";

    try {
      const proc = Bun.spawn(["which", name], {
        stdout: "pipe",
        stderr: "pipe",
      });
      const exitCode = await proc.exited;
      if (exitCode === 0) {
        const output = await new Response(proc.stdout).text();
        this.binaryPath = output.trim();
        return this.binaryPath;
      }
    } catch {
      // Fall through to other detection methods
    }

    const paths = ["/usr/local/bin/rg", "/usr/bin/rg", `${process.env.HOME}/.cargo/bin/rg`];
    for (const path of paths) {
      try {
        const file = Bun.file(path);
        if (await file.exists()) {
          this.binaryPath = path;
          return path;
        }
      } catch {
        continue;
      }
    }

    return null;
  }

  static async files(
    dir: string = ".",
    globs?: string[],
    exclude?: string[]
  ): Promise<string[]> {
    const binary = await this.detect();
    if (!binary) throw new Error("ripgrep binary not found");

    const args = ["--files", "--hidden", "--follow", "-g", "!.git"];

    if (globs) {
      for (const glob of globs) {
        args.push("-g", glob);
      }
    }

    if (exclude) {
      for (const pattern of exclude) {
        args.push("-g", `!${pattern}`);
      }
    }

    const proc = Bun.spawn([binary, ...args], {
      cwd: dir,
      stdout: "pipe",
      stderr: "pipe",
    });

    const exitCode = await proc.exited;
    if (exitCode !== 0) {
      const stderr = await new Response(proc.stderr).text();
      throw new Error(`ripgrep failed: ${stderr}`);
    }

    const output = await new Response(proc.stdout).text();
    return output
      .split("\n")
      .filter((line) => line.length > 0)
      .sort();
  }

  static async *search(
    pattern: string,
    dir: string = ".",
    opts: {
      caseSensitive?: boolean;
      wholeWord?: boolean;
      regex?: boolean;
      include?: string[];
      exclude?: string[];
      maxResults?: number;
      contextLines?: number;
    } = {}
  ): AsyncGenerator<RipgrepResult> {
    const binary = await this.detect();
    if (!binary) throw new Error("ripgrep binary not found");

    const args = [
      "--json",
      "--hidden",
      "--no-messages",
      "--field-match-separator=|",
    ];

    if (!opts.caseSensitive) args.push("--ignore-case");
    if (opts.wholeWord) args.push("--word-regexp");
    if (!opts.regex) pattern = escapeRegex(pattern);

    if (opts.include) {
      for (const glob of opts.include) {
        args.push("-g", glob);
      }
    }

    if (opts.exclude) {
      for (const pattern of opts.exclude) {
        args.push("-g", `!${pattern}`);
      }
    }

    if (opts.contextLines) {
      args.push("-C", String(opts.contextLines));
    }

    args.push(pattern);

    const proc = Bun.spawn([binary, ...args], {
      cwd: dir,
      stdout: "pipe",
      stderr: "pipe",
    });

    const reader = proc.stdout.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let count = 0;
    const maxResults = opts.maxResults ?? 100;

    try {
      while (count < maxResults) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (line.trim().length === 0) continue;

          try {
            const parsed = JSON.parse(line);
            const result = RipgrepResultSchema.parse(parsed);
            yield result;

            if (result.type === "match") {
              count++;
              if (count >= maxResults) break;
            }
          } catch {
            continue;
          }
        }
      }
    } finally {
      reader.releaseLock();
      proc.kill();
    }
  }

  static async tree(dir: string = ".", globs?: string[]): Promise<TreeNode> {
    const files = await this.files(dir, globs);
    const root: TreeNode = { name: dir.split("/").pop() || ".", path: dir, type: "directory", children: [] };
    const map = new Map<string, TreeNode>();
    map.set(dir, root);

    for (const file of files) {
      const parts = file.split("/");
      let currentPath = dir;

      for (let i = 0; i < parts.length; i++) {
        const part = parts[i]!;
        const parentPath = currentPath;
        currentPath = currentPath === "." ? part : `${currentPath}/${part}`;

        if (!map.has(currentPath)) {
          const node: TreeNode = {
            name: part,
            path: currentPath,
            type: i === parts.length - 1 ? "file" : "directory",
          };

          if (node.type === "directory") {
            node.children = [];
          }

          map.set(currentPath, node);

          const parent = map.get(parentPath);
          if (parent && parent.children) {
            parent.children.push(node);
          }
        }
      }
    }

    return root;
  }

  static async formatTree(dir: string = ".", globs?: string[]): Promise<string> {
    const root = await this.tree(dir, globs);
    return this.renderTreeNode(root, "", true);
  }

  private static renderTreeNode(node: TreeNode, prefix: string, isLast: boolean): string {
    const connector = isLast ? "└── " : "├── ";
    let output = prefix + connector + node.name + "\n";

    if (node.children) {
      const children = node.children.sort((a, b) => {
        if (a.type === b.type) return a.name.localeCompare(b.name);
        return a.type === "directory" ? -1 : 1;
      });

      for (let i = 0; i < children.length; i++) {
        const childPrefix = prefix + (isLast ? "    " : "│   ");
        output += this.renderTreeNode(children[i]!, childPrefix, i === children.length - 1);
      }
    }

    return output;
  }
}

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
