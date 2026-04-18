import { expect, test } from "@playwright/test";

const TAIL_FRAGMENT = "TAIL_FRAGMENT_EXECUTION_CANVAS_NO_TRUNCATION";
const LONG_OUTPUT_TEXT =
  "Execution canvas deterministic payload for no-truncation verification. " +
  "This text intentionally includes a unique tail marker to verify that " +
  "Timeline and Preview panels render full content end-to-end. " +
  TAIL_FRAGMENT;
const PANEL_LABEL_PATTERN = /workspace/i;

test("execution canvas keeps lanes readable and payloads untruncated", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL(/\/app\/workspace$/);
  await expect(page.getByRole("button", { name: PANEL_LABEL_PATTERN }).first()).toBeVisible();

  const closeSidePanelButton = page.getByRole("button", {
    name: new RegExp(`^Hide ${PANEL_LABEL_PATTERN.source}$`, "i"),
  });
  if ((await closeSidePanelButton.count()) === 0) {
    await page
      .getByRole("button", {
        name: new RegExp(`^Show ${PANEL_LABEL_PATTERN.source}$`, "i"),
      })
      .click({ force: true });
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
                tool_name: "summarize_long_document",
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

          const chatStoreModule = await import("/src/features/workspace/use-workspace.ts");
          const navigationStoreModule = await import("/src/stores/navigation-store.ts");
          const workspaceUiStoreModule = await import("/src/features/workspace/use-workspace.ts");

          chatStoreModule.useChatStore.setState({
            messages: [
              {
                id: "trace-reasoning",
                type: "trace",
                content: "reasoning",
                traceSource: "live",
                renderParts: [
                  {
                    kind: "reasoning",
                    parts: [
                      {
                        type: "text",
                        text: "Execution canvas reasoning stays fully readable without truncation.",
                      },
                    ],
                    isStreaming: false,
                  },
                ],
              },
              {
                id: "trace-tool",
                type: "trace",
                content: "tool call",
                traceSource: "live",
                renderParts: [
                  {
                    kind: "tool",
                    title: "summarize_long_document",
                    toolType: "summarize_long_document",
                    state: "output-available",
                    input: { path: "docs/spec.md" },
                    output: longOutputText,
                  },
                ],
              },
              {
                id: "assistant-1",
                type: "assistant",
                content: "Execution canvas smoke turn",
                streaming: false,
                renderParts: [
                  {
                    kind: "sources",
                    title: "Sources",
                    sources: [
                      {
                        sourceId: "src-1",
                        kind: "file",
                        title: "docs/spec.md",
                        description: "Execution canvas smoke source.",
                      },
                    ],
                  },
                ],
              },
            ],
            turnArtifactsByMessageId: {
              "assistant-1": steps,
            },
            isStreaming: false,
            error: null,
          });

          navigationStoreModule.useNavigationStore.setState({
            activeNav: "workspace",
            isCanvasOpen: true,
          });

          workspaceUiStoreModule.useWorkspaceUiStore.setState({
            selectedAssistantTurnId: "assistant-1",
            activeInspectorTab: "graph",
          });
        },
        { longOutputText: LONG_OUTPUT_TEXT },
      );
      break;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error ?? "");
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
  await expect(page.getByText("Sub-agent", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Delegate", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("2.0s", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("tabpanel").getByText(TAIL_FRAGMENT, { exact: false }).first(),
  ).toBeVisible();

  await page.getByRole("tab", { name: "Execution", exact: true }).click();
  await expect(
    page.getByText(/tool:\s*summarize_long_document/i).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("tabpanel").getByText(TAIL_FRAGMENT, { exact: false }).first(),
  ).toBeVisible();

  await page.getByRole("tab", { name: "Message", exact: true }).click();
  await expect(page.getByText("docs/spec.md", { exact: false }).first()).toBeVisible();
});
