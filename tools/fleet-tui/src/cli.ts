#!/usr/bin/env node

import { runAgentTUI } from "@ai-sdk/tui";
import { createHash, randomUUID } from "node:crypto";
import { open, rename, rm } from "node:fs/promises";
import { basename, dirname, join } from "node:path";

import { FleetApiClient } from "./fleet-api-client.js";
import { FleetSseAgent } from "./fleet-sse-agent.js";
import { formatTranscript } from "./transcript.js";

export type CliOptions = {
  apiUrl: string;
  sessionId?: string;
  userId?: string;
  workspaceId?: string;
  artifactId?: string;
  outputPath?: string;
};

export function parseArgs(args: string[]): CliOptions | "help" {
  const options: CliOptions = { apiUrl: process.env.FLEET_API_URL ?? "http://127.0.0.1:8000" };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--") {
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      return "help";
    }
    if (arg === "artifact") {
      const artifactId = args[index + 1];
      if (!artifactId || artifactId.startsWith("--")) {
        throw new Error("Missing artifact UUID");
      }
      options.artifactId = artifactId;
      index += 1;
      continue;
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
      case "--output":
        options.outputPath = value;
        break;
      default:
        throw new Error(`Unknown option: ${arg}`);
    }
    index += 1;
  }
  if (Boolean(options.artifactId) !== Boolean(options.outputPath)) {
    throw new Error("artifact <uuid> requires --output <path>");
  }
  return options;
}

export async function run(options: CliOptions): Promise<void> {
  const client = new FleetApiClient({
    baseUrl: options.apiUrl,
    identity: {
      token: process.env.FLEET_API_TOKEN,
      userId: options.userId,
      workspaceId: options.workspaceId,
    },
  });
  if (Boolean(options.userId) !== Boolean(options.workspaceId)) {
    throw new Error("--user-id and --workspace-id must be provided together");
  }
  if (process.env.FLEET_API_TOKEN && options.userId) {
    throw new Error("FLEET_API_TOKEN cannot be combined with synthetic dev identity");
  }
  if (options.artifactId && options.outputPath) {
    await saveArtifact(client, options.artifactId, options.outputPath);
    process.stdout.write(`Saved verified artifact to ${options.outputPath}\n`);
    return;
  }
  const resumed = Boolean(options.sessionId);
  const session = resumed
    ? await client.getSession(options.sessionId!)
    : await client.createSession();

  process.stdout.write(`Fleet session: ${session.id}\n`);
  if (resumed) {
    process.stdout.write(formatTranscript(await client.listTurns(session.id)));
  }
  await runAgentTUI({
    title: "Fleet RLM",
    agent: new FleetSseAgent(client, session.id),
    // A Fleet RLM run is an execution trace.  Keep earlier RLM steps and
    // reasoning expanded when later events arrive instead of collapsing them
    // into invisible history.
    tools: "full",
    reasoning: "full",
  });
}

async function saveArtifact(
  client: FleetApiClient,
  artifactId: string,
  outputPath: string,
): Promise<void> {
  const response = await client.downloadArtifact(artifactId);
  const expectedLength = response.headers.get("content-length");
  const expectedDigest = response.headers.get("etag")?.replace(/^W\//, "").replaceAll('"', "");
  if (!response.body || !expectedLength || !expectedDigest) {
    throw new Error("Artifact response is missing integrity headers");
  }

  const temporaryPath = join(dirname(outputPath), `.${basename(outputPath)}.${randomUUID()}.part`);
  const file = await open(temporaryPath, "wx");
  const digest = createHash("sha256");
  let byteSize = 0;
  try {
    const reader = response.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      await file.write(value);
      digest.update(value);
      byteSize += value.byteLength;
    }
    await file.sync();
    await file.close();
    if (byteSize !== Number(expectedLength) || digest.digest("hex") !== expectedDigest) {
      throw new Error("Artifact integrity verification failed");
    }
    await rename(temporaryPath, outputPath);
  } catch (error) {
    await file.close().catch(() => undefined);
    await rm(temporaryPath, { force: true });
    throw error;
  }
}

function usage(): string {
  return `Usage: pnpm start -- [options]

Options:
  --api-url <url>          Fleet API base URL (default: http://127.0.0.1:8000)
  --session <uuid>         Resume an existing Fleet session
  --user-id <uuid>         Synthetic dev identity header
  --workspace-id <uuid>    Synthetic dev workspace header
  artifact <uuid> --output <path>
                            Download, verify, and atomically save an Artifact
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
