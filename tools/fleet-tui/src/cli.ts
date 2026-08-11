#!/usr/bin/env node

import {
  createFleetClient,
  parseArgs,
  runArtifactDownload,
  tuiUsage,
  type CliOptions,
} from "./cli-core.js";
import { createFleetTui } from "./tui/application.js";
import { DraftStore } from "./tui/draft-store.js";
import { projectDurableTurns } from "./tui/projection.js";

export { parseArgs, type CliOptions };

export async function run(options: CliOptions): Promise<void> {
  if (await runArtifactDownload(options)) return;
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    throw new Error("Fleet TUI requires interactive stdin and stdout terminals");
  }

  const client = createFleetClient(options);
  const resumed = Boolean(options.sessionId);
  const session = resumed
    ? await client.getSession(options.sessionId!)
    : await client.createSession();
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
