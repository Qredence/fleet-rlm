import { expect, test } from "@playwright/test";

const TAIL_FRAGMENT = "TAIL_FRAGMENT_EXECUTION_CANVAS_NO_TRUNCATION";
const LONG_OUTPUT_TEXT =
  "Execution canvas deterministic payload for no-truncation verification. " +
  "This text intentionally includes a unique tail marker to verify that " +
  "Timeline and Preview panels render full content end-to-end. " +
  TAIL_FRAGMENT;

test("execution canvas keeps lanes readable and payloads untruncated", async ({
  page,
}) => {
  await page.goto("/");
  await page.waitForURL(/\/app\/workspace$/);
  await expect(
    page.getByRole("button", { name: /side panel/i }).first(),
  ).toBeVisible();

  const closeSidePanelButton = page.getByRole("button", {
    name: "Close side panel",
  });
  if ((await closeSidePanelButton.count()) === 0) {
    await page.getByRole("button", { name: "Open side panel" }).click();
  }

  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await page.evaluate(
        async ({ longOutputText }) => {
          const baseTimestamp = Date.parse("2026-03-03T10:00:00.000Z");
          const steps = [
            {
              id: "root-1",
              type: "llm",
              label: "Root planning step",
              timestamp: baseTimestamp,
              depth: 0,
              actor_kind: "root_rlm",
              lane_key: "root_rlm",
              output: { text: "Root planner initialized." },
            },
            {
              id: "sub-1",
              parent_id: "root-1",
              type: "tool",
              label: "Sub-agent analysis",
              timestamp: baseTimestamp + 2_000,
              depth: 1,
              actor_kind: "sub_agent",
              actor_id: "research-sub-agent",
              lane_key: "sub_agent:research-sub-agent",
              output: {
                tool_name: "analyze_long_document",
                tool_output: "Sub-agent extracted requirements.",
              },
            },
            {
              id: "delegate-1",
              parent_id: "sub-1",
              type: "repl",
              label: "Delegate execution",
              timestamp: baseTimestamp + 5_000,
              depth: 2,
              actor_kind: "delegate",
              actor_id: "delegate-worker",
              lane_key: "delegate:delegate-worker",
              input: { code: "print('delegate run')" },
              output: { text: "Delegate execution completed." },
            },
            {
              id: "output-1",
              parent_id: "delegate-1",
              type: "output",
              label: "Final artifact output",
              timestamp: baseTimestamp + 8_000,
              depth: 2,
              actor_kind: "delegate",
              actor_id: "delegate-worker",
              lane_key: "delegate:delegate-worker",
              output: {
                text: longOutputText,
                payload: { summary: longOutputText },
              },
            },
          ];

          const storeModule = await import("/src/stores/artifactStore.ts");
          const state = storeModule.useArtifactStore.getState();
          state.setSteps(steps);
          state.setActiveStepId("output-1");
        },
        { longOutputText: LONG_OUTPUT_TEXT },
      );
      break;
    } catch (error) {
      const message =
        error instanceof Error ? error.message : String(error ?? "");
      const isRetryable = message.includes("Execution context was destroyed");
      const isLastAttempt = attempt === 2;
      if (!isRetryable || isLastAttempt) {
        throw error;
      }
      await page.waitForURL(/\/app\/workspace$/);
      await page.waitForLoadState("domcontentloaded");
    }
  }

  await page.getByRole("tab", { name: "Graph", exact: true }).click();
  await expect(page.getByText("Root RLM", { exact: false }).first()).toBeVisible();
  await expect(
    page.getByText("Sub-agent", { exact: false }).first(),
  ).toBeVisible();
  await expect(page.getByText("Delegate", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("2.0s", { exact: true }).first()).toBeVisible();

  await page.getByRole("tab", { name: "Timeline", exact: true }).click();
  await expect(
    page.getByRole("tabpanel").getByText(TAIL_FRAGMENT, { exact: false }).first(),
  ).toBeVisible();

  await page.getByRole("tab", { name: "Preview", exact: true }).click();
  await expect(
    page.getByRole("tabpanel").getByText(TAIL_FRAGMENT, { exact: false }).first(),
  ).toBeVisible();
});
