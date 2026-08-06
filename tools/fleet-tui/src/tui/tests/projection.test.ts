import { describe, expect, it } from "vitest";

import type { FleetTurn } from "../../fleet-api-client.js";
import type { FleetUIMessageChunk } from "../../sse.js";
import { LiveTurnProjector, projectDurableTurns } from "../projection.js";
import type { Message, StoreEvent } from "../store.js";

const clock = () => 100;

describe("terminal projection", () => {
  it("does not create an empty assistant message before text content arrives", () => {
    const live = new LiveTurnProjector(clock);

    expect(live.push({ type: "text-start", id: "text-1" })).toEqual([]);
    expect(live.push({ type: "text-delta", id: "text-1", delta: "" })).toEqual([]);
    expect(live.push({ type: "text-end", id: "text-1" })).toEqual([]);
  });

  it("projects live status into Run state and omits transient durable status", () => {
    const live = new LiveTurnProjector(clock);

    expect(
      live.push({
        type: "data-status",
        data: { phase: "execution", status: "running", message: null },
      }),
    ).toEqual([{ type: "run/status", phase: "execution", detail: "running" }]);
    expect(
      projectDurableTurns(
        [
          {
            id: "run-1",
            role: "assistant",
            parts: [
              {
                type: "data-status",
                data: { phase: "execution", status: "running" },
              },
            ],
          },
        ],
        clock,
      ),
    ).toEqual([]);
  });

  it("projects the existing SSE Run lifecycle into explicit store events", () => {
    const live = new LiveTurnProjector(clock);

    expect(
      live.push({
        type: "start",
        messageId: "run-1",
        messageMetadata: { delivery: "replay" },
      }),
    ).toEqual([{ type: "run/start", runId: "run-1", delivery: "replay", traceId: null }]);
    expect(live.push({ type: "start-step" })).toEqual([{ type: "run/step-start" }]);
    expect(live.push({ type: "finish-step" })).toEqual([{ type: "run/step-finish" }]);
    expect(
      live.push({
        type: "finish",
        finishReason: "stop",
        messageMetadata: { durationMs: 1250, checkpointVersion: 7 },
      }),
    ).toEqual([
      {
        type: "run/finish",
        finishReason: "stop",
        error: null,
        durationMs: 1250,
        checkpointVersion: 7,
        traceId: null,
      },
    ]);
  });

  it("preserves missing usage telemetry as unknown", () => {
    const live = new LiveTurnProjector(clock);
    live.push({ type: "start", messageId: "run-1", messageMetadata: {} });

    const events = live.push({
      type: "data-usage",
      id: "usage-part",
      data: { usage: {} },
    });

    expect(events).toMatchObject([
      {
        type: "message/upsert",
        message: {
          kind: "usage",
          runId: "run-1",
          iterations: null,
          durationMs: null,
        },
      },
    ]);
  });

  it("preserves backend failure and cancellation terminal detail", () => {
    const failed = new LiveTurnProjector(clock);
    failed.push({ type: "start", messageId: "run-1", messageMetadata: {} });

    const errorEvents = failed.push({ type: "error", errorText: "Run failed safely" });
    expect(errorEvents).toMatchObject([
      { type: "message/upsert", message: { kind: "error", text: "Run failed safely" } },
    ]);
    expect(failed.push({ type: "finish", finishReason: "error" })).toEqual([
      {
        type: "run/finish",
        finishReason: "error",
        error: "Run failed safely",
        durationMs: null,
        checkpointVersion: null,
        traceId: null,
      },
    ]);

    const cancelled = new LiveTurnProjector(clock);
    expect(cancelled.push({ type: "abort", reason: "Cancelled by operator" })).toEqual([
      { type: "run/cancelled", reason: "Cancelled by operator" },
    ]);
  });

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

  it("upserts a same-step trajectory correction into one canonical live card", () => {
    const live = new LiveTurnProjector(clock);
    const events = [
      { type: "data-rlm-code", id: "code-run-1-1", data: { step: 1, code: "stale" } },
      { type: "data-rlm-output", id: "output-run-1-1", data: { step: 1, output: "stale" } },
      { type: "data-rlm-code", id: "code-run-1-1", data: { step: 1, code: "canonical" } },
      { type: "data-rlm-output", id: "output-run-1-1", data: { step: 1, output: "canonical" } },
    ] satisfies FleetUIMessageChunk[];

    const messages = finalMessages(events.flatMap((chunk) => live.push(chunk)));

    expect(messages).toHaveLength(2);
    expect(messages).toMatchObject([
      { kind: "code", code: "canonical" },
      { kind: "output", output: "canonical" },
    ]);
  });

  it("renders incremental RLM deltas into stable reasoning and code cards", () => {
    const live = new LiveTurnProjector(clock);
    live.push({ type: "start", messageId: "run-1", messageMetadata: {} });

    const reasoning = [
      { type: "reasoning-start", id: "stream-reasoning" },
      { type: "reasoning-delta", id: "stream-reasoning", delta: "Inspect " },
      { type: "reasoning-delta", id: "stream-reasoning", delta: "now" },
      { type: "reasoning-end", id: "stream-reasoning" },
    ] satisfies FleetUIMessageChunk[];
    const code = [
      {
        type: "data-rlm-code",
        id: "stream-code",
        data: { step: 1, code: "SUBMIT(", is_delta: true, is_final: false },
      },
      {
        type: "data-rlm-code",
        id: "stream-code",
        data: { step: 1, code: "answer='done')", is_delta: true, is_final: true },
      },
    ] satisfies FleetUIMessageChunk[];

    const events = [...reasoning, ...code].flatMap((chunk) => live.push(chunk));
    const messages = finalMessages(events);

    expect(messages).toMatchObject([
      { kind: "reasoning", id: "thinking-stream-reasoning", text: "Inspect now" },
      {
        kind: "code",
        id: "code-stream-code",
        code: "SUBMIT(answer='done')",
        language: "python",
        streaming: false,
      },
    ]);
  });

  it("upserts a canonical reasoning correction into the existing step card", () => {
    const live = new LiveTurnProjector(clock);
    live.push({ type: "start", messageId: "run-1", messageMetadata: {} });

    const events = [
      { type: "reasoning-start", id: "stream-reasoning" },
      { type: "reasoning-delta", id: "stream-reasoning", delta: "stale" },
      { type: "reasoning-end", id: "stream-reasoning" },
      { type: "reasoning-start", id: "stream-reasoning:canonical" },
      { type: "reasoning-delta", id: "stream-reasoning:canonical", delta: "canonical" },
      { type: "reasoning-end", id: "stream-reasoning:canonical" },
    ] satisfies FleetUIMessageChunk[];

    expect(finalMessages(events.flatMap((chunk) => live.push(chunk)))).toMatchObject([
      { id: "thinking-stream-reasoning", kind: "reasoning", text: "canonical" },
    ]);
  });

  it("replaces output deltas with a canonical final output", () => {
    const live = new LiveTurnProjector(clock);
    live.push({ type: "start", messageId: "run-1", messageMetadata: {} });

    const events = [
      {
        type: "data-rlm-output",
        id: "stream-output",
        data: { step: 1, output: "first", is_delta: true, is_final: false },
      },
      {
        type: "data-rlm-output",
        id: "stream-output",
        data: { step: 1, output: "first second", is_delta: false, is_final: true },
      },
    ] satisfies FleetUIMessageChunk[];

    expect(finalMessages(events.flatMap((chunk) => live.push(chunk)))).toMatchObject([
      {
        kind: "output",
        id: "output-stream-output",
        output: "first second",
        streaming: false,
      },
    ]);
  });

  it("accumulates output deltas and settles an empty final delta", () => {
    const live = new LiveTurnProjector(clock);
    live.push({ type: "start", messageId: "run-1", messageMetadata: {} });

    const events = [
      {
        type: "data-rlm-output",
        id: "stream-output",
        data: { step: 1, output: "first", is_delta: true, is_final: false },
      },
      {
        type: "data-rlm-output",
        id: "stream-output",
        data: { step: 1, output: " second", is_delta: true, is_final: false },
      },
      {
        type: "data-rlm-output",
        id: "stream-output",
        data: { step: 1, output: "", is_delta: true, is_final: true },
      },
    ] satisfies FleetUIMessageChunk[];

    const messages = finalMessages(events.flatMap((chunk) => live.push(chunk)));

    expect(messages).toMatchObject([
      {
        kind: "output",
        id: "output-stream-output",
        output: "first second",
        streaming: false,
      },
    ]);
  });

  it("does not collapse malformed empty stream ids into one output card", () => {
    const live = new LiveTurnProjector(clock);
    live.push({ type: "start", messageId: "run-1", messageMetadata: {} });

    const first = live.push({
      type: "data-rlm-output",
      id: "output-first",
      data: { step: 1, output: "first", stream_id: "", is_delta: false, is_final: true },
    });
    const second = live.push({
      type: "data-rlm-output",
      id: "output-second",
      data: { step: 2, output: "second", stream_id: "", is_delta: false, is_final: true },
    });

    expect(finalMessages([...first, ...second])).toMatchObject([
      { kind: "output", id: "output-output-first", output: "first" },
      { kind: "output", id: "output-output-second", output: "second" },
    ]);
  });

  it("omits empty trajectory code and output cards in live and durable projection", () => {
    const live = new LiveTurnProjector(clock);
    const liveEvents = [
      { type: "start", messageId: "run-1", messageMetadata: {} },
      { type: "data-rlm-code", id: "code-run-1-1", data: { step: 1, code: "" } },
      { type: "data-rlm-output", id: "output-run-1-1", data: { step: 1, output: "" } },
    ] satisfies FleetUIMessageChunk[];
    const durableEvents = projectDurableTurns(
      [
        {
          id: "run-1",
          role: "assistant",
          metadata: { runId: "run-1" },
          parts: [
            { type: "data-step", data: { step: 1 } },
            { type: "data-rlm-code", data: { step: 1, code: "" } },
            { type: "data-rlm-output", data: { step: 1, output: "" } },
          ],
        },
      ] satisfies FleetTurn[],
      clock,
    );

    expect(finalMessages(liveEvents.flatMap((chunk) => live.push(chunk)))).toEqual([]);
    expect(finalMessages(durableEvents)).toEqual([]);
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
      inputTokens: 4,
      outputTokens: 5,
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

  it("renders the runtime artifact kind field", () => {
    const projector = new LiveTurnProjector(clock);
    const messages = finalMessages(
      [
        { type: "start", messageId: "run-1", messageMetadata: {} },
        {
          type: "data-artifact",
          id: "artifact-1",
          data: {
            artifact_id: "artifact-1",
            artifact_kind: "markdown",
            title: "report",
            media_type: "text/markdown",
            byte_size: 3,
            checksum_sha256: "a".repeat(64),
          },
        },
      ].flatMap((chunk) => projector.push(chunk as FleetUIMessageChunk)),
    );

    expect(messages).toMatchObject([{ kind: "artifact", artifactKind: "markdown" }]);
  });

  it("preserves distinct Skill lifecycle metadata live and after hydration", () => {
    const live = new LiveTurnProjector(clock);
    const chunks: FleetUIMessageChunk[] = [
      { type: "start", messageId: "run-1", messageMetadata: {} },
      {
        type: "data-skill",
        id: "skill-1",
        data: {
          skill_id: "skill-1",
          name: "inspect",
          phase: "activated",
          version: "2",
          trust: "workspace",
        },
      },
      {
        type: "data-skill",
        id: "skill-1",
        data: { skill_id: "skill-1", name: "inspect", phase: "loaded", version: "2" },
      },
    ];
    const liveMessages = finalMessages(chunks.flatMap((chunk) => live.push(chunk)));
    const durableMessages = finalMessages(
      projectDurableTurns(
        [
          {
            id: "run-1",
            role: "assistant",
            parts: chunks.slice(1).map((chunk) => ({
              type: "data-skill" as const,
              id: "id" in chunk ? chunk.id : undefined,
              data: "data" in chunk ? chunk.data : undefined,
            })),
          },
        ] satisfies FleetTurn[],
        clock,
      ),
    );

    const expected = [
      { kind: "skill", phase: "activated", trust: "workspace" },
      { kind: "skill", phase: "loaded" },
    ];
    expect(liveMessages).toMatchObject(expected);
    expect(durableMessages).toMatchObject(expected);
    expect(liveMessages[1]).not.toHaveProperty("trust");
    expect(durableMessages[1]).not.toHaveProperty("trust");
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
          phase: message.phase,
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
          inputTokens: message.inputTokens,
          outputTokens: message.outputTokens,
          durationMs: message.durationMs,
          observedLmUsage: message.observedLmUsage,
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
