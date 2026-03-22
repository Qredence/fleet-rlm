import { describe, expect, it } from "vite-plus/test";

import {
  applyFrameToRunWorkbenchState,
  createInitialRunWorkbenchState,
  failRunWorkbenchRun,
  startRunWorkbenchRun,
  shouldApplyRunFrame,
} from "@/screens/workspace/model/run-workbench-adapter";
import type { WsServerMessage } from "@/lib/rlm-api";

function makeEvent(kind: string, text: string, payload?: Record<string, unknown>): WsServerMessage {
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

describe("runWorkbenchAdapter", () => {
  it("uses chat final run_result only as a narrow summary and final artifact backfill", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Analyze the diligence corpus",
      repoUrl: "https://github.com/qredence/fleet-rlm.git",
      repoRef: "main",
      contextPaths: ["/Users/zocho/Documents/spec.pdf"],
    });

    const next = applyFrameToRunWorkbenchState(
      started,
      makeEvent("final", "Done", {
        runtime_mode: "daytona_pilot",
        runtime: {
          runtime_mode: "daytona_pilot",
          run_id: "run-123",
          daytona_mode: "host_loop_rlm",
        },
        final_artifact: {
          kind: "markdown",
          value: {
            summary: "Readable final summary of the Daytona run.",
            final_markdown: "## Final\nDone",
          },
          finalization_mode: "SUBMIT",
        },
        run_result: {
          run_id: "run-123",
          repo: "https://github.com/qredence/fleet-rlm.git",
          ref: "main",
          task: "Analyze the diligence corpus",
          context_sources: [
            {
              source_id: "ctx-1",
              kind: "file",
              host_path: "/Users/zocho/Documents/spec.pdf",
              staged_path: "/workspace/context/spec.pdf.extracted.txt",
              source_type: "pdf",
              extraction_method: "pypdf",
              file_count: 1,
            },
          ],
          prompts: [
            {
              handle_id: "prompt-1",
              kind: "task",
              label: "Root task",
              char_count: 9001,
              line_count: 120,
              preview: "A long root task preview that should stay visible.",
            },
          ],
          iterations: [
            {
              iteration: 1,
              status: "completed",
              reasoning_summary: "Planner selected a grounded repo-and-doc sweep.",
              code: "summary = 'Done'\nSUBMIT(summary=summary)",
              stdout: "done",
              duration_ms: 123,
              callback_count: 1,
              finalized: true,
            },
          ],
          callbacks: [
            {
              id: "callback-1",
              callback_name: "llm_query_batched",
              iteration: 1,
              status: "completed",
              task: "Summarize tracing subsystem",
              label: "Tracing pass",
              result_preview: "Summarized tracing subsystem.",
              source: {
                kind: "file_slice",
                source_id: "src-1",
                path: "src/fleet_rlm/analytics/scorers.py",
                start_line: 1,
                end_line: 80,
                preview: "Tracing scorer file preview",
              },
            },
          ],
          sources: [
            {
              source_id: "ctx-1",
              kind: "file",
              title: "spec.pdf",
              display_url: "/Users/zocho/Documents/spec.pdf",
              description: "Staged at /workspace/context/spec.pdf.extracted.txt",
            },
          ],
          attachments: [
            {
              attachment_id: "ctx-1",
              name: "spec.pdf",
              kind: "file",
              mime_type: "pdf",
            },
          ],
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
            sandboxes_used: 1,
            termination_reason: "completed",
          },
        },
      }),
    );

    expect(next.status).toBe("completed");
    expect(next.runId).toBe("run-123");
    expect(next.daytonaMode).toBe("host_loop_rlm");
    expect(next.contextSources[0]?.hostPath).toBe("/Users/zocho/Documents/spec.pdf");
    expect(next.iterations).toEqual([]);
    expect(next.callbacks).toEqual([]);
    expect(next.promptHandles).toEqual([]);
    expect(next.sources).toEqual([]);
    expect(next.attachments).toEqual([]);
    expect(next.finalArtifact?.finalizationMode).toBe("SUBMIT");
    expect(next.summary).toMatchObject({
      durationMs: 1234,
      sandboxesUsed: 1,
      terminationReason: "completed",
    });
    expect(next.compatBackfillCount).toBe(1);
    expect(next.lastCompatBackfill).toMatchObject({
      runtimeMode: "daytona_pilot",
      usedSummary: true,
      usedFinalArtifact: true,
    });
  });

  it("hydrates execution_completed run_summary payloads without relying on chat final run_result", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Analyze the diligence corpus",
    });

    const next = applyFrameToRunWorkbenchState(started, {
      type: "event",
      data: {
        kind: "final",
        text: "Done",
        payload: {
          source_type: "execution_completed",
          run_summary: {
            run_id: "run-456",
            runtime_mode: "modal_chat",
            task: "Analyze the diligence corpus",
            status: "completed",
            final_artifact: {
              kind: "markdown",
              value: {
                summary: "Modal execution summary",
              },
            },
            summary: {
              warnings: ["One warning"],
              termination_reason: "final",
            },
            attachments: [
              {
                attachment_id: "attachment-1",
                name: "result.md",
              },
            ],
          },
        },
        event_id: "evt-execution-completed",
        timestamp: "2026-03-10T12:00:00Z",
      },
    });

    expect(next.runId).toBe("run-456");
    expect(next.status).toBe("completed");
    expect(next.finalArtifact?.value).toMatchObject({
      summary: "Modal execution summary",
    });
    expect(next.summary?.warnings).toEqual(["One warning"]);
    expect(next.attachments[0]?.name).toBe("result.md");
  });

  it("hydrates a rich modal execution_completed summary without relying on chat final run_result", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Summarize the runtime",
    });

    const next = applyFrameToRunWorkbenchState(
      started,
      makeEvent("final", "Modal summary complete", {
        source_type: "execution_completed",
        run_summary: {
          run_id: "run-modal-1",
          runtime_mode: "modal_chat",
          task: "Summarize the runtime",
          status: "completed",
          context_sources: [
            {
              source_id: "ctx-modal-1",
              kind: "file",
              host_path: "/workspace/runtime.md",
              staged_path: "/workspace/context/runtime.md",
            },
          ],
          prompts: [
            {
              handle_id: "prompt-modal-1",
              label: "Planner prompt",
              preview: "Summarize the runtime shape",
            },
          ],
          iterations: [
            {
              iteration: 1,
              status: "completed",
              reasoning_summary: "Planner synthesized a modal summary.",
              callback_count: 1,
              finalized: true,
            },
          ],
          callbacks: [
            {
              id: "callback-modal-1",
              callback_name: "llm_query",
              iteration: 1,
              status: "completed",
              task: "Summarize the runtime",
              result_preview: "Runtime summarized.",
            },
          ],
          sources: [
            {
              source_id: "src-modal-1",
              kind: "file",
              title: "runtime.md",
              display_url: "/workspace/runtime.md",
            },
          ],
          attachments: [
            {
              attachment_id: "attachment-modal-1",
              name: "runtime.md",
            },
          ],
          final_artifact: {
            kind: "markdown",
            value: {
              summary: "Modal execution summary",
              final_markdown: "## Modal\nSummary",
            },
          },
          summary: {
            termination_reason: "final",
            warnings: ["Modal warning"],
            duration_ms: 321,
          },
          warnings: ["Modal warning"],
        },
      }),
    );

    expect(next.status).toBe("completed");
    expect(next.runId).toBe("run-modal-1");
    expect(next.contextSources[0]?.hostPath).toBe("/workspace/runtime.md");
    expect(next.promptHandles[0]?.handleId).toBe("prompt-modal-1");
    expect(next.iterations[0]?.reasoningSummary).toContain("modal");
    expect(next.callbacks[0]?.callbackName).toBe("llm_query");
    expect(next.sources[0]?.sourceId).toBe("src-modal-1");
    expect(next.attachments[0]?.name).toBe("runtime.md");
    expect(next.finalArtifact?.value).toMatchObject({
      summary: "Modal execution summary",
    });
    expect(next.summary).toMatchObject({
      terminationReason: "final",
      durationMs: 321,
      warnings: ["Modal warning"],
    });
  });

  it("hydrates a rich Daytona execution_completed summary without relying on chat final run_result", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Analyze the repo",
      contextPaths: ["/workspace/docs/overview.md"],
    });

    const next = applyFrameToRunWorkbenchState(
      started,
      makeEvent("final", "Daytona summary complete", {
        source_type: "execution_completed",
        runtime_mode: "daytona_pilot",
        daytona_mode: "host_loop_rlm",
        run_summary: {
          run_id: "run-daytona-1",
          runtime_mode: "daytona_pilot",
          task: "Analyze the repo",
          status: "completed",
          context_sources: [
            {
              source_id: "ctx-daytona-1",
              kind: "file",
              host_path: "/workspace/docs/overview.md",
              staged_path: "/workspace/context/overview.md",
            },
          ],
          prompts: [
            {
              handle_id: "prompt-daytona-1",
              kind: "task",
              label: "Root task",
              preview: "Inspect the repo",
            },
          ],
          iterations: [
            {
              iteration: 1,
              status: "completed",
              reasoning_summary: "Daytona completed repo analysis.",
              callback_count: 2,
              finalized: true,
            },
          ],
          callbacks: [
            {
              id: "callback-daytona-1",
              callback_name: "llm_query_batched",
              iteration: 1,
              status: "completed",
              task: "Inspect sources",
              result_preview: "Inspected repo sources.",
            },
          ],
          sources: [
            {
              source_id: "src-daytona-1",
              kind: "file",
              title: "overview.md",
              display_url: "/workspace/docs/overview.md",
            },
          ],
          attachments: [
            {
              attachment_id: "attachment-daytona-1",
              name: "overview.md",
            },
          ],
          final_artifact: {
            kind: "markdown",
            value: {
              summary: "Daytona execution summary",
              final_markdown: "## Daytona\nSummary",
            },
            finalization_mode: "SUBMIT",
          },
          summary: {
            termination_reason: "completed",
            warnings: ["Daytona warning"],
            duration_ms: 654,
          },
          warnings: ["Daytona warning"],
        },
      }),
    );

    expect(next.status).toBe("completed");
    expect(next.runId).toBe("run-daytona-1");
    expect(next.daytonaMode).toBe("host_loop_rlm");
    expect(next.contextSources[0]?.hostPath).toBe("/workspace/docs/overview.md");
    expect(next.promptHandles[0]?.handleId).toBe("prompt-daytona-1");
    expect(next.iterations[0]?.reasoningSummary).toContain("Daytona");
    expect(next.callbacks[0]?.callbackName).toBe("llm_query_batched");
    expect(next.sources[0]?.sourceId).toBe("src-daytona-1");
    expect(next.attachments[0]?.name).toBe("overview.md");
    expect(next.finalArtifact?.finalizationMode).toBe("SUBMIT");
    expect(next.summary).toMatchObject({
      terminationReason: "completed",
      durationMs: 654,
      warnings: ["Daytona warning"],
    });
  });

  it("tracks incremental iteration and callback activity before final hydration", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Analyze the repo",
      contextPaths: ["/workspace/docs"],
    });

    const withStatus = applyFrameToRunWorkbenchState(
      started,
      makeEvent("status", "Preparing prompt for iteration 1.", {
        runtime_mode: "daytona_pilot",
        iteration: 1,
        phase: "prepare_prompt",
        elapsed_ms: 42,
        node: {
          prompt_manifest: {
            handles: [
              {
                handle_id: "prompt-boot",
                label: "Boot prompt",
                preview: "Boot preview",
              },
            ],
          },
        },
      }),
    );

    const withCallback = applyFrameToRunWorkbenchState(
      withStatus,
      makeEvent("tool_result", "Completed host callback `llm_query`.", {
        runtime_mode: "daytona_pilot",
        iteration: 1,
        callback_name: "llm_query",
        tool_input: {
          task: {
            task: "Summarize the overview section",
            source: {
              kind: "file_slice",
              path: "docs/overview.md",
              start_line: 1,
              end_line: 20,
            },
          },
        },
        tool_result: {
          result_preview: "The overview explains the system topology.",
        },
      }),
    );

    expect(withCallback.status).toBe("running");
    expect(withCallback.iterations[0]?.iteration).toBe(1);
    expect(withCallback.iterations[0]?.phase).toBe("prepare_prompt");
    expect(withCallback.promptHandles).toHaveLength(1);
    expect(withCallback.callbacks[0]?.callbackName).toBe("llm_query");
    expect(withCallback.callbacks[0]?.source?.path).toBe("docs/overview.md");
    expect(withCallback.activity).toHaveLength(2);
    expect(withCallback.contextSources[0]?.hostPath).toBe("/workspace/docs");
  });

  it("merges tool_result frames into the live callback row when tool_input is absent", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Analyze the repo",
    });

    const withToolCall = applyFrameToRunWorkbenchState(
      started,
      makeEvent("tool_call", "Running host callback `llm_query`.", {
        runtime_mode: "daytona_pilot",
        iteration: 1,
        callback_name: "llm_query",
        tool_input: {
          task: {
            task: "Summarize the overview section",
            label: "Overview",
            source: {
              kind: "file_slice",
              path: "docs/overview.md",
              start_line: 1,
              end_line: 20,
            },
          },
        },
      }),
    );

    const withToolResult = applyFrameToRunWorkbenchState(
      withToolCall,
      makeEvent("tool_result", "Completed host callback `llm_query`.", {
        runtime_mode: "daytona_pilot",
        iteration: 1,
        callback_name: "llm_query",
        tool_result: {
          result_preview: "The overview explains the system topology.",
        },
      }),
    );

    expect(withToolResult.callbacks).toHaveLength(1);
    expect(withToolResult.callbacks[0]?.status).toBe("completed");
    expect(withToolResult.callbacks[0]?.task).toBe("Summarize the overview section");
    expect(withToolResult.callbacks[0]?.label).toBe("Overview");
    expect(withToolResult.callbacks[0]?.source?.path).toBe("docs/overview.md");
    expect(withToolResult.callbacks[0]?.resultPreview).toContain("system topology");
  });

  it("keeps distinct callbacks when the task text repeats across sources", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Analyze the repo",
    });

    const withFirstCallback = applyFrameToRunWorkbenchState(
      started,
      makeEvent("tool_result", "Completed host callback `llm_query`.", {
        runtime_mode: "daytona_pilot",
        iteration: 1,
        callback_name: "llm_query",
        tool_input: {
          task: {
            task: "Summarize the overview section",
            source: {
              kind: "file_slice",
              source_id: "src-1",
              path: "docs/overview.md",
              start_line: 1,
              end_line: 20,
            },
          },
        },
        tool_result: {
          result_preview: "Overview summary.",
        },
      }),
    );

    const withSecondCallback = applyFrameToRunWorkbenchState(
      withFirstCallback,
      makeEvent("tool_result", "Completed host callback `llm_query`.", {
        runtime_mode: "daytona_pilot",
        iteration: 1,
        callback_name: "llm_query",
        tool_input: {
          task: {
            task: "Summarize the overview section",
            source: {
              kind: "file_slice",
              source_id: "src-2",
              path: "docs/architecture.md",
              start_line: 1,
              end_line: 20,
            },
          },
        },
        tool_result: {
          result_preview: "Architecture summary.",
        },
      }),
    );

    expect(withSecondCallback.callbacks).toHaveLength(2);
    expect(withSecondCallback.callbacks.map((item) => item.source?.path)).toEqual([
      "docs/overview.md",
      "docs/architecture.md",
    ]);
  });

  it("keeps distinct sources when multiple excerpts come from the same file", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Analyze the repo",
    });

    const next = applyFrameToRunWorkbenchState(
      started,
      makeEvent("final", "Done", {
        source_type: "execution_completed",
        runtime_mode: "daytona_pilot",
        run_summary: {
          run_id: "run-456",
          repo: "https://github.com/qredence/fleet-rlm.git",
          task: "Analyze the repo",
          prompts: [],
          iterations: [],
          callbacks: [],
          sources: [
            {
              source_id: "slice-1",
              kind: "file",
              title: "overview.md",
              display_url: "docs/overview.md",
              description: "lines 1-20",
              quote: "First excerpt",
            },
            {
              source_id: "slice-2",
              kind: "file",
              title: "overview.md",
              display_url: "docs/overview.md",
              description: "lines 40-60",
              quote: "Second excerpt",
            },
          ],
          attachments: [],
          summary: {
            duration_ms: 200,
            sandboxes_used: 1,
            termination_reason: "completed",
          },
        },
      }),
    );

    expect(next.sources).toHaveLength(2);
    expect(next.sources.map((item) => item.sourceId)).toEqual(["slice-1", "slice-2"]);
  });

  it("ignores non-Daytona frames after a completed Daytona run", () => {
    const completed = applyFrameToRunWorkbenchState(
      startRunWorkbenchRun(createInitialRunWorkbenchState(), {
        task: "Analyze the repo",
      }),
      makeEvent("final", "Done", {
        source_type: "execution_completed",
        runtime_mode: "daytona_pilot",
        runtime: {
          runtime_mode: "daytona_pilot",
          run_id: "run-789",
        },
        run_summary: {
          run_id: "run-789",
          repo: "https://github.com/qredence/fleet-rlm.git",
          task: "Analyze the repo",
          iterations: [],
          callbacks: [],
          prompts: [],
          sources: [],
          attachments: [],
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

    expect(shouldApplyRunFrame(completed, modalFrame)).toBe(false);
  });

  it("marks a pending Daytona run as errored without dropping source context", () => {
    const started = startRunWorkbenchRun(createInitialRunWorkbenchState(), {
      task: "Analyze local docs",
      contextPaths: ["/Users/zocho/Documents/spec.pdf"],
    });

    const next = failRunWorkbenchRun(
      started,
      "No response arrived from the server within 15 seconds. Try again or check the backend logs.",
    );

    expect(next.status).toBe("error");
    expect(next.contextSources[0]?.hostPath).toBe("/Users/zocho/Documents/spec.pdf");
    expect(next.summary?.terminationReason).toBe("failed");
    expect(next.errorMessage).toMatch(/No response arrived/);
    expect(next.activity.at(-1)?.kind).toBe("error");
  });
});
