import { describe, expect, it, vi } from "vitest";

import { ConversationStore, type Message } from "../store.js";
import { setTerminalColorScheme } from "../theme.js";
import { TranscriptComponent } from "../transcript.js";

function message(id: string, text: string): Message {
  return { id, kind: "text", role: "assistant", text, streaming: false, ts: 1 };
}

describe("TranscriptComponent", () => {
  it("keeps Fleet identity while applying the pi accent hierarchy", () => {
    setTerminalColorScheme("dark");
    const store = new ConversationStore();
    store.dispatch({
      type: "session/init",
      session: { id: "session-1", title: "Session", status: "active", resumed: false },
    });

    const header = new TranscriptComponent(store).render(80);

    expect(header[0]).toContain("FLEET");
    expect(header[0]).toContain("\x1b[38;");
    expect(header[1]).toContain("Session · new · active");
  });

  it("renders a caller-controlled Session title as one terminal-safe line", () => {
    const store = new ConversationStore();
    store.dispatch({
      type: "session/init",
      session: {
        id: "session-1",
        title: "Unsafe\nTitle\x1b]52;c;secret\x07",
        status: "active",
        resumed: true,
      },
    });

    const header = new TranscriptComponent(store).render(100)[1];

    expect(header).toContain("Unsafe Title · resumed · active");
    expect(header).not.toContain("secret");
    expect(header).not.toContain("\n");
    expect(header).not.toContain("\x1b]52");
    expect(header).not.toContain("\x07");
  });

  it("reuses unchanged historical rendering and invalidates changed messages by width", () => {
    const store = new ConversationStore();
    const render = vi.fn((value: Message, width: number) => [`${value.id}:${width}`]);
    store.dispatch({
      type: "session/init",
      session: { id: "session-1", title: "Session", status: "active", resumed: false },
    });
    store.dispatch({ type: "message/upsert", message: message("one", "one") });
    store.dispatch({ type: "message/upsert", message: message("two", "two") });
    const transcript = new TranscriptComponent(store, render);

    transcript.render(80);
    for (let index = 0; index < 300; index += 1) {
      store.dispatch({ type: "run/status", phase: "execution", detail: `working-${index}` });
      transcript.render(80);
    }
    expect(render).toHaveBeenCalledTimes(2);

    store.dispatch({ type: "message/upsert", message: message("two", "updated") });
    transcript.render(80);
    expect(render).toHaveBeenCalledTimes(3);

    transcript.render(100);
    expect(render).toHaveBeenCalledTimes(5);
  });

  it("invalidates cached message colors after a terminal scheme change", () => {
    const store = new ConversationStore();
    store.dispatch({ type: "message/upsert", message: message("one", "styled") });
    const transcript = new TranscriptComponent(store);
    setTerminalColorScheme("dark");
    const dark = transcript.render(80).join("\n");

    setTerminalColorScheme("light");
    transcript.invalidate();
    const light = transcript.render(80).join("\n");

    expect(light).not.toBe(dark);
    expect(light).toContain("styled");
  });

  it("rerenders active streaming evidence through one render cache", () => {
    const store = new ConversationStore();
    const caches: unknown[] = [];
    const render = vi.fn((value: Message, _width: number, cache: unknown) => {
      caches.push(cache);
      return [value.id];
    });
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "text",
        kind: "text",
        role: "assistant",
        text: "live",
        streaming: true,
        ts: 1,
      },
    });
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "tool",
        kind: "tool",
        runId: "run-1",
        toolCallId: "call-1",
        name: "read",
        input: {},
        startedAt: 1,
        status: "running",
        ts: 1,
      },
    });
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "code",
        kind: "code",
        runId: "run-1",
        step: 1,
        code: "partial",
        streaming: true,
        ts: 1,
      },
    });
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "output",
        kind: "output",
        runId: "run-1",
        step: 1,
        output: "partial",
        streaming: true,
        ts: 1,
      },
    });
    const transcript = new TranscriptComponent(store, render);

    transcript.render(80);
    // First render paints every message; equal object identities on the second
    // render must hit the cache (no re-render work).
    expect(render).toHaveBeenCalledTimes(4);
    transcript.render(80);
    expect(render).toHaveBeenCalledTimes(4);
    expect(caches[0]).toBe(caches[1]);

    // A CHANGED streaming message (new object per dispatch) must bust the cache
    // and re-render exactly the one that changed.
    store.dispatch({
      type: "message/upsert",
      message: {
        id: "code",
        kind: "code",
        runId: "run-1",
        step: 1,
        code: "partial-more",
        streaming: true,
        ts: 2,
      },
    });
    transcript.render(80);
    expect(render).toHaveBeenCalledTimes(5);
  });
});
