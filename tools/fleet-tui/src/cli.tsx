#!/usr/bin/env node

import { render } from "ink";

import {
  createFleetClient,
  inkUsage,
  parseArgs,
  runArtifactDownload,
  type CliOptions,
} from "./cli-core.js";
import { App } from "./tui/App.js";
import { projectDurableTurns } from "./tui/projection.js";

export { parseArgs, type CliOptions };

export async function run(options: CliOptions): Promise<void> {
  if (await runArtifactDownload(options)) {
    return;
  }

  const client = createFleetClient(options);
  const resumed = Boolean(options.sessionId);
  const session = resumed
    ? await client.getSession(options.sessionId!)
    : await client.createSession();

  process.stdout.write(`Fleet session: ${session.id}\n`);
  const initialEvents = resumed
    ? projectDurableTurns(await client.listTurns(session.id))
    : [];
  render(
    <App
      apiUrl={options.apiUrl}
      client={client}
      session={session}
      resumed={resumed}
      initialEvents={initialEvents}
    />,
    { incrementalRendering: true },
  );
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options === "help") {
      process.stdout.write(inkUsage());
    } else {
      await run(options);
    }
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : "Fleet TUI failed"}\n`);
    process.exitCode = 1;
  }
}
