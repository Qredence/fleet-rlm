import assert from "node:assert/strict";
import test from "node:test";

import {
  BridgeEventEnvelopeSchema,
  BridgeResponseEnvelopeSchema,
  parseBridgeChatSubmit,
  parseBridgeMentionSearch,
  parseBridgeSessionInit,
  parseBridgeSettingsSnapshot,
  parseBridgeStatusPayload,
} from "./bridge-schemas.js";

test("parseBridgeSessionInit accepts valid payload", () => {
  const parsed = parseBridgeSessionInit({
    session_id: "session-1",
    commands: {
      tool_commands: ["search"],
      wrapper_commands: ["status"],
    },
    extra_field: true,
  });

  assert.equal(parsed.session_id, "session-1");
  assert.deepEqual(parsed.commands?.tool_commands, ["search"]);
  assert.deepEqual(parsed.commands?.wrapper_commands, ["status"]);
});

test("parseBridgeSessionInit rejects malformed payload", () => {
  assert.throws(
    () =>
      parseBridgeSessionInit({
        commands: {
          tool_commands: [123],
        },
      }),
    /Invalid session\.init response payload/,
  );
});

test("BridgeResponseEnvelopeSchema requires string id", () => {
  const invalid = BridgeResponseEnvelopeSchema.safeParse({
    id: 1,
    result: {},
  });
  assert.equal(invalid.success, false);
});

test("BridgeEventEnvelopeSchema defaults params to empty object", () => {
  const valid = BridgeEventEnvelopeSchema.safeParse({
    event: "chat.event",
  });
  assert.equal(valid.success, true);
  if (valid.success) {
    assert.deepEqual(valid.data.params, {});
  }
});

test("parseBridgeSettingsSnapshot normalizes missing maps", () => {
  const parsed = parseBridgeSettingsSnapshot({});
  assert.deepEqual(parsed.values, {});
  assert.deepEqual(parsed.masked_values, {});
});

test("parseBridgeSettingsSnapshot rejects invalid map values", () => {
  assert.throws(
    () => parseBridgeSettingsSnapshot({ values: { DSPY_LM_MODEL: 123 } }),
    /Invalid settings\.get response payload/,
  );
});

test("parseBridgeStatusPayload requires object payload", () => {
  assert.throws(() => parseBridgeStatusPayload("bad"), /Invalid status\.get response payload/);
});

test("parseBridgeChatSubmit accepts optional assistant_response", () => {
  const parsed = parseBridgeChatSubmit({ assistant_response: "done" });
  assert.equal(parsed.assistant_response, "done");
});

test("parseBridgeChatSubmit rejects non-string assistant_response", () => {
  assert.throws(
    () => parseBridgeChatSubmit({ assistant_response: 42 }),
    /Invalid chat\.submit response payload/,
  );
});

test("parseBridgeMentionSearch parses item list", () => {
  const parsed = parseBridgeMentionSearch({
    items: [{ path: "src/main.ts", kind: "file", score: 0.9 }],
  });
  assert.equal(parsed.items.length, 1);
  assert.equal(parsed.items[0]?.path, "src/main.ts");
});

test("parseBridgeMentionSearch rejects malformed items", () => {
  assert.throws(
    () => parseBridgeMentionSearch({ items: [{ path: "x", kind: "file", score: "1.0" }] }),
    /Invalid mention\.search response payload/,
  );
});
