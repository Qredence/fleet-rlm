import assert from "node:assert/strict";
import test from "node:test";

import {
  type ChatEventInput,
  clearEventFeed,
  initialEventFeedState,
  reduceEventFeed,
  reduceInlineEvent,
} from "./event-feed.js";

const mkLine = (
  role: "system" | "user" | "assistant" | "tool" | "status" | "error",
  text: string,
) => ({
  id: `id-${Math.random().toString(36).slice(2, 8)}`,
  role,
  text,
});

function apply(mode: "off" | "compact" | "verbose", events: ChatEventInput[]) {
  let state = initialEventFeedState();
  for (const event of events) {
    state = reduceEventFeed(state, mode, event, mkLine);
  }
  return state;
}

test("compact mode suppresses assistant tokens", () => {
  const state = apply("compact", [
    { kind: "assistant_token", text: "hello", payload: {} },
    { kind: "assistant_token", text: "world", payload: {} },
  ]);
  assert.equal(state.lines.length, 0);
});

test("compact mode coalesces reasoning and module status into meta counters", () => {
  const state = apply("compact", [
    { kind: "reasoning_step", text: "r1", payload: {} },
    { kind: "reasoning_step", text: "r2", payload: {} },
    { kind: "status", text: "Running module: Predict", payload: {} },
  ]);
  assert.equal(state.reasoningCount, 2);
  assert.equal(state.moduleStatusCount, 1);
  assert.ok(state.lines.some((line) => line.text.includes("[meta] reasoning steps: 2")));
  assert.ok(state.lines.some((line) => line.text.includes("[meta] module status updates: 1")));
});

test("compact mode keeps high-signal events and summarizes final", () => {
  const state = apply("compact", [
    { kind: "tool_call", text: "tool call: list_files", payload: {} },
    { kind: "tool_result", text: "tool result: finished", payload: {} },
    { kind: "final", text: "Long answer", payload: {} },
  ]);
  assert.ok(state.lines.some((line) => line.text.includes("[tool_call]")));
  assert.ok(!state.lines.some((line) => line.text.includes("[tool_result] tool result: finished")));
  assert.ok(state.lines.some((line) => line.text.includes("[final] Long answer")));
});

test("verbose mode includes assistant tokens", () => {
  const state = apply("verbose", [{ kind: "assistant_token", text: "token", payload: {} }]);
  assert.equal(state.lines.length, 1);
  assert.ok(state.lines[0]?.text.includes("[assistant_token] token"));
});

test("clear resets lines and counters", () => {
  const state = apply("compact", [
    { kind: "reasoning_step", text: "r1", payload: {} },
    { kind: "status", text: "Running module: Predict", payload: {} },
  ]);
  const cleared = clearEventFeed(state);
  assert.equal(cleared.lines.length, 0);
  assert.equal(cleared.reasoningCount, 0);
  assert.equal(cleared.moduleStatusCount, 0);
});

test("inline compact mode suppresses final and emits tool call", () => {
  const initial = initialEventFeedState();
  const tool = reduceInlineEvent(initial, "compact", {
    kind: "tool_call",
    text: "tool call: read_file",
    payload: {},
  });
  assert.equal(tool.line, "[tool_call] tool call: read_file");

  const final = reduceInlineEvent(tool.state, "compact", {
    kind: "final",
    text: "Final answer",
    payload: {},
  });
  assert.equal(final.line, null);
});

test("inline verbose mode shows reasoning_step text", () => {
  const initial = initialEventFeedState();
  const reasoning = reduceInlineEvent(initial, "verbose", {
    kind: "reasoning_step",
    text: "I need to check the file first",
    payload: {},
  });
  assert.ok(reasoning.line?.includes("I need to check the file first"));
  assert.ok(reasoning.line?.startsWith("[reasoning_step]"));
});

test("inline verbose mode shows trajectory thought", () => {
  const initial = initialEventFeedState();
  const trajectory = reduceInlineEvent(initial, "verbose", {
    kind: "trajectory_step",
    text: "",
    payload: {
      step_index: 1,
      step_data: {
        tool_name: "read_file",
        thought: "Let me read the configuration file to understand the setup",
      },
    },
  });
  assert.ok(trajectory.line?.includes("thought: Let me read the configuration file"));
  assert.ok(trajectory.line?.includes("tool=read_file"));
});

test("inline verbose mode suppresses final event", () => {
  const initial = initialEventFeedState();
  const final = reduceInlineEvent(initial, "verbose", {
    kind: "final",
    text: "Final answer",
    payload: {},
  });
  assert.equal(final.line, null);
});
