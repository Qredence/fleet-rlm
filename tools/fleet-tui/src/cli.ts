#!/usr/bin/env node

import { runAgentTUI } from "@ai-sdk/tui";

import { FleetApiClient } from "./fleet-api-client.js";
import { FleetSseAgent } from "./fleet-sse-agent.js";
import { formatTranscript } from "./transcript.js";

export type CliOptions = {
  apiUrl: string;
  sessionId?: string;
  userId?: string;
  workspaceId?: string;
};

export function parseArgs(args: string[]): CliOptions | "help" {
  const options: CliOptions = { apiUrl: "http://127.0.0.1:8000" };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--") {
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      return "help";
    }
    const value = args[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for ${arg}`);
    }
    switch (arg) {
      case "--api-url":
        options.apiUrl = value;
        break;
      case "--session":
        options.sessionId = value;
        break;
      case "--user-id":
        options.userId = value;
        break;
      case "--workspace-id":
        options.workspaceId = value;
        break;
      default:
        throw new Error(`Unknown option: ${arg}`);
    }
    index += 1;
  }
  return options;
}

export async function run(options: CliOptions): Promise<void> {
  const client = new FleetApiClient({
    baseUrl: options.apiUrl,
    identity: { userId: options.userId, workspaceId: options.workspaceId },
  });
  const resumed = Boolean(options.sessionId);
  const session = resumed ? await client.getSession(options.sessionId!) : await client.createSession();

  process.stdout.write(`Fleet session: ${session.id}\n`);
  if (resumed) {
    process.stdout.write(formatTranscript(await client.listTurns(session.id)));
  }
  await runAgentTUI({
    title: "Fleet RLM",
    agent: new FleetSseAgent(client, session.id),
    tools: "auto-collapsed",
    reasoning: "auto-collapsed",
  });
}

function usage(): string {
  return `Usage: pnpm start -- [options]

Options:
  --api-url <url>          Fleet API base URL (default: http://127.0.0.1:8000)
  --session <uuid>         Resume an existing Fleet session
  --user-id <uuid>         Synthetic dev identity header
  --workspace-id <uuid>    Synthetic dev workspace header
  --help, -h               Show this help
`;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options === "help") {
      process.stdout.write(usage());
    } else {
      await run(options);
    }
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : "Fleet TUI failed"}\n`);
    process.exitCode = 1;
  }
}
