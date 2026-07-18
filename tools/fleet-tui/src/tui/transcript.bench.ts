import { performance } from "node:perf_hooks";

import { ConversationStore } from "./store.js";
import { TranscriptComponent } from "./transcript.js";

const retainedLines = 10_000;
const updatesPerSample = 300;
const simulatedEventsPerSecond = 30;
const samples = 20;

const durations = Array.from({ length: samples }, () => runSample()).sort(
  (left, right) => left - right,
);
const p50 = percentile(durations, 0.5);
const p95 = percentile(durations, 0.95);

process.stdout.write(
  `${JSON.stringify(
    {
      retainedLines,
      updatesPerSample,
      simulatedEventsPerSecond,
      samples,
      scenarioMs: { p50, p95 },
      updateMs: { p50: p50 / updatesPerSample, p95: p95 / updatesPerSample },
    },
    null,
    2,
  )}\n`,
);

function runSample(): number {
  const store = new ConversationStore();
  store.dispatch({
    type: "message/upsert",
    message: {
      id: "history",
      kind: "output",
      runId: "run-history",
      step: 1,
      output: Array.from({ length: retainedLines }, (_, index) => `row-${index}`).join("\n"),
      ts: 1,
    },
  });
  const transcript = new TranscriptComponent(store);
  transcript.render(100);

  const startedAt = performance.now();
  for (let index = 0; index < updatesPerSample; index += 1) {
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "live",
        kind: "text",
        role: "assistant",
        text: `delta-${index}`,
        streaming: true,
        ts: 2,
      },
    });
    transcript.render(100);
  }
  return performance.now() - startedAt;
}

function percentile(values: number[], quantile: number): number {
  const index = Math.min(values.length - 1, Math.max(0, Math.ceil(values.length * quantile) - 1));
  return Number((values[index] ?? 0).toFixed(3));
}
