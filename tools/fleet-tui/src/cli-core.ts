import { createHash, randomUUID } from "node:crypto";
import { open, rename, rm } from "node:fs/promises";
import { basename, dirname, join } from "node:path";

import { FleetApiClient } from "./fleet-api-client.js";

export type CliOptions = {
  apiUrl: string;
  sessionId?: string;
  artifactId?: string;
  outputPath?: string;
};

export function parseArgs(args: string[]): CliOptions | "help" {
  const options: CliOptions = {
    apiUrl: process.env.FLEET_API_URL ?? "http://127.0.0.1:8000",
  };
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

export function createFleetClient(options: CliOptions): FleetApiClient {
  return new FleetApiClient({ baseUrl: options.apiUrl });
}

export async function saveArtifact(
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

export async function runArtifactDownload(options: CliOptions): Promise<boolean> {
  if (!options.artifactId || !options.outputPath) {
    return false;
  }
  const client = createFleetClient(options);
  await saveArtifact(client, options.artifactId, options.outputPath);
  process.stdout.write(`Saved verified artifact to ${options.outputPath}\n`);
  return true;
}

export function inkUsage(): string {
  return `Usage: pnpm start -- [options]

Options:
  --api-url <url>          Fleet API base URL (default: http://127.0.0.1:8000)
  --session <uuid>         Resume an existing Fleet session
  artifact <uuid> --output <path>
                            Download, verify, and atomically save an Artifact
  --help, -h               Show this help

Slash commands:
  /help      list commands
  /clear     clear the visible conversation
  /sessions  list recent sessions
  /resume    resume a session by id
  /cancel    cancel the current run
  /status    show session, run, and usage
  /exit      exit
`;
}
