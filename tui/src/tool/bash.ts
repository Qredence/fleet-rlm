import { z } from "zod";
import { Tool, truncateOutput } from "./tool";

const BashParamsSchema = z.object({
  command: z.string().describe("Shell command to execute"),
  cwd: z.string().default(".").describe("Working directory"),
  timeout: z.number().default(30000).describe("Timeout in milliseconds"),
  env: z.record(z.string()).optional().describe("Environment variables"),
});

export const BashTool = Tool.define("bash", {
  name: "Bash",
  description: "Execute shell commands",
  parameters: BashParamsSchema,
  execute: async (args, context) => {
    context.setMetadata("tool", "bash");
    context.setMetadata("command", args.command);

    const proc = Bun.spawn(args.command.split(" "), {
      cwd: args.cwd,
      env: args.env ? { ...process.env, ...args.env } : process.env,
      stdout: "pipe",
      stderr: "pipe",
    });

    const timeoutId = setTimeout(() => {
      proc.kill();
    }, args.timeout);

    try {
      const exitCode = await proc.exited;
      clearTimeout(timeoutId);

      const stdout = await new Response(proc.stdout).text();
      const stderr = await new Response(proc.stderr).text();

      const output: string[] = [];
      if (stdout) output.push(stdout);
      if (stderr) output.push(`stderr:\n${stderr}`);

      return {
        title: `Bash: ${args.command.slice(0, 40)}${args.command.length > 40 ? "..." : ""}`,
        metadata: {
          command: args.command,
          cwd: args.cwd,
          exitCode,
          timedOut: exitCode === null,
        },
        output: truncateOutput(output.join("\n\n") || "(no output)"),
      };
    } catch (error) {
      clearTimeout(timeoutId);
      proc.kill();

      return {
        title: `Bash: ${args.command.slice(0, 40)}...`,
        metadata: {
          command: args.command,
          cwd: args.cwd,
          error: true,
        },
        output: truncateOutput(`Error: ${error instanceof Error ? error.message : String(error)}`),
      };
    }
  },
});
