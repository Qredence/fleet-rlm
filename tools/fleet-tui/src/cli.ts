#!/usr/bin/env node

import {
  type CliOptions,
  createFleetClient,
  parseArgs,
  runArtifactDownload,
  tuiUsage,
} from "./cli-core.js";
import { createFleetTui } from "./tui/application.js";
import { DraftStore } from "./tui/draft-store.js";
import { projectDurableTurns } from "./tui/durable-projection.js";

export { type CliOptions, parseArgs };

export async function run(options: CliOptions): Promise<void> {
  if (await runArtifactDownload(options)) return;
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new Error("Fleet TUI requires interactive stdin and stdout terminals");
  }

  const client = createFleetClient(options);
  const sessionId = options.sessionId;
  const resumed = Boolean(sessionId);
  const session = sessionId ? await client.getSession(sessionId) : await client.createSession();
  const initialEvents = resumed ? projectDurableTurns(await client.listTurns(session.id)) : [];

  process.stdout.write(`Fleet session: ${session.id}\n`);
  await createFleetTui({
    client,
    session,
    resumed,
    initialEvents,
    draftStore: new DraftStore(),
  }).start();
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options === "help") process.stdout.write(tuiUsage());
    else await run(options);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : "Fleet TUI failed"}\n`);
    process.exitCode = 1;
  }
}
