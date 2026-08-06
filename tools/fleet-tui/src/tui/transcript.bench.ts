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
  let liveText = "";
  let liveCode = "";
  let liveOutput = "";
  for (let index = 0; index < updatesPerSample; index += 1) {
    liveText += `${index % 20 === 0 ? "\n\n## Step " : " token "}${index}`;
    liveCode += `${index === 0 ? "answer = " : ""}${index % 10}\n`;
    liveOutput += `${index % 25 === 0 ? "\n" : ""}output-${index}`;
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "live-text",
        kind: "text",
        role: "assistant",
        text: liveText,
        streaming: true,
        ts: index,
      },
    });
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "live-code",
        kind: "code",
        runId: "run-live",
        step: 1,
        code: liveCode,
        language: "python",
        streaming: true,
        ts: index,
      },
    });
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "live-output",
        kind: "output",
        runId: "run-live",
        step: 1,
        output: liveOutput,
        streaming: true,
        ts: index,
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
