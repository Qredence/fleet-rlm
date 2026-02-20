import assert from "node:assert/strict";
import test from "node:test";

import { MentionDebounceController } from "./mention-debounce.js";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("mention debounce suppresses burst requests", async () => {
  const controller = new MentionDebounceController(30);
  let calls = 0;

  controller.schedule(() => {
    calls += 1;
  });
  await sleep(5);

  controller.schedule(() => {
    calls += 1;
  });
  await sleep(5);

  controller.schedule(() => {
    calls += 1;
  });

  await sleep(50);

  assert.equal(calls, 1);
});

test("mention debounce token marks stale responses", () => {
  const controller = new MentionDebounceController(0);

  const firstToken = controller.schedule(() => {});
  const secondToken = controller.schedule(() => {});

  assert.equal(controller.isCurrent(firstToken), false);
  assert.equal(controller.isCurrent(secondToken), true);
});
