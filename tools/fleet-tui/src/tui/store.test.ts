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
  });

  it("appends a user message and an empty assistant bubble on submit", () => {
    const store = makeStore();
    store.dispatch({ type: "user/submit", text: "hello" });
    const state = store.getState();
    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]?.kind).toBe("text");
    if (state.messages[0]?.kind === "text") {
      expect(state.messages[0].role).toBe("user");
      expect(state.messages[0].text).toBe("hello");
    }
    expect(state.messages[1]?.kind).toBe("text");
    if (state.messages[1]?.kind === "text") {
      expect(state.messages[1].role).toBe("assistant");
      expect(state.messages[1].streaming).toBe(true);
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

  it("flags run as error when finish carries an error", () => {
    const store = makeStore();
    store.dispatch({ type: "run/start", runId: "r-1", model: null });
    store.dispatch({ type: "run/finish", finishReason: "error", error: "boom" });
    expect(store.getState().run.phase).toBe("error");
    expect(store.getState().run.error).toBe("boom");
  });

  it("toggles expanded ids", () => {
    const store = makeStore();
    store.dispatch({ type: "message/toggle-expanded", id: "x" });
    expect(store.getState().expandedIds.has("x")).toBe(true);
    store.dispatch({ type: "message/toggle-expanded", id: "x" });
    expect(store.getState().expandedIds.has("x")).toBe(false);
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
  });

  it("auto-expands toggleable messages and tracks tool metrics", () => {
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
    expect(state.expandedIds.has("tool-1")).toBe(true);
    expect(state.run.toolCount).toBe(1);
  });

  it("auto-expands each operator timeline variant and counts outputs once", () => {
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

    expect([...store.getState().expandedIds]).toEqual(["reason", "code", "output", "result"]);
    expect(store.getState().run.completedSteps).toBe(1);
  });
});
