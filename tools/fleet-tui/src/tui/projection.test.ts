import { describe, expect, it } from "vitest";

import type { FleetTurn } from "../fleet-api-client.js";
import type { FleetUIMessageChunk } from "../sse.js";
import { LiveTurnProjector, projectDurableTurns } from "./projection.js";
import type { Message, StoreEvent } from "./store.js";

const clock = () => 100;

describe("terminal projection", () => {
  it("renders a default text-only Prediction identically live and after hydration", () => {
    const live = new LiveTurnProjector(clock);
    const liveMessages = finalMessages(
      [
        { type: "start", messageId: "run-1", messageMetadata: {} },
        { type: "text-start", id: "text-run-1" },
        { type: "text-delta", id: "text-run-1", delta: "The answer." },
        { type: "text-end", id: "text-run-1" },
      ].flatMap((chunk) => live.push(chunk as FleetUIMessageChunk)),
    );
    const durableMessages = finalMessages(
      projectDurableTurns(
        [
          {
            id: "run-1",
            role: "assistant",
            metadata: { runId: "run-1" },
            parts: [{ type: "text", text: "The answer.", state: "done" }],
          },
        ] satisfies FleetTurn[],
        clock,
      ),
    );

    expect(correlations(liveMessages)).toEqual(correlations(durableMessages));
    expect(correlations(liveMessages)).toEqual([
      {
        kind: "text",
        role: "assistant",
        text: "The answer.",
        streaming: false,
      },
    ]);
  });

  it("emits store events with live/reload parity for every visible kind", () => {
    const live = new LiveTurnProjector(clock);
    const chunks: FleetUIMessageChunk[] = [
      { type: "start", messageId: "run-1", messageMetadata: {} },
      {
        type: "data-structured-result",
        id: "result-run-1",
        data: { schemaId: "answer", schemaVersion: "1", value: 7 },
      },
      { type: "text-start", id: "text-1" },
      { type: "text-delta", id: "text-1", delta: "The answer is 7." },
      { type: "text-end", id: "text-1" },
      { type: "reasoning-start", id: "reasoning-2" },
      { type: "reasoning-delta", id: "reasoning-2", delta: "Think" },
      { type: "reasoning-end", id: "reasoning-2" },
      { type: "data-rlm-code", id: "code-2", data: { step: 2, code: "print(1)" } },
      { type: "data-rlm-output", id: "output-2", data: { step: 2, output: "1" } },
      {
        type: "tool-input-available",
        toolCallId: "call-1",
        toolName: "read",
        input: { path: "x" },
      },
      { type: "tool-output-available", toolCallId: "call-1", output: "ok" },
      {
        type: "data-skill",
        id: "skill-part",
        data: { skill_id: "skill-1", name: "inspect", version: "1", trust: "system" },
      },
      {
        type: "data-attachment",
        id: "attachment-part",
        data: { attachment_id: "attachment-1", filename: "input.txt", byte_size: 2 },
      },
      {
        type: "data-artifact",
        id: "artifact-part",
        data: { artifact_id: "artifact-1", title: "report", kind: "file", byte_size: 3 },
      },
      {
        type: "data-usage",
        id: "usage-part",
        data: {
          usage: {
            iterations: 2,
            observed_lm_usage: { root: { prompt_tokens: 4, completion_tokens: 5 } },
            duration_ms: 1200,
          },
        },
      },
      { type: "data-status", id: "status-part", data: { phase: "running", detail: "work" } },
      { type: "data-warning", id: "warning-part", data: { code: "w", message: "warn" } },
    ];
    const liveMessages = finalMessages(chunks.flatMap((chunk) => live.push(chunk)));

    const durableEvents = projectDurableTurns(
      [
        {
          id: "run-1",
          role: "assistant",
          metadata: { runId: "run-1" },
          parts: [
            {
              type: "data-structured-result",
              data: { schemaId: "answer", schemaVersion: "1", value: 7 },
            },
            { type: "text", text: "The answer is 7.", state: "done" },
            { type: "data-step", data: { step: 2 } },
            { type: "reasoning", text: "Think", state: "done" },
            { type: "data-rlm-code", data: { step: 2, code: "print(1)" } },
            { type: "data-rlm-output", data: { step: 2, output: "1" } },
            {
              type: "dynamic-tool",
              toolCallId: "call-1",
              toolName: "read",
              input: { path: "x" },
              output: "ok",
              state: "output-available",
            },
            {
              type: "data-skill",
              id: "skill-part",
              data: { skillId: "skill-1", name: "inspect", version: "1", trust: "system" },
            },
            {
              type: "data-attachment",
              data: { attachmentId: "attachment-1", filename: "input.txt", byteSize: 2 },
            },
            {
              type: "data-artifact",
              data: { artifactId: "artifact-1", title: "report", kind: "file", byteSize: 3 },
            },
            {
              type: "data-usage",
              data: {
                iterations: 2,
                observed_lm_usage: { root: { prompt_tokens: 4, completion_tokens: 5 } },
                duration_ms: 1200,
              },
            },
            { type: "data-status", data: { phase: "running", detail: "work" } },
            { type: "data-warning", data: { code: "w", message: "warn" } },
          ],
        },
      ] satisfies FleetTurn[],
      clock,
    );
    const durableMessages = finalMessages(durableEvents);

    expect(durableEvents.every((event) => event.type === "message/upsert")).toBe(true);
    expect(liveMessages.map((message) => message.kind)).toEqual([
      "result",
      "reasoning",
      "code",
      "output",
      "tool",
      "skill",
      "attachment",
      "artifact",
      "usage",
      "status",
      "warning",
    ]);
    expect(durableMessages.map((message) => message.kind)).toEqual(
      liveMessages.map((message) => message.kind),
    );
    expect(durableMessages.map((message) => message.id)).toEqual([
      "run-1:0",
      "run-1:3",
      "run-1:4",
      "run-1:5",
      "run-1:6",
      "run-1:7",
      "run-1:8",
      "run-1:9",
      "run-1:10",
      "run-1:11",
      "run-1:12",
    ]);
    expect(correlations(liveMessages)).toEqual(correlations(durableMessages));
    const usageMessage = liveMessages.find(
      (message): message is Extract<Message, { kind: "usage" }> => message.kind === "usage",
    );
    expect(usageMessage).toMatchObject({
      kind: "usage",
      runId: "run-1",
      iterations: 2,
      prompt: 4,
      completion: 5,
      durationMs: 1200,
      observedLmUsage: { root: { prompt_tokens: 4, completion_tokens: 5 } },
    });
    expect(liveMessages.map((message) => message.id)).toEqual([
      "result-run-1",
      "thinking-reasoning-2",
      "code-code-2",
      "output-output-2",
      "tool-call-1",
      "skill-part",
      "attachment-part",
      "artifact-part",
      "usage-part",
      "status-part",
      "warning-part",
    ]);
  });

  it("ignores step bookkeeping but projects structured results", () => {
    expect(
      projectDurableTurns(
        [
          {
            id: "turn-1",
            role: "assistant",
            parts: [
              { type: "step-start" },
              { type: "data-step", data: { step: 1 } },
              { type: "data-structured-result", data: { value: 1 } },
            ],
          },
        ],
        clock,
      ),
    ).toMatchObject([
      {
        type: "message/upsert",
        message: { kind: "result", value: 1, schemaId: "", schemaVersion: "" },
      },
    ]);
  });

  it("keeps repeated provider data parts as separate live messages", () => {
    const projector = new LiveTurnProjector(clock);
    const messages = finalMessages(
      [
        { type: "start", messageId: "run-1", messageMetadata: {} },
        {
          type: "data-attachment",
          id: "attachment-1",
          data: { attachment_id: "attachment-1", filename: "first.txt", byte_size: 1 },
        },
        {
          type: "data-attachment",
          id: "attachment-1",
          data: { attachment_id: "attachment-1", filename: "second.txt", byte_size: 2 },
        },
        {
          type: "data-skill",
          id: "skill-1",
          data: { skill_id: "skill-1", name: "inspect", version: "1", trust: "system" },
        },
        {
          type: "data-skill",
          id: "skill-1",
          data: { skill_id: "skill-1", name: "inspect", version: "1", trust: "system" },
        },
      ].flatMap((chunk) => projector.push(chunk as FleetUIMessageChunk)),
    );

    expect(messages.map((message) => message.id)).toEqual([
      "attachment-1",
      "attachment-1:1",
      "skill-1",
      "skill-1:1",
    ]);
  });

  it("merges narrative and result regardless of durable part order", () => {
    const parts = [
      { type: "text", text: "Explanation", state: "done" } as const,
      {
        type: "data-structured-result",
        data: { schemaId: "answer", schemaVersion: "1", value: { digit: "7" } },
      } as const,
    ];
    const project = (ordered: typeof parts) =>
      finalMessages(
        projectDurableTurns([{ id: "turn-1", role: "assistant", parts: ordered }], clock),
      ).map(({ id: _id, ts: _ts, ...message }) => message);

    expect(project(parts)).toEqual(project([...parts].reverse()));
    expect(project(parts)).toEqual([
      {
        kind: "result",
        runId: "turn-1",
        schemaId: "answer",
        schemaVersion: "1",
        value: { digit: "7" },
        narrative: "Explanation",
      },
    ]);
  });

  it("suppresses narrative that exactly duplicates a single scalar result", () => {
    const messages = finalMessages(
      projectDurableTurns(
        [
          {
            id: "turn-1",
            role: "assistant",
            parts: [
              { type: "data-structured-result", data: { value: { digit: "7" } } },
              { type: "text", text: "7", state: "done" },
            ],
          },
        ],
        clock,
      ),
    );
    expect(messages[0]).not.toHaveProperty("narrative");
  });

  it("replaces an already-streaming assistant message when a result arrives later", () => {
    const projector = new LiveTurnProjector(clock);
    const messages = finalMessages(
      [
        { type: "start", messageId: "run-1", messageMetadata: {} },
        { type: "text-start", id: "text-1" },
        { type: "text-delta", id: "text-1", delta: "Explanation" },
        {
          type: "data-structured-result",
          id: "result-1",
          data: { schemaId: "answer", schemaVersion: "1", value: 7 },
        },
      ].flatMap((chunk) => projector.push(chunk as FleetUIMessageChunk)),
    );

    expect(messages).toMatchObject([
      {
        id: "text-1",
        kind: "result",
        value: 7,
        narrative: "Explanation",
      },
    ]);
  });
});

function finalMessages(events: StoreEvent[]): Message[] {
  const byId = new Map<string, Message>();
  for (const event of events) {
    if (event.type === "message/upsert") byId.set(event.message.id, event.message);
  }
  return [...byId.values()];
}

function correlations(messages: Message[]): unknown[] {
  return messages.map((message) => {
    switch (message.kind) {
      case "text":
        return {
          kind: message.kind,
          role: message.role,
          text: message.text,
          streaming: message.streaming,
        };
      case "reasoning":
        return { kind: message.kind, runId: message.runId, step: message.step, text: message.text };
      case "code":
        return {
          kind: message.kind,
          runId: message.runId,
          step: message.step,
          code: message.code,
        };
      case "output":
        return {
          kind: message.kind,
          runId: message.runId,
          step: message.step,
          output: message.output,
        };
      case "result":
        return {
          kind: message.kind,
          runId: message.runId,
          schemaId: message.schemaId,
          schemaVersion: message.schemaVersion,
          value: message.value,
          narrative: message.narrative,
        };
      case "tool":
        return {
          kind: message.kind,
          runId: message.runId,
          toolCallId: message.toolCallId,
          name: message.name,
          input: message.input,
          output: message.output,
          status: message.status,
        };
      case "skill":
        return {
          kind: message.kind,
          runId: message.runId,
          skillId: message.skillId,
          name: message.name,
          version: message.version,
          trust: message.trust,
        };
      case "attachment":
        return {
          kind: message.kind,
          runId: message.runId,
          attachmentId: message.attachmentId,
          filename: message.filename,
          bytes: message.bytes,
        };
      case "artifact":
        return {
          kind: message.kind,
          runId: message.runId,
          artifactId: message.artifactId,
          name: message.name,
          artifactKind: message.artifactKind,
          bytes: message.bytes,
        };
      case "usage":
        return {
          kind: message.kind,
          runId: message.runId,
          iterations: message.iterations,
          prompt: message.prompt,
          completion: message.completion,
          durationMs: message.durationMs,
          observedLmUsage: message.observedLmUsage,
        };
      case "status":
        return {
          kind: message.kind,
          runId: message.runId,
          phase: message.phase,
          detail: message.detail,
        };
      case "warning":
        return {
          kind: message.kind,
          runId: message.runId,
          code: message.code,
          message: message.message,
        };
      case "error":
        return { kind: message.kind, text: message.text };
    }
    return assertNever(message);
  });
}

function assertNever(value: never): never {
  throw new Error(`Unexpected message: ${JSON.stringify(value)}`);
}
