import { describe, expect, it } from "vitest";

import {
  applyDaytonaFrameToWorkbenchState,
  createInitialDaytonaWorkbenchState,
  startDaytonaWorkbenchRun,
  shouldApplyDaytonaFrame,
} from "@/features/rlm-workspace/daytona-workbench/daytonaWorkbenchAdapter";
import type { WsServerMessage } from "@/lib/rlm-api";

function makeEvent(
  kind: string,
  text: string,
  payload?: Record<string, unknown>,
): WsServerMessage {
  return {
    type: "event",
    data: {
      kind: kind as never,
      text,
      payload,
      event_id: `evt-${kind}`,
      timestamp: "2026-03-10T12:00:00Z",
    },
  };
}

describe("daytonaWorkbenchAdapter", () => {
  it("hydrates a rich final run_result into nodes, prompts, and final artifact", () => {
    const started = startDaytonaWorkbenchRun(createInitialDaytonaWorkbenchState(), {
      task: "Analyze the repo",
      repoUrl: "https://github.com/qredence/fleet-rlm.git",
      repoRef: "main",
    });

    const next = applyDaytonaFrameToWorkbenchState(
      started,
      makeEvent("final", "Done", {
        runtime_mode: "daytona_pilot",
        runtime: {
          runtime_mode: "daytona_pilot",
          run_id: "run-123",
        },
        final_artifact: {
          kind: "markdown",
          value: {
            summary: "Readable final summary of the Daytona run.",
          },
          finalization_mode: "SUBMIT",
        },
        summary: {
          duration_ms: 1234,
          sandboxes_used: 2,
          termination_reason: "completed",
        },
        run_result: {
          run_id: "run-123",
          repo: "https://github.com/qredence/fleet-rlm.git",
          ref: "main",
          task: "Analyze the repo",
          root_id: "root-node",
          final_artifact: {
            kind: "markdown",
            value: {
              summary: "Readable final summary of the Daytona run.",
              final_markdown: "## Final\nDone",
            },
            finalization_mode: "SUBMIT",
          },
          summary: {
            duration_ms: 1234,
            sandboxes_used: 2,
            termination_reason: "completed",
          },
          nodes: {
            "root-node": {
              node_id: "root-node",
              depth: 0,
              task: "Analyze the repo",
              status: "completed",
              sandbox_id: "sbx-root",
              prompt_manifest: {
                handles: [
                  {
                    handle_id: "prompt-1",
                    kind: "task",
                    label: "Root task",
                    char_count: 9001,
                    line_count: 120,
                    preview: "A long root task preview that should stay visible.",
                  },
                ],
              },
              child_links: [
                {
                  child_id: "child-1",
                  callback_name: "llm_query_batched",
                  status: "completed",
                  result_preview: "Summarized tracing subsystem.",
                  task: {
                    task: "Summarize tracing subsystem",
                    label: "Tracing pass",
                    source: {
                      kind: "file_slice",
                      source_id: "src-1",
                      path: "src/fleet_rlm/analytics/scorers.py",
                      start_line: 1,
                      end_line: 80,
                      preview: "Tracing scorer file preview",
                    },
                  },
                },
              ],
            },
          },
        },
      }),
    );

    expect(next.status).toBe("completed");
    expect(next.runId).toBe("run-123");
    expect(next.rootId).toBe("root-node");
    expect(next.selectedNodeId).toBe("root-node");
    expect(next.timeline).toHaveLength(1);
    expect(next.finalArtifact?.finalizationMode).toBe("SUBMIT");
    expect(next.finalArtifact?.textPreview).toContain("Readable final summary");
    expect(next.summary?.terminationReason).toBe("completed");
    expect(next.nodes["root-node"]?.promptHandles[0]?.handleId).toBe("prompt-1");
    expect(next.nodes["root-node"]?.childLinks[0]?.task.source?.path).toBe(
      "src/fleet_rlm/analytics/scorers.py",
    );
  });

  it("tracks incremental node metadata from status events before final hydration", () => {
    const started = startDaytonaWorkbenchRun(createInitialDaytonaWorkbenchState(), {
      task: "Analyze the repo",
      repoUrl: "https://github.com/qredence/fleet-rlm.git",
    });

    const next = applyDaytonaFrameToWorkbenchState(
      started,
      makeEvent("status", "Bootstrapping Daytona sandbox", {
        runtime: {
          runtime_mode: "daytona_pilot",
          run_id: "run-456",
          sandbox_id: "sbx-boot",
          depth: 0,
        },
        node: {
          node_id: "root-node",
          depth: 0,
          task: "Analyze the repo",
          status: "bootstrapping",
          prompt_handles: [
            {
              handle_id: "prompt-boot",
              label: "Boot prompt",
              preview: "Boot preview",
            },
          ],
        },
      }),
    );

    expect(next.status).toBe("running");
    expect(next.runId).toBe("run-456");
    expect(next.nodes["root-node"]?.sandboxId).toBe("sbx-boot");
    expect(next.nodes["root-node"]?.promptHandles).toHaveLength(1);
    expect(next.timeline[0]?.promptHandleCount).toBe(1);
  });

  it("ignores non-Daytona frames after a completed Daytona run", () => {
    const completed = applyDaytonaFrameToWorkbenchState(
      startDaytonaWorkbenchRun(createInitialDaytonaWorkbenchState(), {
        task: "Analyze the repo",
        repoUrl: "https://github.com/qredence/fleet-rlm.git",
      }),
      makeEvent("final", "Done", {
        runtime_mode: "daytona_pilot",
        runtime: {
          runtime_mode: "daytona_pilot",
          run_id: "run-789",
        },
        run_result: {
          run_id: "run-789",
          repo: "https://github.com/qredence/fleet-rlm.git",
          task: "Analyze the repo",
          root_id: "root-node",
          budget: {
            max_sandboxes: 50,
            max_depth: 2,
            max_iterations: 50,
            global_timeout: 3600,
            result_truncation_limit: 10000,
            batch_concurrency: 4,
          },
          nodes: {},
          summary: {
            duration_ms: 200,
            sandboxes_used: 1,
            termination_reason: "completed",
          },
        },
      }),
    );

    const modalFrame = makeEvent("status", "Modal chat status", {
      runtime: {
        execution_profile: "ROOT_INTERLOCUTOR",
      },
    });

    expect(shouldApplyDaytonaFrame(completed, modalFrame)).toBe(false);
  });
});
