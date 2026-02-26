import { describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { applyWsFrameToMessages } from "@/app/pages/skill-creation/backendChatEventAdapter";
import type { ChatMessage, ChatRenderPart } from "@/lib/data/types";
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

  it("creates reasoning render parts for reasoning_step events", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("reasoning_step", "Analyzing input"),
    );
    const reasoning = messages.find((m) => m.type === "reasoning");
    expect(reasoning?.reasoningData?.isThinking).toBe(true);
    expect(reasoning?.renderParts?.[0]?.kind).toBe("reasoning");
    if (reasoning?.renderParts?.[0]?.kind === "reasoning") {
      expect(reasoning.renderParts[0].parts[0]?.text).toBe("Analyzing input");
    }
  });

  it("maps trajectory_step to a chain_of_thought trace render part", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("trajectory_step", "trace", {
        step_index: 0,
        step_data: {
          thought: "Read file",
          tool_name: "read_file",
          observation: "Found entrypoint",
        },
      }),
    );

    const cot = findFirstPart(messages, (p) => p.kind === "chain_of_thought");
    expect(cot).toBeDefined();
    if (cot?.kind === "chain_of_thought") {
      expect(cot.steps).toHaveLength(1);
      expect(cot.steps[0]?.label).toContain("Read file");
      expect(cot.steps[0]?.status).toBe("active");
    }
  });

  it("maps plan_update to a queue trace part and closes reasoning", () => {
    let msgs: ChatMessage[] = [];
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("reasoning_step", "Thinking..."),
    ).messages;
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("plan_update", "Moving to step 2"),
    ).messages;

    const queue = findFirstPart(msgs, (p) => p.kind === "queue");
    expect(queue).toBeDefined();
    if (queue?.kind === "queue") {
      expect(queue.items[queue.items.length - 1]?.label).toBe(
        "Moving to step 2",
      );
    }

    const reasoning = msgs.find((m) => m.type === "reasoning");
    expect(reasoning?.reasoningData?.isThinking).toBe(false);
  });

  it("maps rlm_executing to a task trace part", () => {
    const { messages } = applyWsFrameToMessages(
      [],
      makeEvent("rlm_executing", "Delegating", {
        tool_name: "PythonInterpreter",
      }),
    );

    const task = findFirstPart(messages, (p) => p.kind === "task");
    expect(task).toBeDefined();
    if (task?.kind === "task") {
      expect(task.title).toContain("PythonInterpreter");
      expect(task.status).toBe("in_progress");
    }
  });

  it("maps tool_call/tool_result to a tool render part and updates it in place", () => {
    let msgs: ChatMessage[] = [];
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("tool_call", "Running tool", {
        tool_name: "grep",
        tool_args: { pattern: "foo" },
      }),
    ).messages;

    let tool = findFirstPart(msgs, (p) => p.kind === "tool");
    expect(tool).toBeDefined();
    if (tool?.kind === "tool") {
      expect(tool.state).toBe("running");
      expect(tool.toolType).toBe("grep");
    }

    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("tool_result", "Done", {
        tool_name: "grep",
        tool_output: "match line",
      }),
    ).messages;

    const tools = msgs.flatMap((m) =>
      (m.renderParts ?? []).filter((p) => p.kind === "tool"),
    );
    expect(tools).toHaveLength(1);
    tool = tools[0];
    if (tool?.kind === "tool") {
      expect(tool.state).toBe("output-available");
      expect(String(tool.output)).toContain("match line");
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

  it("final closes reasoning and finalizes trace parts and attaches citations", () => {
    let msgs: ChatMessage[] = [];
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("assistant_token", "Hello"),
    ).messages;
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("reasoning_step", "Thinking"),
    ).messages;
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("trajectory_step", "trace", {
        step_index: 0,
        step_data: { thought: "step one" },
      }),
    ).messages;
    msgs = applyWsFrameToMessages(
      msgs,
      makeEvent("plan_update", "Do X"),
    ).messages;

    const { messages, terminal } = applyWsFrameToMessages(
      msgs,
      makeEvent("final", "Done", {
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

    expect(terminal).toBe(true);
    const assistant = messages.find((m) => m.type === "assistant");
    expect(assistant?.streaming).toBe(false);
    expect(
      assistant?.renderParts?.some((p) => p.kind === "inline_citation_group"),
    ).toBe(true);
    expect(assistant?.renderParts?.some((p) => p.kind === "sources")).toBe(
      true,
    );
    expect(
      assistant?.renderParts?.some((p) => p.kind === "attachments"),
    ).toBe(true);
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

    const reasoning = messages.find((m) => m.type === "reasoning");
    expect(reasoning?.reasoningData?.isThinking).toBe(false);

    const cot = findFirstPart(messages, (p) => p.kind === "chain_of_thought");
    if (cot?.kind === "chain_of_thought") {
      expect(cot.steps[0]?.status).toBe("complete");
    }
    const queue = findFirstPart(messages, (p) => p.kind === "queue");
    if (queue?.kind === "queue") {
      expect(queue.items.every((item) => item.completed)).toBe(true);
    }
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
