/**
 * Deterministic canonical Turn event-sequence generators (P32/QRE-193).
 *
 * Generated models respect canonical semantic identity/order constraints:
 * streams open, accumulate, and close; tool calls pair with one result;
 * structured results follow their narrative text; lifecycle brackets content.
 * Every model is fully derived from its seed, so any failure prints a
 * replayable seed plus the compact model JSON (the smallest practical
 * failing sequence is found by shrinking the model JSON by hand).
 *
 * The generator is source-agnostic: `renderLiveEvents` mirrors the
 * live-normalized route (fragmented start/delta/end streams), while
 * `renderDurableEvents` mirrors the durable-normalized route (single-shot,
 * terminally folded records) for the same semantic Turn.
 */

import type { CanonicalEvent } from "../canonical.js";

// ---------------------------------------------------------------------------
// Deterministic PRNG (no dependencies; seeds are reported on failure)
// ---------------------------------------------------------------------------

export function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class Rng {
  private readonly nextValue: () => number;

  constructor(readonly seed: number) {
    this.nextValue = mulberry32(seed);
  }

  int(min: number, max: number): number {
    return min + Math.floor(this.nextValue() * (max - min + 1));
  }

  chance(probability: number): boolean {
    return this.nextValue() < probability;
  }

  pick<T>(items: readonly T[]): T {
    return items[this.int(0, items.length - 1)] as T;
  }
}

// ---------------------------------------------------------------------------
// Semantic Turn model (both renderings derive from one model)
// ---------------------------------------------------------------------------

export interface ReasoningPlan {
  kind: "reasoning";
  step: number;
  fragments: string[];
}

export interface TextPlan {
  kind: "text";
  fragments: string[];
}

export interface CodePlan {
  kind: "code";
  step: number;
  fragments: string[];
}

export interface OutputPlan {
  kind: "output";
  step: number;
  fragments: string[];
}

export interface ToolPlan {
  kind: "tool";
  toolCallId: string;
  toolName: string;
  input: unknown;
  output?: unknown;
  error?: string;
}

export interface SkillPlan {
  kind: "skill";
  skillId: string;
  name: string;
}

export interface AttachmentPlan {
  kind: "attachment";
  attachmentId: string;
  filename: string;
  byteSize: number;
}

export interface ArtifactPlan {
  kind: "artifact";
  artifactId: string;
  title: string;
  artifactKind: string;
}

export interface WarningPlan {
  kind: "warning";
  code: string;
  message: string;
}

export type PartPlan =
  | ReasoningPlan
  | TextPlan
  | CodePlan
  | OutputPlan
  | ToolPlan
  | SkillPlan
  | AttachmentPlan
  | ArtifactPlan
  | WarningPlan;

export interface UsagePlan {
  iterations: number;
  durationMs: number;
  usage: Record<string, unknown>;
}

export interface ResultPlan {
  schemaId: string;
  schemaVersion: string;
  value: unknown;
}

export interface TurnModel {
  seed: number;
  runId: string;
  parts: PartPlan[];
  result: ResultPlan | null;
  /** Extra assistant text fragments streamed AFTER the structured result. */
  trailingNarrative: string[];
  usage: UsagePlan | null;
  finishReason: "stop" | "length" | "error";
  cancelled: boolean;
}

const WORDS = [
  "alpha",
  "bravo",
  "charlie\n",
  "delta ",
  "echo",
  "foxtrot ",
  "golf\n\n",
  "hotel",
  "india ",
  "juliet",
] as const;

const TOOL_NAMES = [
  "list_workspace_files",
  "read_workspace_file",
  "remember",
  "search_memories",
] as const;
const WARNING_CODES = ["memory_digest", "partial_trajectory", "attachment_degraded"] as const;

function fragments(rng: Rng, maxFragments: number): string[] {
  const count = rng.int(1, maxFragments);
  const out: string[] = [];
  for (let index = 0; index < count; index += 1) {
    out.push(rng.pick(WORDS) + String(rng.int(0, 99)));
  }
  return out;
}

function jsonSnippet(rng: Rng): unknown {
  return { value: rng.int(0, 1000), label: rng.pick(WORDS).trim(), ok: rng.chance(0.5) };
}

/** Build the deterministic semantic model for one seed. */
export function generateTurnModel(seed: number): TurnModel {
  const rng = new Rng(seed);
  const parts: PartPlan[] = [];
  const stepCount = rng.int(1, 3);
  let toolCount = 0;
  for (let step = 1; step <= stepCount; step += 1) {
    if (rng.chance(0.7)) parts.push({ kind: "reasoning", step, fragments: fragments(rng, 4) });
    if (rng.chance(0.6)) {
      parts.push({ kind: "code", step, fragments: fragments(rng, 3) });
      parts.push({ kind: "output", step, fragments: fragments(rng, 3) });
    }
    if (rng.chance(0.5)) {
      toolCount += 1;
      const toolCallId = `call-${seed}-${toolCount}`;
      const plan: ToolPlan = {
        kind: "tool",
        toolCallId,
        toolName: rng.pick(TOOL_NAMES),
        input: { args: rng.int(0, 9) },
      };
      if (rng.chance(0.2)) plan.error = `tool ${toolCount} failed`;
      else plan.output = jsonSnippet(rng);
      parts.push(plan);
    }
  }
  if (rng.chance(0.4)) {
    parts.push({
      kind: "skill",
      skillId: `skill-${rng.int(1, 3)}`,
      name: `skill-${rng.int(1, 3)}`,
    });
  }
  if (rng.chance(0.3)) {
    parts.push({
      kind: "attachment",
      attachmentId: `att-${rng.int(1, 4)}`,
      filename: `notes-${rng.int(1, 9)}.md`,
      byteSize: rng.int(10, 5000),
    });
  }
  if (rng.chance(0.3)) {
    parts.push({
      kind: "artifact",
      artifactId: `art-${rng.int(1, 4)}`,
      title: `report-${rng.int(1, 9)}`,
      artifactKind: rng.pick(["file", "chart", "summary"]),
    });
  }
  if (rng.chance(0.35)) {
    parts.push({ kind: "warning", code: rng.pick(WARNING_CODES), message: "bounded warning" });
  }
  const hasText = rng.chance(0.85);
  if (hasText) parts.push({ kind: "text", fragments: fragments(rng, 5) });

  let result: ResultPlan | null = null;
  if (hasText ? rng.chance(0.55) : rng.chance(0.12)) {
    result = {
      schemaId: "fleet.answer.v1",
      schemaVersion: "1",
      value: rng.chance(0.5) ? { answer: `final-${rng.int(0, 9)}` } : jsonSnippet(rng),
    };
  }
  // Trailing narrative only makes semantic sense when an answer stream exists
  // for the live route to append to.
  const trailingNarrative = hasText && result && rng.chance(0.3) ? fragments(rng, 2) : [];
  const usage: UsagePlan | null = rng.chance(0.85)
    ? {
        iterations: stepCount,
        durationMs: rng.int(100, 90_000),
        usage: { iterations: stepCount, duration_ms: rng.int(100, 90_000) },
      }
    : null;
  const cancelled = rng.chance(0.12);
  return {
    seed,
    runId: `run-${seed}`,
    parts,
    result,
    trailingNarrative,
    usage,
    finishReason: rng.pick(["stop", "stop", "length", "error"] as const),
    cancelled,
  };
}

// ---------------------------------------------------------------------------
// Live-normalized rendering (fragmented start/delta/end streaming)
// ---------------------------------------------------------------------------

function livePartEvents(part: PartPlan): CanonicalEvent[] {
  switch (part.kind) {
    case "reasoning": {
      const streamId = `reasoning-${part.step}`;
      return [
        { type: "reasoning", streamId, step: 0, text: "", final: false },
        ...part.fragments.map(
          (text): CanonicalEvent => ({ type: "reasoning", streamId, step: 0, text, final: false }),
        ),
        { type: "reasoning", streamId, step: 0, text: "", final: true },
      ];
    }
    case "code":
      return part.fragments.map(
        (codeDelta, index): CanonicalEvent => ({
          type: "code",
          streamId: String(part.step),
          step: part.step,
          codeDelta,
          isDelta: index > 0,
          final: index === part.fragments.length - 1,
        }),
      );
    case "output":
      return part.fragments.map(
        (outputDelta, index): CanonicalEvent => ({
          type: "output",
          streamId: String(part.step),
          step: part.step,
          outputDelta,
          isDelta: index > 0,
          final: index === part.fragments.length - 1,
        }),
      );
    case "text": {
      const streamId = "answer";
      return [
        { type: "text", streamId, textDelta: "", final: false, role: "assistant" },
        ...part.fragments.map(
          (textDelta): CanonicalEvent => ({
            type: "text",
            streamId,
            textDelta,
            final: false,
            role: "assistant",
          }),
        ),
        { type: "text", streamId, textDelta: "", final: true, role: "assistant" },
      ];
    }
    case "tool":
      return [
        {
          type: "tool_call",
          toolCallId: part.toolCallId,
          toolName: part.toolName,
          input: part.input,
        },
        part.error !== undefined
          ? { type: "tool_result", toolCallId: part.toolCallId, error: part.error }
          : { type: "tool_result", toolCallId: part.toolCallId, output: part.output },
      ];
    case "skill":
      return [
        {
          type: "skill",
          streamId: part.skillId,
          messageId: part.skillId,
          skillId: part.skillId,
          phase: "activated",
          name: part.name,
        },
      ];
    case "attachment":
      return [
        {
          type: "attachment",
          streamId: part.attachmentId,
          messageId: part.attachmentId,
          attachmentId: part.attachmentId,
          phase: "staged",
          filename: part.filename,
          byteSize: part.byteSize,
        },
      ];
    case "artifact":
      return [
        {
          type: "artifact",
          streamId: part.artifactId,
          messageId: part.artifactId,
          artifactId: part.artifactId,
          artifactKind: part.artifactKind,
          title: part.title,
          byteSize: null,
        },
      ];
    case "warning":
      return [
        {
          type: "warning",
          streamId: `warning-${part.code}`,
          messageId: `warning-${part.code}`,
          code: part.code,
          message: part.message,
        },
      ];
  }
}

/** Order-preserving deterministic shuffle-merge across stream event lists. */
export function shuffleMerge(lists: CanonicalEvent[][], rng: Rng): CanonicalEvent[] {
  const heads = lists.map(() => 0);
  const out: CanonicalEvent[] = [];
  for (;;) {
    const open = heads
      .map((head, index) => [head, index] as const)
      .filter(([head, index]) => head < (lists[index]?.length ?? 0));
    if (open.length === 0) return out;
    const [head, index] = open[rng.int(0, open.length - 1)] as readonly [number, number];
    const event = lists[index]?.[head];
    if (event !== undefined) out.push(event);
    heads[index] = head + 1;
  }
}

export function renderLiveEvents(model: TurnModel): CanonicalEvent[] {
  const rng = new Rng(model.seed ^ 0x5bd1e995);
  const events: CanonicalEvent[] = [{ type: "turn_start", runId: model.runId, delivery: "live" }];
  let index = 0;
  while (index < model.parts.length) {
    const part = model.parts[index];
    if (part === undefined) break;
    const streamLike =
      part.kind === "reasoning" ||
      part.kind === "code" ||
      part.kind === "output" ||
      part.kind === "text";
    // Adjacent stream blocks are merged 40% of the time to exercise valid
    // interleavings across independent stream identities.
    const next = model.parts[index + 1];
    const nextStreamLike =
      next &&
      (next.kind === "reasoning" ||
        next.kind === "code" ||
        next.kind === "output" ||
        next.kind === "text");
    if (streamLike && nextStreamLike && rng.chance(0.4)) {
      events.push(...shuffleMerge([livePartEvents(part), livePartEvents(next)], rng));
      index += 2;
      continue;
    }
    events.push(...livePartEvents(part));
    index += 1;
  }
  if (model.result) {
    events.push({
      type: "structured_result",
      streamId: "result",
      schemaId: model.result.schemaId,
      schemaVersion: model.result.schemaVersion,
      value: model.result.value,
    });
  }
  for (const textDelta of model.trailingNarrative) {
    events.push({ type: "text", streamId: "answer", textDelta, final: true, role: "assistant" });
  }
  if (model.usage) {
    events.push({
      type: "usage",
      streamId: "usage-1",
      messageId: "usage-1",
      iterations: model.usage.iterations,
      durationMs: model.usage.durationMs,
      usage: model.usage.usage,
    });
  }
  if (model.cancelled) {
    events.push({ type: "turn_cancelled", reason: "operator cancel" });
  } else {
    if (model.finishReason === "error") {
      events.push({ type: "error", text: "stream exploded" });
    }
    events.push({
      type: "turn_finish",
      finishReason: model.finishReason,
      durationMs: model.usage?.durationMs ?? null,
      checkpointVersion: null,
    });
  }
  return events;
}

// ---------------------------------------------------------------------------
// Durable-normalized rendering (single-shot, terminally folded)
// ---------------------------------------------------------------------------

/** The complete assistant narrative collected across text parts (fold order). */
export function foldedNarrative(model: TurnModel): string {
  const fragments: string[] = [];
  for (const part of model.parts) {
    if (part.kind === "text") fragments.push(...part.fragments);
  }
  fragments.push(...model.trailingNarrative);
  return fragments.join("");
}

export function renderDurableEvents(model: TurnModel): CanonicalEvent[] {
  const events: CanonicalEvent[] = [{ type: "turn_context", runId: model.runId }];
  let index = 0;
  const nextId = () => {
    index += 1;
    return `d-${model.runId}-${index}`;
  };
  for (const part of model.parts) {
    const messageId = nextId();
    switch (part.kind) {
      case "reasoning":
        events.push({
          type: "reasoning",
          streamId: messageId,
          messageId,
          step: part.step,
          text: part.fragments.join(""),
          final: true,
        });
        break;
      case "code":
        events.push({
          type: "code",
          streamId: messageId,
          messageId,
          step: part.step,
          codeDelta: part.fragments.join(""),
          isDelta: false,
          final: true,
        });
        break;
      case "output":
        events.push({
          type: "output",
          streamId: messageId,
          messageId,
          step: part.step,
          outputDelta: part.fragments.join(""),
          isDelta: false,
          final: true,
        });
        break;
      case "text":
        // With a structured result the narrative folds onto the result card;
        // without one each text part becomes its own settled message.
        if (!model.result) {
          events.push({
            type: "text",
            streamId: messageId,
            messageId,
            textDelta: part.fragments.join(""),
            final: true,
            role: "assistant",
          });
        }
        break;
      case "tool":
        events.push({
          type: "tool_call",
          toolCallId: part.toolCallId,
          toolName: part.toolName,
          input: part.input,
          messageId,
        });
        events.push(
          part.error !== undefined
            ? { type: "tool_result", toolCallId: part.toolCallId, error: part.error, messageId }
            : { type: "tool_result", toolCallId: part.toolCallId, output: part.output, messageId },
        );
        break;
      case "skill":
        events.push({
          type: "skill",
          streamId: messageId,
          messageId,
          skillId: part.skillId,
          phase: "activated",
          name: part.name,
        });
        break;
      case "attachment":
        events.push({
          type: "attachment",
          streamId: messageId,
          messageId,
          attachmentId: part.attachmentId,
          phase: "staged",
          filename: part.filename,
          byteSize: part.byteSize,
        });
        break;
      case "artifact":
        events.push({
          type: "artifact",
          streamId: messageId,
          messageId,
          artifactId: part.artifactId,
          artifactKind: part.artifactKind,
          title: part.title,
          byteSize: null,
        });
        break;
      case "warning":
        events.push({
          type: "warning",
          streamId: messageId,
          messageId,
          code: part.code,
          message: part.message,
        });
        break;
    }
  }
  if (model.result) {
    const messageId = nextId();
    const narrative = foldedNarrative(model);
    events.push({
      type: "structured_result",
      streamId: messageId,
      messageId,
      schemaId: model.result.schemaId,
      schemaVersion: model.result.schemaVersion,
      value: model.result.value,
      ...(narrative ? { narrativeText: narrative } : {}),
    });
  }
  if (model.usage) {
    const messageId = nextId();
    events.push({
      type: "usage",
      streamId: messageId,
      messageId,
      iterations: model.usage.iterations,
      durationMs: model.usage.durationMs,
      usage: model.usage.usage,
    });
  }
  return events;
}

/** Compact, replayable dump attached to every invariant failure. */
export function dumpModel(model: TurnModel): string {
  return JSON.stringify(model);
}
