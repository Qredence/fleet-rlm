import { describe, expect, it } from "vitest";

import { ConversationStore } from "./store.js";

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

  it("marks run finished with the supplied finish reason", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", model: "openai/gpt" });
    store.dispatch({ type: "run/finish", finishReason: "stop", error: null });
    const state = store.getState();
    expect(state.run.phase).toBe("completed");
    expect(state.run.finishReason).toBe("stop");
    expect(state.run.error).toBeNull();
  });

  it("keeps transient status in Run state and clears it on terminal", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", model: null });
    store.dispatch({ type: "run/status", phase: "execution", detail: "running" });

    expect(store.getState().run).toMatchObject({
      statusPhase: "execution",
      statusDetail: "running",
    });
    expect(store.getState().messages).toEqual([]);

    store.dispatch({ type: "run/finish", finishReason: "stop", error: null });

    expect(store.getState().run.statusPhase).toBeNull();
    expect(store.getState().run.statusDetail).toBeNull();
  });

  it("flags run as error when finish carries an error", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", model: null });
    store.dispatch({ type: "run/finish", finishReason: "error", error: "boom" });
    expect(store.getState().run.phase).toBe("error");
    expect(store.getState().run.error).toBe("boom");
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
    store.dispatch({ type: "run/start", runId: "old-run", model: null });
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

  it("retains each operator timeline variant and counts outputs once", () => {
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
    expect(store.getState().run.completedSteps).toBe(1);
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
    expect(store.getState().run.completedSteps).toBe(1);
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
