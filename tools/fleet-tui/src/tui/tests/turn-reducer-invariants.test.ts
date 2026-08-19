/**
 * P32/QRE-194: canonical `TurnEventReducer` invariant proofs.
 *
 * Deterministic generated corpus (fixed seeds) + hand-authored regressions
 * assert replay determinism, live/durable semantic parity, accumulation and
 * replacement rules, tool lifecycle exclusivity, and terminal closure —
 * without changing production reducer behavior.
 */

import { describe, expect, it } from "vitest";

import type { CanonicalEvent } from "../canonical.js";
import { serializeCanonicalEvent } from "../canonical.js";
import { normalizedNarrative } from "../projection-helpers.js";
import type { Message, StoreEvent } from "../store.js";
import { TurnEventReducer } from "../turn-reducer.js";
import {
  dumpModel,
  foldedNarrative,
  generateTurnModel,
  renderDurableEvents,
  renderLiveEvents,
  type PartPlan,
  type TurnModel,
} from "./reducer-sequence-gen.js";

/** Fixed seed corpus: CI is deterministic and every failure reports a seed. */
const CORPUS_SEEDS = [
  11, 42, 77, 101, 137, 211, 307, 401, 509, 613, 727, 823, 929, 1031, 1223, 1327, 1487, 1601, 1753,
  1871, 1999, 2113, 2221, 2371, 2503, 2671,
] as const;

const clock = () => 100;

/** Non-null-assertion-free single-element access; fails loudly when absent. */
function sole<T>(items: readonly T[], what: string): T {
  if (items.length !== 1) throw new Error(`expected exactly one ${what}, found ${items.length}`);
  const [value] = items;
  if (value === undefined) throw new Error(`missing ${what}`);
  return value;
}

function reduceAll(events: CanonicalEvent[]): { store: StoreEvent[]; messages: Message[] } {
  const reducer = new TurnEventReducer(clock);
  const store: StoreEvent[] = [];
  const byId = new Map<string, Message>();
  for (const event of events) {
    for (const emitted of reducer.push(event)) {
      store.push(emitted);
      if (emitted.type === "message/upsert") byId.set(emitted.message.id, emitted.message);
    }
  }
  return { store, messages: [...byId.values()] };
}

/** Semantic projection: positional ids and clocks are adapter-owned. */
function semanticView(messages: Message[]): Record<string, unknown>[] {
  return messages.map((message) => {
    const { id: _id, ts: _ts, ...rest } = message as Message & { runId?: string };
    const { runId: _runId, ...visible } = rest;
    return visible as Record<string, unknown>;
  });
}

function family(messages: Message[], kind: Message["kind"]): Message[] {
  return messages.filter((message) => message.kind === kind);
}

function byKind(messages: Message[]): Map<string, Message[]> {
  const grouped = new Map<string, Message[]>();
  for (const message of messages) {
    const list = grouped.get(message.kind) ?? [];
    list.push(message);
    grouped.set(message.kind, list);
  }
  return grouped;
}

function expectSemanticParity(live: Message[], durable: Message[], context: () => string): void {
  expect(durable.map((m) => m.kind).sort(), context()).toEqual(live.map((m) => m.kind).sort());
  const liveByKind = byKind(live);
  const durableByKind = byKind(durable);
  for (const [kind, liveFamily] of liveByKind) {
    expect(semanticView(durableByKind.get(kind) ?? []), `${context()} kind=${kind}`).toEqual(
      semanticView(liveFamily),
    );
  }
}

function expectAccumulation(model: TurnModel, messages: Message[]): void {
  const reasoningByStep = new Map<number, string>();
  for (const part of model.parts) {
    if (part.kind === "reasoning") {
      reasoningByStep.set(
        part.step,
        (reasoningByStep.get(part.step) ?? "") + part.fragments.join(""),
      );
    }
  }
  const reasoningCards = family(messages, "reasoning");
  expect(reasoningCards.length, "one reasoning card per streamed step").toBe(reasoningByStep.size);
  for (const card of reasoningCards) {
    if (card.kind === "reasoning") {
      expect(card.text, `reasoning step ${card.step} accumulates without duplication`).toBe(
        reasoningByStep.get(card.step),
      );
    }
  }
  for (const part of model.parts) {
    if (part.kind === "code") {
      const card = messages.find(
        (message) => message.kind === "code" && message.step === part.step,
      );
      expect(
        card && card.kind === "code" ? card.code : undefined,
        "code folds without duplication",
      ).toBe(part.fragments.join(""));
    }
    if (part.kind === "output") {
      const card = messages.find(
        (message) => message.kind === "output" && message.step === part.step,
      );
      expect(
        card && card.kind === "output" ? card.output : undefined,
        "output folds without duplication",
      ).toBe(part.fragments.join(""));
    }
  }
}

function expectToolPairing(model: TurnModel, messages: Message[]): void {
  const plans = model.parts.filter(
    (part): part is PartPlan & { kind: "tool" } => part.kind === "tool",
  );
  const tools = family(messages, "tool");
  expect(tools.length, "one tool card per invocation identity").toBe(plans.length);
  const byToolCallId = new Map<string, Message>(
    tools.map((message) => [message.kind === "tool" ? message.toolCallId : "", message]),
  );
  expect(byToolCallId.size).toBe(plans.length);
  for (const plan of plans) {
    const card = byToolCallId.get(plan.toolCallId);
    if (!card || card.kind !== "tool") throw new Error(`missing tool card ${plan.toolCallId}`);
    expect(card.name).toBe(plan.toolName);
    expect(card.input).toEqual(plan.input);
    // Terminal states are mutually exclusive and paired to one invocation.
    expect(card.output !== undefined).toBe(plan.output !== undefined);
    expect(card.error !== undefined).toBe(plan.error !== undefined);
    expect(card.status).toBe(plan.error !== undefined ? "error" : "success");
    expect(card.endedAt).toBeDefined();
  }
}

function expectResultSemantics(model: TurnModel, messages: Message[]): void {
  const results = family(messages, "result");
  const texts = family(messages, "text");
  if (!model.result) {
    expect(results, "no structured result, no result card").toHaveLength(0);
    const textParts = model.parts.filter((part) => part.kind === "text");
    expect(texts.length).toBe(textParts.length);
    return;
  }
  expect(results, "structured result replaces narrative with one final card").toHaveLength(1);
  expect(texts, "no orphan text card survives replacement").toHaveLength(0);
  const card = sole(results, "result card");
  if (card.kind === "result") {
    expect(card.schemaId).toBe(model.result.schemaId);
    expect(card.schemaVersion).toBe(model.result.schemaVersion);
    expect(card.value).toEqual(model.result.value);
    expect(card.narrative).toEqual(normalizedNarrative(foldedNarrative(model), model.result.value));
  }
}

function expectTerminalClosure(messages: Message[]): void {
  for (const message of messages) {
    if (message.kind === "text" || message.kind === "code" || message.kind === "output") {
      expect(message.streaming, `${message.kind} card closed at turn end`).toBe(false);
    }
  }
}

function expectPrimitiveIdentityOrder(model: TurnModel, a: Message[], b: Message[]): void {
  const identity = (message: Message): string => {
    switch (message.kind) {
      case "artifact":
        return `artifact:${message.artifactId}:${message.name}`;
      case "attachment":
        return `attachment:${message.attachmentId}:${message.filename}`;
      case "skill":
        return `skill:${message.skillId}:${message.name}`;
      case "warning":
        return `warning:${message.code}:${message.message}`;
      default:
        return message.kind;
    }
  };
  for (const kind of ["artifact", "attachment", "skill", "warning"] as const) {
    const one = family(a, kind).map(identity);
    const two = family(b, kind).map(identity);
    expect(two, `${kind} identities keep stable order under both routes`).toEqual(one);
  }
}

/** Attach a replayable report (seed + compact model + both sequences) to any failure. */
function prove(model: TurnModel, invariant: () => void): void {
  const live = renderLiveEvents(model);
  const durable = renderDurableEvents(model);
  try {
    invariant();
  } catch (error) {
    const liveDump = live.map((event) => JSON.stringify(serializeCanonicalEvent(event))).join("\n");
    const durableDump = durable
      .map((event) => JSON.stringify(serializeCanonicalEvent(event)))
      .join("\n");
    const cause = error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : String(error);
    throw new Error(
      `P32 invariant failed (seed=${model.seed}); re-run with CORPUS_SEEDS=[${model.seed}]\n` +
        `model=${dumpModel(model)}\nlive=\n${liveDump}\ndurable=\n${durableDump}\ncause=${cause}`,
    );
  }
}

describe("turn reducer generated invariants", () => {
  for (const seed of CORPUS_SEEDS) {
    describe(`seed ${seed}`, () => {
      const model = generateTurnModel(seed);

      it("reduces the same sequence to a deep-equal final state (replay determinism)", () => {
        prove(model, () => {
          const live = renderLiveEvents(model);
          const first = reduceAll(live);
          const second = reduceAll(live);
          expect(second.messages).toEqual(first.messages);
          expect(second.store).toEqual(first.store);
          const durable = renderDurableEvents(model);
          expect(reduceAll(durable).messages).toEqual(reduceAll(durable).messages);
        });
      });

      it("live-normalized and durable-normalized turns converge semantically", () => {
        prove(model, () => {
          const live = reduceAll(renderLiveEvents(model)).messages;
          const durable = reduceAll(renderDurableEvents(model)).messages.filter(
            (message) => message.kind !== "error",
          );
          expectSemanticParity(
            live.filter((message) => message.kind !== "error"),
            durable,
            () => `seed=${model.seed} kind parity`,
          );
          expectPrimitiveIdentityOrder(model, live, durable);
        });
      });

      it("accumulates text/reasoning/code/output without duplication", () => {
        prove(model, () => {
          expectAccumulation(model, reduceAll(renderLiveEvents(model)).messages);
        });
      });

      it("keeps tool terminal states exclusive and paired to one invocation", () => {
        prove(model, () => {
          expectToolPairing(model, reduceAll(renderLiveEvents(model)).messages);
          expectToolPairing(model, reduceAll(renderDurableEvents(model)).messages);
        });
      });

      it("yields at most one final structured-result card", () => {
        prove(model, () => {
          expectResultSemantics(model, reduceAll(renderLiveEvents(model)).messages);
          expectResultSemantics(model, reduceAll(renderDurableEvents(model)).messages);
        });
      });

      it("closes every streaming card at terminal state", () => {
        prove(model, () => {
          expectTerminalClosure(reduceAll(renderLiveEvents(model)).messages);
          expectTerminalClosure(reduceAll(renderDurableEvents(model)).messages);
        });
      });

      it("keeps usage and execution summary parity", () => {
        if (!model.usage) return;
        prove(model, () => {
          for (const events of [renderLiveEvents(model), renderDurableEvents(model)]) {
            const usageCards = family(reduceAll(events).messages, "usage");
            expect(usageCards).toHaveLength(1);
            const card = sole(usageCards, "usage card");
            const usagePlan = model.usage;
            if (usagePlan && card.kind === "usage") {
              expect(card.iterations).toBe(usagePlan.iterations);
              expect(card.executionSummary?.iterations).toBe(usagePlan.iterations);
            }
          }
        });
      });

      it("replay in one reducer upserts stable ids without duplicating content", () => {
        prove(model, () => {
          const reducer = new TurnEventReducer(clock);
          const byId = new Map<string, Message>();
          for (let round = 0; round < 2; round += 1) {
            for (const event of renderDurableEvents(model)) {
              for (const emitted of reducer.push(event)) {
                if (emitted.type === "message/upsert")
                  byId.set(emitted.message.id, emitted.message);
              }
            }
          }
          const messages = [...byId.values()];
          // Upsert-stable kinds must not duplicate under replay.
          expect(family(messages, "reasoning")).toHaveLength(
            family(reduceAll(renderDurableEvents(model)).messages, "reasoning").length,
          );
          expect(family(messages, "code")).toHaveLength(
            family(reduceAll(renderDurableEvents(model)).messages, "code").length,
          );
          expect(family(messages, "result")).toHaveLength(
            family(reduceAll(renderDurableEvents(model)).messages, "result").length,
          );
        });
      });
    });
  }
});

describe("turn reducer defensive invalid-input handling", () => {
  it("ignores a reasoning end marker with no open stream", () => {
    const reducer = new TurnEventReducer(clock);
    const events = reducer.push({
      type: "reasoning",
      streamId: "ghost-1",
      step: 0,
      text: "",
      final: true,
    });
    expect(events).toEqual([]);
  });

  it("mints a settled tool card for a result without a known call", () => {
    const reducer = new TurnEventReducer(clock);
    reducer.push({ type: "turn_start", runId: "r1", delivery: "live" });
    const out = reduceAll([
      { type: "turn_start", runId: "r1", delivery: "live" },
      { type: "tool_result", toolCallId: "orphan-1", output: { ok: true } },
    ]);
    const tools = family(out.messages, "tool");
    expect(tools).toHaveLength(1);
    const card = sole(tools, "tool card");
    if (card.kind === "tool") {
      expect(card.toolCallId).toBe("orphan-1");
      expect(card.status).toBe("success");
      expect(card.output).toEqual({ ok: true });
    }
    void reducer;
  });

  it("keeps duplicate provided artifact occurrences as two distinct acts", () => {
    const byId = reduceAll([
      { type: "turn_start", runId: "r1", delivery: "live" },
      {
        type: "artifact",
        streamId: "artifact-1",
        messageId: "artifact-1",
        artifactId: "a1",
        title: "one",
      },
      {
        type: "artifact",
        streamId: "artifact-1",
        messageId: "artifact-1",
        artifactId: "a1",
        title: "one",
      },
    ]);
    expect(family(byId.messages, "artifact")).toHaveLength(2);
  });
});

describe("turn reducer regression fixtures", () => {
  it("reopens a reasoning card when its :canonical twin arrives with corrected text", () => {
    const { messages } = reduceAll([
      { type: "turn_start", runId: "r1", delivery: "live" },
      { type: "reasoning", streamId: "reasoning-3", step: 0, text: "", final: false },
      { type: "reasoning", streamId: "reasoning-3", step: 0, text: "stale ", final: false },
      { type: "reasoning", streamId: "reasoning-3", step: 0, text: "", final: true },
      { type: "reasoning", streamId: "reasoning-3:canonical", step: 3, text: "", final: false },
      {
        type: "reasoning",
        streamId: "reasoning-3:canonical",
        step: 3,
        text: "corrected ",
        final: false,
      },
      { type: "reasoning", streamId: "reasoning-3:canonical", step: 3, text: "text", final: true },
    ]);
    const reasoningCards = family(messages, "reasoning");
    expect(reasoningCards).toHaveLength(1);
    const card = sole(reasoningCards, "reasoning card");
    if (card.kind === "reasoning") {
      // The twin's fresh start marker cleared the stale stream, and its final
      // full-text fold settled the card.
      expect(card.text).toBe("text");
      expect(card.step).toBe(3);
    }
  });

  it("lets a durable single-shot final fold replace accumulated reasoning text", () => {
    const { messages } = reduceAll([
      { type: "turn_start", runId: "r1", delivery: "live" },
      { type: "reasoning", streamId: "reasoning-4", step: 0, text: "", final: false },
      { type: "reasoning", streamId: "reasoning-4", step: 0, text: "fragment ", final: false },
      { type: "reasoning", streamId: "reasoning-4", step: 0, text: "accumulation", final: false },
      {
        type: "reasoning",
        streamId: "reasoning-4",
        step: 4,
        text: "settled full text",
        final: true,
      },
    ]);
    const card = sole(family(messages, "reasoning"), "reasoning card");
    if (card.kind === "reasoning") {
      expect(card.text).toBe("settled full text");
      expect(card.step).toBe(4);
    }
  });

  it("replaces the streamed text card with one structured result card", () => {
    const { messages } = reduceAll([
      { type: "turn_start", runId: "r1", delivery: "live" },
      { type: "text", streamId: "answer", textDelta: "", final: false, role: "assistant" },
      {
        type: "text",
        streamId: "answer",
        textDelta: "The answer is 42.",
        final: false,
        role: "assistant",
      },
      { type: "text", streamId: "answer", textDelta: "", final: true, role: "assistant" },
      {
        type: "structured_result",
        streamId: "result",
        schemaId: "s",
        schemaVersion: "1",
        value: { answer: "42" },
      },
    ]);
    expect(family(messages, "text")).toHaveLength(0);
    const results = family(messages, "result");
    expect(results).toHaveLength(1);
    const card = sole(results, "result card");
    if (card.kind === "result") {
      expect(card.narrative).toBe("The answer is 42.");
    }
  });

  it("appends post-result narrative deltas to the result card", () => {
    const { messages } = reduceAll([
      { type: "turn_start", runId: "r1", delivery: "live" },
      { type: "text", streamId: "answer", textDelta: "base ", final: false, role: "assistant" },
      {
        type: "structured_result",
        streamId: "result",
        schemaId: "s",
        schemaVersion: "1",
        value: { answer: "x" },
      },
      { type: "text", streamId: "answer", textDelta: "trailing", final: true, role: "assistant" },
    ]);
    const results = family(messages, "result");
    expect(results).toHaveLength(1);
    const card = sole(results, "result card");
    if (card.kind === "result") {
      expect(card.narrative).toBe("base trailing");
    }
  });

  it("carries the stream error onto an error finish without minting finish cards", () => {
    const { store } = reduceAll([
      { type: "turn_start", runId: "r1", delivery: "live" },
      { type: "error", text: "provider exploded" },
      { type: "turn_finish", finishReason: "error", durationMs: 12, checkpointVersion: null },
    ]);
    const finish = store.find((event) => event.type === "run/finish");
    expect(finish && finish.type === "run/finish" ? finish.error : undefined).toBe(
      "provider exploded",
    );
  });

  it("cancels without a run/finish event", () => {
    const { store } = reduceAll([
      { type: "turn_start", runId: "r1", delivery: "live" },
      { type: "turn_cancelled", reason: "operator cancel" },
    ]);
    expect(store.some((event) => event.type === "run/finish")).toBe(false);
    expect(store.some((event) => event.type === "run/cancelled")).toBe(true);
  });
});
