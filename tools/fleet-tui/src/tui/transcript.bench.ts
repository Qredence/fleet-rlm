import { performance } from "node:perf_hooks";

import { ConversationStore } from "./store.js";
import { TranscriptComponent } from "./transcript.js";

const updatesPerSample = 300;
const simulatedEventsPerSecond = 30;
const samples = 20;
// Sweep history sizes so per-update frame cost stays flat as sessions grow.
const historySizes = [1_000, 10_000, 50_000];

function percentile(values: number[], quantile: number): number {
  const index = Math.min(values.length - 1, Math.max(0, Math.ceil(values.length * quantile) - 1));
  return Number((values[index] ?? 0).toFixed(3));
}

function runSample(retainedLines: number): number {
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

const results = historySizes.map((retainedLines) => {
  const durations = Array.from({ length: samples }, () => runSample(retainedLines)).sort(
    (left, right) => left - right,
  );
  return {
    retainedLines,
    updatesPerSample,
    simulatedEventsPerSecond,
    samples,
    scenarioMs: { p50: percentile(durations, 0.5), p95: percentile(durations, 0.95) },
    updateMs: {
      p50: percentile(durations, 0.5) / updatesPerSample,
      p95: percentile(durations, 0.95) / updatesPerSample,
    },
  };
});

process.stdout.write(`${JSON.stringify(results, null, 2)}\n`);
