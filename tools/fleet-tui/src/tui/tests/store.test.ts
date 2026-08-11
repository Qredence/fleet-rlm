import { describe, expect, it } from "vitest";

import { ConversationStore } from "../store.js";

function makeStore(): ConversationStore {
  return new ConversationStore();
}

describe("ConversationStore", () => {
  it("starts idle with no messages", () => {
    const store = makeStore();
    const state = store.getState();
    expect(state.run.phase).toBe("idle");
    expect(state.messages).toHaveLength(0);
    expect(state.pendingSkillSelections).toEqual([]);
  });

  it("appends only the user message on submit", () => {
    const store = makeStore();
    store.dispatch({ type: "user/submit", text: "hello" });
    const state = store.getState();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]?.kind).toBe("text");
    if (state.messages[0]?.kind === "text") {
      expect(state.messages[0].role).toBe("user");
      expect(state.messages[0].text).toBe("hello");
    }
  });

  it("resets prior Run metrics when a new Turn starts preparing", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "old", delivery: "replay" });
    store.dispatch({ type: "run/step-start" });
    store.dispatch({ type: "run/step-finish" });
    store.dispatch({
      type: "run/finish",
      finishReason: "stop",
      error: null,
      durationMs: 10,
      checkpointVersion: 2,
    });

    store.dispatch({ type: "user/submit", text: "next" });

    expect(store.getState().run).toMatchObject({
      id: null,
      phase: "submitting",
      delivery: null,
      outcome: null,
      startedSteps: 0,
      completedSteps: 0,
      durationMs: null,
      checkpointVersion: null,
    });
  });

  it("marks run finished with the supplied finish reason", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", delivery: "live" });
    store.dispatch({
      type: "run/finish",
      finishReason: "stop",
      error: null,
      durationMs: 1200,
      checkpointVersion: 3,
    });
    const state = store.getState();
    expect(state.run.phase).toBe("completed");
    expect(state.run.outcome).toBe("completed");
    expect(state.run.delivery).toBe("live");
    expect(state.run.finishReason).toBe("stop");
    expect(state.run.error).toBeNull();
    expect(state.run.durationMs).toBe(1200);
    expect(state.run.checkpointVersion).toBe(3);
  });

  it("settles live text, code, and output when any Run terminal path arrives", () => {
    const terminalEvents = [
      {
        type: "run/finish" as const,
        finishReason: "stop",
        error: null,
        durationMs: null,
        checkpointVersion: null,
      },
      { type: "run/cancelled" as const, reason: "Cancelled by operator" },
      { type: "run/interrupted" as const, error: "Connection lost" },
    ];

    for (const terminal of terminalEvents) {
      const store = makeStore();
      store.dispatch({ type: "run/start", runId: "r-1", delivery: "live" });
      store.dispatch({
        type: "message/upsert",
        message: {
          id: "text",
          kind: "text",
          role: "assistant",
          runId: "r-1",
          text: "partial",
          streaming: true,
          ts: 1,
        },
      });
      store.dispatch({
        type: "message/upsert",
        message: {
          id: "code",
          kind: "code",
          runId: "r-1",
          step: 1,
          code: "partial",
          streaming: true,
          ts: 2,
        },
      });
      store.dispatch({
        type: "message/upsert",
        message: {
          id: "output",
          kind: "output",
          runId: "r-1",
          step: 1,
          output: "partial",
          streaming: true,
          ts: 3,
        },
      });

      store.dispatch(terminal);

      expect(store.getState().messages).toMatchObject([
        { id: "text", streaming: false },
        { id: "code", streaming: false },
        { id: "output", streaming: false },
      ]);
    }
  });

  it("counts steps only from the SSE step lifecycle", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", delivery: "live" });
    store.dispatch({ type: "run/step-start" });
    store.dispatch({
      type: "message/upsert",
      message: { id: "output", kind: "output", runId: "r-1", step: 1, output: "1", ts: 1 },
    });

    expect(store.getState().run).toMatchObject({ startedSteps: 1, completedSteps: 0 });

    store.dispatch({ type: "run/step-finish" });
    expect(store.getState().run).toMatchObject({ startedSteps: 1, completedSteps: 1 });
  });

  it("distinguishes cancellation and transport interruption", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", delivery: "replay" });
    store.dispatch({ type: "run/cancelled", reason: "Cancelled by operator" });
    expect(store.getState().run).toMatchObject({
      phase: "idle",
      outcome: "cancelled",
      delivery: "replay",
      abortReason: "Cancelled by operator",
    });

    store.dispatch({ type: "run/interrupted", error: "Connection lost" });
    expect(store.getState().run).toMatchObject({
      phase: "error",
      outcome: "interrupted",
      error: "Connection lost",
    });
  });

  it("keeps transient status in Run state and clears it on terminal", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", delivery: "live" });
    store.dispatch({ type: "run/status", phase: "execution", detail: "running" });

    expect(store.getState().run).toMatchObject({
      statusPhase: "execution",
      statusDetail: "running",
    });
    expect(store.getState().messages).toEqual([]);

    store.dispatch({
      type: "run/finish",
      finishReason: "stop",
      error: null,
      durationMs: null,
      checkpointVersion: null,
    });

    expect(store.getState().run.statusPhase).toBeNull();
    expect(store.getState().run.statusDetail).toBeNull();
  });

  it("flags run as error when finish carries an error", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", delivery: "live" });
    store.dispatch({
      type: "run/finish",
      finishReason: "error",
      error: "Turn failed because a required workspace update was not completed",
      durationMs: null,
      checkpointVersion: null,
    });
    expect(store.getState().run.phase).toBe("error");
    expect(store.getState().run.outcome).toBe("failed");
    expect(store.getState().run.error).toBe(
      "Turn failed because a required workspace update was not completed",
    );
  });

  it("clears messages but keeps session", () => {
    const store = makeStore();
    store.dispatch({
      type: "session/init",
      session: { id: "s", title: "T", status: "active", resumed: false },
    });
    store.dispatch({ type: "user/submit", text: "hi" });
    store.dispatch({ type: "clear" });
    const state = store.getState();
    expect(state.messages).toHaveLength(0);
    expect(state.session?.id).toBe("s");
  });

  it("atomically replaces the session and complete projected history", () => {
    const store = makeStore();
    store.dispatch({
      type: "session/init",
      session: { id: "old", title: "Old", status: "active", resumed: false },
    });
    store.dispatch({ type: "user/submit", text: "old message" });
    store.dispatch({ type: "run/start", runId: "old-run", delivery: "live" });
    store.dispatch({ type: "run/status", phase: "execution", detail: "running" });
    const message = {
      id: "new:0",
      kind: "text",
      role: "user",
      text: "restored",
      streaming: false,
      ts: 1,
    } as const;

    store.dispatch({
      type: "session/hydrate",
      session: { id: "new", title: "New", status: "active", resumed: true },
      events: [{ type: "message/upsert", message }],
    });

    expect(store.getState().session?.id).toBe("new");
    expect(store.getState().messages).toEqual([message]);
    expect(store.getState().run.phase).toBe("idle");
    expect(store.getState().run.statusPhase).toBeNull();
    expect(store.getState().run.statusDetail).toBeNull();
  });

  it("does not derive transient Run metrics while hydrating durable messages", () => {
    const store = makeStore();
    store.dispatch({
      type: "session/hydrate",
      session: { id: "session-1", title: "Session", status: "active", resumed: true },
      events: [
        {
          type: "message/upsert",
          message: {
            id: "tool",
            kind: "tool",
            runId: "run-1",
            toolCallId: "call-1",
            name: "read",
            input: {},
            startedAt: 1,
            endedAt: 2,
            status: "success",
            output: "ok",
            ts: 1,
          },
        },
      ],
    });

    expect(store.getState().messages).toHaveLength(1);
    expect(store.getState().run).toMatchObject({ phase: "idle", toolCount: 0, outcome: null });
  });

  it("clears local messages and Run metadata without changing Session or pending Skills", () => {
    const store = makeStore();
    store.dispatch({
      type: "session/init",
      session: { id: "session-1", title: "Session", status: "active", resumed: false },
    });
    store.dispatch({
      type: "skill-selection/pin",
      selection: { id: "skill-1", expectedVersion: "1.0.0", displayName: "Skill" },
    });
    store.dispatch({ type: "user/submit", text: "hello" });
    store.dispatch({ type: "run/start", runId: "run-1", delivery: "replay" });
    store.dispatch({ type: "run/step-start" });
    store.dispatch({ type: "run/step-finish" });
    store.dispatch({
      type: "run/finish",
      finishReason: "stop",
      error: null,
      durationMs: 20,
      checkpointVersion: 2,
    });

    store.dispatch({ type: "clear" });

    expect(store.getState()).toMatchObject({
      session: { id: "session-1" },
      messages: [],
      pendingSkillSelections: [{ id: "skill-1" }],
      run: {
        id: null,
        phase: "idle",
        delivery: null,
        outcome: null,
        startedSteps: 0,
        completedSteps: 0,
        toolCount: 0,
        durationMs: null,
        checkpointVersion: null,
      },
    });
  });

  it("pins at most four unique Skills and updates an existing pin in place", () => {
    const store = makeStore();
    for (let index = 1; index <= 5; index += 1) {
      store.dispatch({
        type: "skill-selection/pin",
        selection: {
          id: `skill-${index}`,
          expectedVersion: "1.0.0",
          displayName: `skill-${index}`,
        },
      });
    }
    store.dispatch({
      type: "skill-selection/pin",
      selection: {
        id: "skill-2",
        expectedVersion: "2.0.0",
        displayName: "renamed",
      },
    });

    expect(store.getState().pendingSkillSelections).toHaveLength(4);
    expect(store.getState().pendingSkillSelections[1]).toEqual({
      id: "skill-2",
      expectedVersion: "2.0.0",
      displayName: "renamed",
    });
  });

  it("consumes only accepted Skill versions and retains selections after unrelated failures", () => {
    const store = makeStore();
    const accepted = {
      id: "skill-1",
      expectedVersion: "1.0.0",
      displayName: "one",
    };
    store.dispatch({ type: "skill-selection/pin", selection: accepted });
    store.dispatch({
      type: "skill-selection/pin",
      selection: { id: "skill-2", expectedVersion: "2.0.0", displayName: "two" },
    });

    store.dispatch({
      type: "skill-selection/consume",
      selections: [{ ...accepted, expectedVersion: "0.9.0" }],
    });
    expect(store.getState().pendingSkillSelections).toHaveLength(2);

    store.dispatch({ type: "skill-selection/consume", selections: [accepted] });
    expect(store.getState().pendingSkillSelections).toEqual([
      { id: "skill-2", expectedVersion: "2.0.0", displayName: "two" },
    ]);
  });

  it("keeps replayed Skill cards visible during session hydration", () => {
    const store = makeStore();
    const skillMessage = {
      id: "skill-card",
      kind: "skill",
      runId: "run-1",
      skillId: "skill-1",
      name: "long-context",
      phase: "activated",
      version: "2.0.0",
      trust: "system",
      ts: 1,
    } as const;

    store.dispatch({
      type: "session/hydrate",
      session: { id: "session-1", title: "Session", status: "active", resumed: true },
      events: [{ type: "message/upsert", message: skillMessage }],
    });

    expect(store.getState().messages).toEqual([skillMessage]);
  });

  it("tracks tool metrics", () => {
    const store = makeStore();
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "tool-1",
        kind: "tool",
        runId: "r-1",
        toolCallId: "c-1",
        name: "read_attachment",
        input: {},
        startedAt: Date.now(),
        status: "running",
        ts: Date.now(),
      },
    });
    const state = store.getState();
    expect(state.run.toolCount).toBe(1);
  });

  it("retains each operator timeline variant without inferring step metrics", () => {
    const store = makeStore();
    const messages = [
      { id: "reason", kind: "reasoning", runId: "r", step: 1, text: "think", ts: 1 },
      { id: "code", kind: "code", runId: "r", step: 1, code: "print(1)", ts: 2 },
      { id: "output", kind: "output", runId: "r", step: 1, output: "1", ts: 3 },
      {
        id: "result",
        kind: "result",
        runId: "r",
        schemaId: "answer",
        schemaVersion: "1",
        value: 1,
        ts: 4,
      },
    ] as const;

    for (const message of messages) store.dispatch({ type: "message/upsert", message });
    store.dispatch({ type: "message/upsert", message: messages[2] });

    expect(store.getState().messages.map((message) => message.id)).toEqual([
      "reason",
      "code",
      "output",
      "result",
    ]);
    expect(store.getState().run.completedSteps).toBe(0);
  });

  it("replaces a same-ID trajectory correction without increasing output metrics", () => {
    const store = makeStore();
    store.dispatch({
      type: "message/upsert",
      message: { id: "output-r-1", kind: "output", runId: "r", step: 1, output: "stale", ts: 1 },
    });
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "output-r-1",
        kind: "output",
        runId: "r",
        step: 1,
        output: "canonical",
        ts: 2,
      },
    });

    expect(store.getState().messages).toMatchObject([{ id: "output-r-1", output: "canonical" }]);
    expect(store.getState().run.completedSteps).toBe(0);
  });

  it("inserts late trajectory reasoning before its already-streamed step details", () => {
    const store = makeStore();
    store.dispatch({
      type: "message/upsert",
      message: { id: "code-r-1", kind: "code", runId: "r", step: 1, code: "stale", ts: 1 },
    });
    store.dispatch({
      type: "message/upsert",
      message: { id: "output-r-1", kind: "output", runId: "r", step: 1, output: "stale", ts: 2 },
    });
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "reasoning-r-1",
        kind: "reasoning",
        runId: "r",
        step: 1,
        text: "canonical",
        ts: 3,
      },
    });

    expect(store.getState().messages.map((message) => message.kind)).toEqual([
      "reasoning",
      "code",
      "output",
    ]);
  });
});

describe("pending attachments and lastPrompt", () => {
  it("pins up to eight Attachments and consumes them by id", () => {
    const store = makeStore();
    for (let index = 1; index <= 9; index += 1) {
      store.dispatch({
        type: "attachment/pin",
        attachment: { id: `a-${index}`, filename: `f-${index}.txt`, bytes: index },
      });
    }
    expect(store.getState().pendingAttachments).toHaveLength(8);
    expect(store.getState().pendingAttachments[0]?.id).toBe("a-1");

    store.dispatch({
      type: "attachment/consume",
      attachments: [{ id: "a-1", filename: "f-1.txt", bytes: 1 }],
    });
    expect(store.getState().pendingAttachments.map((a) => a.id)).not.toContain("a-1");

    store.dispatch({ type: "attachment/clear" });
    expect(store.getState().pendingAttachments).toEqual([]);
  });

  it("keeps the last submitted prompt for /redo", () => {
    const store = makeStore();
    store.dispatch({ type: "user/submit", text: "first" });
    store.dispatch({ type: "user/submit", text: "second" });
    expect(store.getState().lastPrompt).toBe("second");

    store.dispatch({ type: "user/prompt-restore", text: "restored" });
    expect(store.getState().lastPrompt).toBe("restored");
  });

  it("clears attachments and lastPrompt state on reset", () => {
    const store = makeStore();
    store.dispatch({
      type: "attachment/pin",
      attachment: { id: "a-1", filename: "f.txt", bytes: 1 },
    });
    store.dispatch({ type: "user/submit", text: "x" });
    store.dispatch({ type: "reset" });
    expect(store.getState().pendingAttachments).toEqual([]);
    expect(store.getState().lastPrompt).toBeNull();
  });
});
