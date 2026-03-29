import { describe, expect, it, vi } from "vite-plus/test";
import { QueryClient } from "@tanstack/react-query";
import { applyWsFrameToMessages } from "@/lib/workspace/backend-chat-event-adapter";
import type {
  ChatMessage,
  ChatRenderPart,
} from "@/lib/workspace/workspace-types";
import type { WsServerMessage } from "@/lib/rlm-api";

function makeEvent(
  kind: string,
  text: string,
  payload?: Record<string, unknown>,
): WsServerMessage {
  return {
    type: "event",
    data: { kind: kind as never, text, payload },
  };
}

function makeError(message: string): WsServerMessage {
  return { type: "error", message };
}

function findFirstPart(
  messages: ChatMessage[],
  predicate: (part: ChatRenderPart) => boolean,
): ChatRenderPart | undefined {
  for (const message of messages) {
    for (const part of message.renderParts ?? []) {
      if (predicate(part)) return part;
    }
  }
  return undefined;
}

function findAllParts(
  messages: ChatMessage[],
  predicate: (part: ChatRenderPart) => boolean,
): ChatRenderPart[] {
  const results: ChatRenderPart[] = [];
  for (const message of messages) {
    for (const part of message.renderParts ?? []) {
      if (predicate(part)) results.push(part);
    }
  }
  return results;
}

function traceRows(
  messages: ChatMessage[],
  predicate?: (part: ChatRenderPart, message: ChatMessage) => boolean,
): Array<{ message: ChatMessage; part: ChatRenderPart }> {
  const rows: Array<{ message: ChatMessage; part: ChatRenderPart }> = [];
  for (const message of messages) {
    if (message.type !== "trace") continue;
    const part = message.renderParts?.[0];
    if (!part) continue;
    if (predicate && !predicate(part, message)) continue;
    rows.push({ message, part });
  }
  return rows;
}

describe("applyWsFrameToMessages", () => {
  it("appends a backend error system message and closes open reasoning", () => {
    const initial: ChatMessage[] = [
      {
        id: "r1",
        type: "reasoning",
        content: "",
        phase: 1,
        reasoningData: {
          parts: [{ type: "text", text: "thinking..." }],
          isThinking: true,
        },
      },
    ];

    const { messages, terminal, errored } = applyWsFrameToMessages(
      initial,
      makeError("Something went wrong"),
    );

    expect(terminal).toBe(true);
    expect(errored).toBe(true);
    expect(messages.some((m) => m.type === "system")).toBe(true);
    const reasoning = messages.find((m) => m.type === "reasoning");
    expect(reasoning?.reasoningData?.isThinking).toBe(false);
  });

  it("accumulates assistant tokens into a streaming assistant message", () => {
    let msgs: ChatMessage[] = [];
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("assistant_token", "Hello"),
    ).messages;
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("assistant_token", " world"),
    ).messages;

    const assistant = msgs.find((m) => m.type === "assistant");
    expect(assistant?.content).toBe("Hello world");
    expect(assistant?.streaming).toBe(true);
  });

  it("creates append-only reasoning rows for reasoning_step events", () => {
    let messages: ChatMessage[] = [];
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("reasoning_step", "Analyzing input "),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("reasoning_step", "and checking constraints"),
    ).messages;

    const reasoningRows = traceRows(
      messages,
      (part, message) =>
        part.kind === "reasoning" && message.traceSource === "live",
    );

    expect(reasoningRows).toHaveLength(2);
    expect(
      reasoningRows.map((row) =>
        row.part.kind === "reasoning" ? row.part.parts[0]?.text : "",
      ),
    ).toEqual(["Analyzing input ", "and checking constraints"]);
  });

  it("attaches runtime context to live reasoning rows", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("reasoning_step", "Inspecting sandbox output", {
        runtime: {
          depth: 1,
          max_depth: 3,
          execution_profile: "RLM_ROOT",
          sandbox_active: true,
          effective_max_iters: 30,
          volume_name: "shared-volume",
          execution_mode: "rlm",
          sandbox_id: "sb-1234567890",
        },
      }),
    );

    const reasoning = findFirstPart(
      messages,
      (part) => part.kind === "reasoning",
    );
    expect(reasoning).toBeDefined();
    if (reasoning?.kind === "reasoning") {
      expect(reasoning.runtimeContext).toEqual({
        depth: 1,
        maxDepth: 3,
        executionProfile: "RLM_ROOT",
        sandboxActive: true,
        effectiveMaxIters: 30,
        volumeName: "shared-volume",
        executionMode: "rlm",
        sandboxId: "sb-1234567890",
      });
    }
  });

  it("uses payload reasoning labels for live reasoning rows", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("reasoning_step", "Planner prompt preview", {
        reasoning_label: "prompt_iter_1",
      }),
    );

    const reasoning = findFirstPart(
      messages,
      (part) => part.kind === "reasoning",
    );
    expect(reasoning).toBeDefined();
    if (reasoning?.kind === "reasoning") {
      expect(reasoning.label).toBe("prompt_iter_1");
    }
  });

  it("uses trajectory as fallback primary rows when live events are absent", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("trajectory_step", "trace", {
        step_index: 0,
        step_data: {
          thought: "Read file",
          action: "Inspect entrypoint",
          tool_name: "read_file",
          observation: "Found entrypoint",
        },
      }),
    );

    const fallbackPrimary = traceRows(
      messages,
      (_part, message) => message.traceSource === "trajectory",
    );
    expect(fallbackPrimary.map((row) => row.part.kind)).toEqual(["reasoning"]);

    const fallbackReasoning = fallbackPrimary[0]?.part;
    if (fallbackReasoning?.kind === "reasoning") {
      expect(fallbackReasoning.parts[0]?.text).toBe("Read file");
    }

    const cot = findFirstPart(messages, (p) => p.kind === "chain_of_thought");
    expect(cot).toBeDefined();
    if (cot?.kind === "chain_of_thought") {
      expect(cot.steps).toHaveLength(1);
      expect(cot.steps[0]?.label).toContain("Inspect entrypoint");
      expect(cot.steps[0]?.status).toBe("active");
      expect(cot.steps[0]?.details).toContain("Tool · read_file");
      expect(cot.steps[0]?.details).toContain("Observation · Found entrypoint");
    }
  });

  it("suppresses trajectory fallback primary rows when live trace already exists", () => {
    let messages: ChatMessage[] = [];
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("reasoning_step", "Live reasoning"),
    ).messages;

    messages = applyWsFrameToMessages(
      messages,
      makeEvent("trajectory_step", "trace", {
        step_index: 0,
        step_data: {
          thought: "Should not duplicate in primary",
          tool_name: "grep_file",
          observation: "found",
        },
      }),
    ).messages;

    const trajectoryPrimary = traceRows(
      messages,
      (_part, message) => message.traceSource === "trajectory",
    );
    expect(trajectoryPrimary).toHaveLength(0);

    const reasoningRows = traceRows(
      messages,
      (part) => part.kind === "reasoning",
    );
    expect(reasoningRows).toHaveLength(1);

    const cot = findFirstPart(
      messages,
      (part) => part.kind === "chain_of_thought",
    );
    expect(cot).toBeDefined();
    if (cot?.kind === "chain_of_thought") {
      expect(cot.steps).toHaveLength(1);
      expect(cot.steps[0]?.label).toBe("Tool: grep_file");
    }
  });

  it("normalizes indexed trajectory payloads and renders sorted step order", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("trajectory_step", "trace", {
        thought_1: "Second thought",
        tool_name_1: "glob_search",
        tool_args_1: { path: ".", pattern: "**/*" },
        observation_1: { count: 2 },
        thought_0: "First thought",
        tool_name_0: "list_files",
        tool_args_0: { path: "src" },
        observation_0: ["a.py", "b.py"],
      }),
    );

    const trajectoryReasoning = traceRows(
      messages,
      (part, message) =>
        part.kind === "reasoning" && message.traceSource === "trajectory",
    );
    expect(
      trajectoryReasoning.map((row) =>
        row.part.kind === "reasoning" ? row.part.parts[0]?.text : "",
      ),
    ).toEqual(["First thought", "Second thought"]);

    const tools = findAllParts(messages, (part) => part.kind === "tool");
    expect(tools).toHaveLength(0);

    const cot = findFirstPart(
      messages,
      (part) => part.kind === "chain_of_thought",
    );
    expect(cot).toBeDefined();
    if (cot?.kind === "chain_of_thought") {
      expect(cot.steps).toHaveLength(2);
      expect(cot.steps[0]?.index).toBe(0);
      expect(cot.steps[1]?.index).toBe(1);
      expect(cot.steps[0]?.details).toContain("Input · path=src");
      expect(cot.steps[0]?.details).toContain("Observation · a.py, b.py");
      expect(cot.steps[1]?.details).toContain("Input · path=., pattern=**/*");
      expect(cot.steps[1]?.details).toContain("Observation · count=2");
    }
  });

  it("keeps chain_of_thought sorted by index even when events arrive out of order", () => {
    let messages: ChatMessage[] = [];
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("trajectory_step", "late step", {
        step_index: 1,
        step_data: { thought: "second", tool_name: "tool_2" },
      }),
    ).messages;

    messages = applyWsFrameToMessages(
      messages,
      makeEvent("trajectory_step", "early step", {
        step_index: 0,
        step_data: { thought: "first", tool_name: "tool_1" },
      }),
    ).messages;

    const cot = findFirstPart(
      messages,
      (part) => part.kind === "chain_of_thought",
    );
    expect(cot).toBeDefined();
    if (cot?.kind === "chain_of_thought") {
      expect(cot.steps.map((step) => step.index)).toEqual([0, 1]);
      expect(cot.steps[0]?.label).toBe("Tool: tool_1");
      expect(cot.steps[1]?.label).toBe("Tool: tool_2");
    }
  });

  it("keeps exact interleaved order for reasoning and tool events", () => {
    let messages: ChatMessage[] = [];
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("reasoning_step", "r1"),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("tool_call", "call", {
        tool_name: "grep",
        tool_args: { pattern: "foo" },
      }),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("reasoning_step", "r2"),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("tool_result", "result", {
        tool_name: "grep",
        tool_output: "match",
      }),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("reasoning_step", "r3"),
    ).messages;

    const primaryRows = traceRows(
      messages,
      (part, message) =>
        message.traceSource === "live" &&
        (part.kind === "reasoning" ||
          part.kind === "tool" ||
          part.kind === "sandbox"),
    );

    expect(primaryRows.map((row) => row.part.kind)).toEqual([
      "reasoning",
      "tool",
      "reasoning",
      "tool",
      "reasoning",
    ]);

    const toolRows = primaryRows.filter((row) => row.part.kind === "tool");
    expect(toolRows).toHaveLength(2);
    if (
      toolRows[0]?.part.kind === "tool" &&
      toolRows[1]?.part.kind === "tool"
    ) {
      expect(toolRows[0].part.state).toBe("running");
      expect(toolRows[1].part.state).toBe("output-available");
    }
  });

  it("maps plan_update, rlm_executing, memory_update to task rows in order", () => {
    let messages: ChatMessage[] = [];

    messages = applyWsFrameToMessages(
      messages,
      makeEvent("plan_update", "Moving to step 2"),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("rlm_executing", "Delegating", {
        tool_name: "PythonInterpreter",
      }),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("memory_update", "Saved semantic relationship"),
    ).messages;

    const taskRows = traceRows(
      messages,
      (part, message) => part.kind === "task" && message.traceSource === "live",
    );
    expect(taskRows).toHaveLength(3);

    const taskTitles = taskRows.map((row) =>
      row.part.kind === "task" ? row.part.title : "",
    );
    expect(taskTitles).toEqual([
      "Plan update",
      "Executing PythonInterpreter",
      "Saved semantic relationship",
    ]);

    const taskStatuses = taskRows.map((row) =>
      row.part.kind === "task" ? row.part.status : "pending",
    );
    expect(taskStatuses).toEqual(["in_progress", "in_progress", "completed"]);

    const queue = findFirstPart(messages, (p) => p.kind === "queue");
    expect(queue).toBeDefined();
    if (queue?.kind === "queue") {
      expect(queue.items[queue.items.length - 1]?.label).toBe(
        "Moving to step 2",
      );
    }
  });

  it("maps tool_call/tool_result to distinct chronological tool rows", () => {
    let messages: ChatMessage[] = [];
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("tool_call", "Running tool", {
        tool_name: "grep",
        tool_args: { pattern: "foo" },
      }),
    ).messages;

    messages = applyWsFrameToMessages(
      messages,
      makeEvent("tool_result", "Done", {
        tool_name: "grep",
        tool_output: "match line",
      }),
    ).messages;

    const toolRows = traceRows(messages, (p) => p.kind === "tool");
    expect(toolRows).toHaveLength(2);

    const first = toolRows[0]?.part;
    const second = toolRows[1]?.part;
    if (first?.kind === "tool" && second?.kind === "tool") {
      expect(first.state).toBe("running");
      expect(second.state).toBe("output-available");
      expect(String(second.output)).toContain("match line");
    }
  });

  it("preserves tool-session metadata on status_note rows", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("status", "Tool finished", {
        tool_name: "read_buffer",
        step_index: 4,
        runtime: {
          depth: 1,
          max_depth: 4,
          execution_profile: "ROOT_INTERLOCUTOR",
          sandbox_active: true,
          effective_max_iters: 12,
          volume_name: "docs-volume",
          execution_mode: "rlm",
          sandbox_id: "sb-status-1",
        },
      }),
    );

    const statusPart = findFirstPart(messages, (p) => p.kind === "status_note");
    expect(statusPart).toBeDefined();
    if (statusPart?.kind === "status_note") {
      expect(statusPart.text).toBe("Tool finished");
      expect(statusPart.toolName).toBe("read_buffer");
      expect(statusPart.stepIndex).toBe(4);
      expect(statusPart.runtimeContext).toEqual({
        depth: 1,
        maxDepth: 4,
        executionProfile: "ROOT_INTERLOCUTOR",
        sandboxActive: true,
        effectiveMaxIters: 12,
        volumeName: "docs-volume",
        executionMode: "rlm",
        sandboxId: "sb-status-1",
      });
    }
  });

  it("renders Daytona sandbox progress status events as sandbox trace parts", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("status", "Sandbox stdout: loading repository metadata", {
        phase: "sandbox_output",
        iteration: 2,
        stream: "stdout",
        stream_text: "loading repository metadata\n",
        runtime: {
          depth: 0,
          max_depth: 3,
          execution_profile: "DAYTONA_PILOT_HOST_LOOP",
          sandbox_active: true,
          effective_max_iters: 30,
          runtime_mode: "daytona_pilot",
          sandbox_id: "sbx-live-1",
        },
      }),
    );

    const sandbox = findFirstPart(messages, (part) => part.kind === "sandbox");
    expect(sandbox).toBeDefined();
    if (sandbox?.kind === "sandbox") {
      expect(sandbox.title).toBe("Sandbox stdout");
      expect(sandbox.state).toBe("running");
      expect(sandbox.stepIndex).toBe(2);
      expect(sandbox.output).toBe("loading repository metadata");
      expect(sandbox.runtimeContext?.runtimeMode).toBe("daytona_pilot");
    }

    const statusNote = findFirstPart(
      messages,
      (part) => part.kind === "status_note",
    );
    expect(statusNote).toBeUndefined();
  });

  it("treats payload-level tool failures as errors even when text says finished", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("tool_result", "Tool finished", {
        tool_name: "find_files",
        tool_output: {
          status: "error",
          error: "rg not found",
        },
      }),
    );

    const tool = findFirstPart(messages, (part) => part.kind === "tool");
    expect(tool).toBeDefined();
    if (tool?.kind === "tool") {
      expect(tool.state).toBe("output-error");
      expect(tool.errorText).toContain("rg not found");
    }
  });

  it("does not treat successful tool output mentioning errors as a failure", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("tool_result", "Tool finished", {
        tool_name: "grep",
        tool_output: "0 errors found while scanning logs",
      }),
    );

    const tool = findFirstPart(messages, (part) => part.kind === "tool");
    expect(tool).toBeDefined();
    if (tool?.kind === "tool") {
      expect(tool.state).toBe("output-available");
      expect(tool.errorText).toBeUndefined();
      expect(tool.output).toBe("0 errors found while scanning logs");
    }
  });

  it("keeps trajectory fallback reasoning and later live tool result as separate rows", () => {
    let messages = applyWsFrameToMessages(
      [],
      makeEvent("trajectory_step", "trace", {
        step_index: 0,
        step_data: {
          thought: "Run grep",
          tool_name: "grep",
          tool_args: { pattern: "foo" },
        },
      }),
    ).messages;

    messages = applyWsFrameToMessages(
      messages,
      makeEvent("tool_result", "Done", {
        step_index: 0,
        tool_name: "grep",
        tool_output: { matches: 3 },
      }),
    ).messages;

    const reasoningRows = traceRows(
      messages,
      (part) => part.kind === "reasoning",
    );
    const toolRows = traceRows(messages, (part) => part.kind === "tool");

    expect(reasoningRows).toHaveLength(1);
    expect(toolRows).toHaveLength(1);
    expect(reasoningRows[0]?.message.traceSource).toBe("trajectory");
    expect(toolRows[0]?.message.traceSource).toBe("live");

    if (reasoningRows[0]?.part.kind === "reasoning") {
      expect(reasoningRows[0].part.parts[0]?.text).toBe("Run grep");
    }
    if (toolRows[0]?.part.kind === "tool") {
      expect(toolRows[0].part.state).toBe("output-available");
      expect(toolRows[0].part.output).toEqual({ matches: 3 });
    }
  });

  it("classifies repl execution payloads as sandbox render parts", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("tool_call", "python", {
        tool_name: "python",
        step: { type: "repl", input: "print(1)" },
      }),
    );

    const sandbox = findFirstPart(messages, (p) => p.kind === "sandbox");
    expect(sandbox).toBeDefined();
    if (sandbox?.kind === "sandbox") {
      expect(sandbox.code).toContain("print(1)");
    }
  });

  it("classifies environment variable payloads as environment_variables on tool_result", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("tool_result", "Env loaded", {
        tool_name: "runtime_settings",
        variables: { OPENAI_API_KEY: "sk-test", APP_ENV: "local" },
      }),
    );

    const env = findFirstPart(
      messages,
      (p) => p.kind === "environment_variables",
    );
    expect(env).toBeDefined();
    if (env?.kind === "environment_variables") {
      expect(env.variables.map((v) => v.name)).toContain("OPENAI_API_KEY");
    }
  });

  it("invalidates memory query on memory_update and renders completed task", () => {
    const mockQueryClient = {
      invalidateQueries: vi.fn(),
    } as unknown as QueryClient;

    const { messages, terminal } = applyWsFrameToMessages(
      [],
      makeEvent("memory_update", "Saved semantic relationship"),
      mockQueryClient,
    );

    expect(terminal).toBe(false);
    expect(mockQueryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["memory"],
    });
    const task = findFirstPart(messages, (p) => p.kind === "task");
    expect(task).toBeDefined();
    if (task?.kind === "task") {
      expect(task.status).toBe("completed");
    }
  });

  it("final finalizes trace summaries and attaches citations/sources/attachments", () => {
    let messages: ChatMessage[] = [];
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("assistant_token", "Hello"),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("reasoning_step", "Thinking"),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("trajectory_step", "trace", {
        step_index: 0,
        step_data: { thought: "step one", tool_name: "read_file" },
      }),
    ).messages;
    messages = applyWsFrameToMessages(
      messages,
      makeEvent("plan_update", "Do X"),
    ).messages;

    const result = applyWsFrameToMessages(
      messages,
      makeEvent("final", "Done", {
        trajectory: {
          thought_1: "Second trajectory thought",
          thought_0: "First trajectory thought",
        },
        final_reasoning: "The evidence lines up with the cited sources.",
        citations: [
          {
            source_id: "src-b",
            anchor_id: "anchor-b",
            title: "Doc B",
            url: "https://example.com/b",
            quote: "evidence-b",
          },
          {
            source_id: "src-a",
            anchor_id: "anchor-a",
            title: "Doc A",
            url: "https://example.com/a",
            quote: "evidence-a",
          },
        ],
        citation_anchors: [
          {
            anchor_id: "anchor-a",
            source_id: "src-a",
            number: "1",
            start_char: 5,
          },
          {
            anchor_id: "anchor-b",
            source_id: "src-b",
            number: "2",
            start_char: 42,
          },
        ],
        attachments: [
          {
            attachment_id: "att-1",
            name: "report.json",
            mime_type: "application/json",
            url: "https://example.com/report.json",
          },
        ],
      }),
    );

    expect(result.terminal).toBe(true);

    const assistant = result.messages.find((m) => m.type === "assistant");
    expect(assistant?.streaming).toBe(false);
    expect(
      assistant?.renderParts?.some((p) => p.kind === "inline_citation_group"),
    ).toBe(true);
    expect(assistant?.renderParts?.some((p) => p.kind === "sources")).toBe(
      true,
    );
    expect(assistant?.renderParts?.some((p) => p.kind === "attachments")).toBe(
      true,
    );

    const citationGroup = assistant?.renderParts?.find(
      (p) => p.kind === "inline_citation_group",
    );
    if (citationGroup?.kind === "inline_citation_group") {
      expect(citationGroup.citations[0]?.title).toBe("Doc A");
      expect(citationGroup.citations[0]?.number).toBe("1");
      expect(citationGroup.citations[1]?.title).toBe("Doc B");
      expect(citationGroup.citations[1]?.number).toBe("2");
    }

    const sources = assistant?.renderParts?.find((p) => p.kind === "sources");
    if (sources?.kind === "sources") {
      expect(sources.sources).toHaveLength(2);
      expect(sources.sources[0]?.sourceId).toBe("src-a");
      expect(sources.sources[1]?.sourceId).toBe("src-b");
    }

    const cot = findFirstPart(
      result.messages,
      (p) => p.kind === "chain_of_thought",
    );
    if (cot?.kind === "chain_of_thought") {
      expect(cot.steps.every((step) => step.status === "complete")).toBe(true);
    }

    const queue = findFirstPart(result.messages, (p) => p.kind === "queue");
    if (queue?.kind === "queue") {
      expect(queue.items.every((item) => item.completed)).toBe(true);
    }

    const taskRows = findAllParts(result.messages, (p) => p.kind === "task");
    for (const task of taskRows) {
      if (task.kind === "task") {
        expect(task.status).toBe("completed");
      }
    }

    const finalReasoningRows = traceRows(
      result.messages,
      (part, message) =>
        part.kind === "reasoning" && message.traceSource === "summary",
    );
    expect(finalReasoningRows).toHaveLength(3);

    const summaryLabels = finalReasoningRows.map((row) =>
      row.part.kind === "reasoning" ? row.part.label : undefined,
    );
    expect(summaryLabels).toEqual([
      "thought_0",
      "thought_1",
      "final_reasoning",
    ]);

    const finalReasoning = finalReasoningRows[2]?.part;
    if (finalReasoning?.kind === "reasoning") {
      expect(finalReasoning.parts[0]?.text).toBe(
        "The evidence lines up with the cited sources.",
      );
    }
  });

  it("prefers final_artifact markdown over raw final event JSON text", () => {
    const result = applyWsFrameToMessages(
      [],
      makeEvent(
        "final",
        '{ "final_markdown": "Hello there, it is great to meet you!" }',
        {
          final_artifact: {
            kind: "markdown",
            value: {
              final_markdown: "Hello there, it is great to meet you!",
            },
          },
        },
      ),
    );

    const assistant = result.messages.find(
      (message) => message.type === "assistant",
    );
    expect(assistant?.content).toBe("Hello there, it is great to meet you!");
  });

  it("maps hitl_request and hitl_resolved events to interactive hitl messages", () => {
    const requested = applyWsFrameToMessages(
      [],
      makeEvent("hitl_request", "Need approval", {
        question: "Approve deployment?",
        actions: [
          { label: "Approve", variant: "primary" },
          { label: "Reject", variant: "secondary" },
        ],
      }),
    ).messages;

    const hitl = requested.find((m) => m.type === "hitl");
    expect(hitl?.hitlData?.question).toBe("Approve deployment?");
    expect(hitl?.hitlData?.resolved).toBeUndefined();

    const resolved = applyWsFrameToMessages(
      requested,
      makeEvent("hitl_resolved", "Approved", { resolution: "Approved" }),
    ).messages;

    const resolvedHitl = resolved.find((m) => m.type === "hitl");
    expect(resolvedHitl?.hitlData?.resolved).toBe(true);
    expect(resolvedHitl?.hitlData?.resolvedLabel).toBe("Approved");
  });

  it("resolves the matching HITL message when hitl_resolved includes message_id", () => {
    const first = applyWsFrameToMessages(
      [],
      makeEvent("hitl_request", "Need approval #1", {
        message_id: "hitl-1",
        question: "Approve first?",
      }),
    ).messages;
    const second = applyWsFrameToMessages(
      first,
      makeEvent("hitl_request", "Need approval #2", {
        message_id: "hitl-2",
        question: "Approve second?",
      }),
    ).messages;

    const resolved = applyWsFrameToMessages(
      second,
      makeEvent("hitl_resolved", "Approved second", {
        message_id: "hitl-2",
        resolution: "Approved second",
      }),
    ).messages;

    const hitl1 = resolved.find((m) => m.id === "hitl-1");
    const hitl2 = resolved.find((m) => m.id === "hitl-2");
    expect(hitl1?.hitlData?.resolved).toBeUndefined();
    expect(hitl2?.hitlData?.resolved).toBe(true);
    expect(hitl2?.hitlData?.resolvedLabel).toBe("Approved second");
  });

  it("applies resolve_hitl command acknowledgements to the target HITL message", () => {
    const messages: ChatMessage[] = [
      {
        id: "hitl-1",
        type: "hitl",
        content: "Need approval",
        phase: 1,
        hitlData: {
          question: "Approve?",
          actions: [
            { label: "Approve", variant: "primary" },
            { label: "Reject", variant: "secondary" },
          ],
        },
      },
    ];

    const next = applyWsFrameToMessages(
      messages,
      makeEvent("command_ack", "resolve_hitl completed", {
        command: "resolve_hitl",
        result: {
          status: "ok",
          message_id: "hitl-1",
          resolution: "Approved by reviewer",
        },
      }),
    ).messages;

    const hitl = next.find((m) => m.id === "hitl-1");
    expect(hitl?.hitlData?.resolved).toBe(true);
    expect(hitl?.hitlData?.resolvedLabel).toBe("Approved by reviewer");
  });

  it("rolls back optimistic HITL state when resolve_hitl command is rejected", () => {
    const messages: ChatMessage[] = [
      {
        id: "hitl-2",
        type: "hitl",
        content: "Need approval",
        phase: 1,
        hitlData: {
          question: "Approve?",
          actions: [
            { label: "Approve", variant: "primary" },
            { label: "Reject", variant: "secondary" },
          ],
          resolved: true,
          resolvedLabel: "Approve",
        },
      },
    ];

    const next = applyWsFrameToMessages(
      messages,
      makeEvent("command_reject", "Denied", {
        command: "resolve_hitl",
        result: {
          status: "error",
          message_id: "hitl-2",
          error: "Denied",
        },
      }),
    ).messages;

    const hitl = next.find((m) => m.id === "hitl-2");
    expect(hitl?.hitlData?.resolved).toBe(false);
    expect(hitl?.hitlData?.resolvedLabel).toBeUndefined();
  });
});
