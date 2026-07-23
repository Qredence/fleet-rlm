import { describe, expect, it } from "vitest";

import { summarizeExecution } from "./execution-summary.js";
import type { Message } from "./store.js";

function usage(runId = "run"): Message {
  return {
    id: `usage-${runId}`,
    kind: "usage",
    runId,
    iterations: 2,
    inputTokens: 10,
    outputTokens: 4,
    durationMs: 1200,
    observedLmUsage: {},
    ts: 20,
  };
}

function tool(
  toolCallId: string,
  name: string,
  input: unknown,
  status: "running" | "success" | "error" = "success",
): Message {
  return {
    id: `tool-${toolCallId}`,
    kind: "tool",
    runId: "run",
    toolCallId,
    name,
    input,
    ...(status === "success" ? { output: { ok: true }, endedAt: 2 } : {}),
    ...(status === "error" ? { error: "failed", endedAt: 2 } : {}),
    startedAt: 1,
    status,
    ts: 1,
  };
}

describe("summarizeExecution", () => {
  it("reports authoritative zeroes for a completed direct computation", () => {
    expect(
      summarizeExecution(
        [
          { id: "code", kind: "code", runId: "run", step: 1, code: "print(1)", ts: 1 },
          { id: "output", kind: "output", runId: "run", step: 1, output: "1", ts: 2 },
          usage(),
        ],
        "run",
      ),
    ).toEqual({
      iterations: 2,
      subLmCalls: 0,
      hostCapabilityCalls: 0,
      interpreterErrors: 0,
      durationMs: 1200,
    });
  });

  it("counts single and batched semantic prompts without double-counting lifecycle updates", () => {
    const running = tool("single", "llm_query", { prompt_chars: 20 }, "running");
    const completed = { ...running, status: "success" as const, output: "ok", endedAt: 2 };

    expect(
      summarizeExecution(
        [
          running,
          completed,
          tool("batch", "llm_query_batched", { prompt_count: 3 }),
          tool("host", "read_session_history", { offset: 0, limit: 20 }),
          usage(),
        ],
        "run",
      ),
    ).toMatchObject({ subLmCalls: 4, hostCapabilityCalls: 1 });
  });

  it("counts failed generated-code steps but not ordinary host-tool failures", () => {
    expect(
      summarizeExecution(
        [
          {
            id: "error-1",
            kind: "output",
            runId: "run",
            step: 1,
            output: "[Error] SyntaxError",
            ts: 1,
          },
          {
            id: "error-1-correction",
            kind: "output",
            runId: "run",
            step: 1,
            output: "Execution error",
            ts: 2,
          },
          {
            id: "error-2",
            kind: "output",
            runId: "run",
            step: 2,
            output: "Execution failed",
            ts: 3,
          },
          tool("host", "read_attachment", {}, "error"),
          usage(),
        ],
        "run",
      ),
    ).toMatchObject({ hostCapabilityCalls: 1, interpreterErrors: 2 });
  });

  it("keeps incomplete or malformed telemetry unknown", () => {
    expect(summarizeExecution([tool("single", "llm_query", {})], "run")).toEqual({
      iterations: null,
      subLmCalls: null,
      hostCapabilityCalls: null,
      interpreterErrors: null,
      durationMs: null,
    });
    expect(
      summarizeExecution(
        [tool("batch", "llm_query_batched", { prompt_count: "three" }), usage()],
        "run",
      ).subLmCalls,
    ).toBeNull();
  });

  it("keeps Deno built-in semantic calls unknown when no tool event is observable", () => {
    expect(
      summarizeExecution(
        [
          {
            id: "code",
            kind: "code",
            runId: "run",
            step: 1,
            code: 'answer = llm_query("classify this")',
            ts: 1,
          },
          { id: "output", kind: "output", runId: "run", step: 1, output: "answer", ts: 2 },
          usage(),
        ],
        "run",
      ).subLmCalls,
    ).toBeNull();
  });

  it("keeps event-derived metrics unknown when execution details were omitted", () => {
    expect(
      summarizeExecution(
        [
          tool("single", "llm_query", {}),
          {
            id: "warning",
            kind: "warning",
            runId: "run",
            code: "warning",
            message: "some detailed execution events were omitted",
            ts: 10,
          },
          usage(),
        ],
        "run",
      ),
    ).toEqual({
      iterations: 2,
      subLmCalls: null,
      hostCapabilityCalls: null,
      interpreterErrors: null,
      durationMs: 1200,
    });
  });
});
