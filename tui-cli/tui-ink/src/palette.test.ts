import assert from "node:assert/strict";
import test from "node:test";

import {
  applyMentionSelection,
  buildRootPalette,
  buildSettingsPalette,
  clampIndex,
  detectMentionQuery,
  filterPalette,
  moveIndex,
  parseCommandInput,
} from "./palette.js";

test("parseCommandInput parses JSON payload", () => {
  const parsed = parseCommandInput('/run-long-context {"docs_path":"a.md","query":"x"}');
  assert.equal(parsed.command, "run-long-context");
  assert.deepEqual(parsed.args, { docs_path: "a.md", query: "x" });
});

test("parseCommandInput parses key-value payload with coercion", () => {
  const parsed = parseCommandInput("/demo enabled=true retries=3");
  assert.equal(parsed.command, "demo");
  assert.deepEqual(parsed.args, { enabled: true, retries: 3 });
});

test("mention helpers detect and replace mention token", () => {
  assert.equal(detectMentionQuery("hello @src/"), "src/");
  assert.equal(
    applyMentionSelection("hello @src/", "src/fleet_rlm/bridge/server.py"),
    "hello @src/fleet_rlm/bridge/server.py ",
  );
});

test("palette filtering and index bounds work", () => {
  const root = buildRootPalette(["status", "check-secret"]);
  const filtered = filterPalette(root, "settings");
  assert.ok(filtered.some((item) => item.label === "Settings"));
  assert.equal(clampIndex(9, 2), 1);
  assert.equal(moveIndex(0, -1, 5), 0);
  assert.equal(moveIndex(0, 2, 5), 2);
});

test("settings palette contains model/provider actions", () => {
  const settings = buildSettingsPalette({
    masked_values: { DSPY_LM_MODEL: "openai/gpt-4o-mini" },
  });
  assert.ok(settings.some((item) => item.action === "view-model-provider"));
  assert.ok(settings.some((item) => item.action === "edit-model"));
  assert.ok(settings.some((item) => item.action === "edit-api-base"));
});
