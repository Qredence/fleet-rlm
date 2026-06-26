import { describe, expect, it } from "vite-plus/test";
import type { ChatMessage, ChatRenderPart } from "@/lib/workspace/workspace-types";
import { appendToolLikePart } from "@/lib/workspace/backend-chat-event-tool-parts";
import { chatRenderPartToAgentToolPart } from "@/lib/workspace/agent-tool-parts";

function appendTracePart(
  messages: ChatMessage[],
  part: ChatRenderPart,
  content?: string,
  traceSource?: ChatMessage["traceSource"],
): ChatMessage[] {
  const lastMessage = messages[messages.length - 1];
  if (lastMessage && lastMessage.type === "trace" && lastMessage.traceSource === traceSource) {
    const copy = [...messages];
    const existingParts = lastMessage.renderParts ?? [];
    copy[messages.length - 1] = {
      ...lastMessage,
      content: content || lastMessage.content,
      renderParts: [...existingParts, part],
    };
    return copy;
  }
  return [
    ...messages,
    {
      id: `trace-${Date.now()}`,
      type: "trace",
      content: content || "",
      traceSource: traceSource ?? "live",
      renderParts: [part],
    },
  ];
}

function makeTraceMessages(): ChatMessage[] {
  return [
    {
      id: "trace-1",
      type: "trace",
      content: "",
      traceSource: "live",
      renderParts: [],
    },
  ];
}

describe("sandboxFromPayload - top-level code extraction", () => {
  it("reads payload.code_preview at top level (VAL-A-001)", () => {
    const messages = makeTraceMessages();
    const payload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      code_preview: "def fibonacci(n):\n    fibs = [0, 1]\n    for i in range(2, n):\n        fibs.append(fibs[-1] + fibs[-2])\n    return fibs",
      step: {
        type: "repl",
        label: "repl_execute",
        input: {},
        output: "",
      },
    };

    const result = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      payload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = result.find((m) => m.type === "trace");
    const sandboxPart = traceMessage?.renderParts?.find(
      (p) => p.kind === "sandbox",
    ) as Extract<ChatRenderPart, { kind: "sandbox" }> | undefined;

    expect(sandboxPart).toBeDefined();
    expect(sandboxPart?.code).toBe(
      "def fibonacci(n):\n    fibs = [0, 1]\n    for i in range(2, n):\n        fibs.append(fibs[-1] + fibs[-2])\n    return fibs",
    );
    expect(sandboxPart?.code).not.toBe("repl_execute");
  });

  it("reads payload.code at top level when code_preview absent (VAL-A-001)", () => {
    const messages = makeTraceMessages();
    const payload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      code: "print('hello world')",
      step: {
        type: "repl",
        label: "repl_execute",
        input: {},
        output: "",
      },
    };

    const result = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      payload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = result.find((m) => m.type === "trace");
    const sandboxPart = traceMessage?.renderParts?.find(
      (p) => p.kind === "sandbox",
    ) as Extract<ChatRenderPart, { kind: "sandbox" }> | undefined;

    expect(sandboxPart).toBeDefined();
    expect(sandboxPart?.code).toBe("print('hello world')");
  });

  it("prefers payload.code_preview over payload.code (VAL-A-003)", () => {
    const messages = makeTraceMessages();
    const payload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      code_preview: "def fibonacci(n):\n    return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)",
      code: "short",
      step: {
        type: "repl",
        label: "repl_execute",
        input: {},
        output: "",
      },
    };

    const result = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      payload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = result.find((m) => m.type === "trace");
    const sandboxPart = traceMessage?.renderParts?.find(
      (p) => p.kind === "sandbox",
    ) as Extract<ChatRenderPart, { kind: "sandbox" }> | undefined;

    expect(sandboxPart?.code).toBe(
      "def fibonacci(n):\n    return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)",
    );
    expect(sandboxPart?.code).not.toBe("short");
  });

  it("still extracts from nested step.input.code (no regression, VAL-A-002)", () => {
    const messages = makeTraceMessages();
    const payload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      step: {
        type: "repl",
        label: "repl_execute",
        input: {
          code: "x = 42\nprint(x)",
        },
        output: "42",
      },
    };

    const result = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      payload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = result.find((m) => m.type === "trace");
    const sandboxPart = traceMessage?.renderParts?.find(
      (p) => p.kind === "sandbox",
    ) as Extract<ChatRenderPart, { kind: "sandbox" }> | undefined;

    expect(sandboxPart).toBeDefined();
    expect(sandboxPart?.code).toBe("x = 42\nprint(x)");
  });

  it("still extracts from step.input as string (no regression, VAL-A-002)", () => {
    const messages = makeTraceMessages();
    const payload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      step: {
        type: "repl",
        label: "repl_execute",
        input: "ls -la",
        output: "",
      },
    };

    const result = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      payload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = result.find((m) => m.type === "trace");
    const sandboxPart = traceMessage?.renderParts?.find(
      (p) => p.kind === "sandbox",
    ) as Extract<ChatRenderPart, { kind: "sandbox" }> | undefined;

    expect(sandboxPart).toBeDefined();
    expect(sandboxPart?.code).toBe("ls -la");
  });

  it("still extracts from tool_args.code (no regression, VAL-A-002)", () => {
    const messages = makeTraceMessages();
    const payload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      tool_args: {
        code: "import os\nprint(os.getcwd())",
      },
      step: {
        type: "repl",
        label: "repl_execute",
      },
    };

    const result = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      payload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = result.find((m) => m.type === "trace");
    const sandboxPart = traceMessage?.renderParts?.find(
      (p) => p.kind === "sandbox",
    ) as Extract<ChatRenderPart, { kind: "sandbox" }> | undefined;

    expect(sandboxPart).toBeDefined();
    expect(sandboxPart?.code).toBe("import os\nprint(os.getcwd())");
  });

  it("top-level code_preview takes precedence over nested step.input.code (VAL-A-003)", () => {
    const messages = makeTraceMessages();
    const payload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      code_preview: "def top_level():\n    pass",
      step: {
        type: "repl",
        label: "repl_execute",
        input: {
          code: "def nested():\n    pass",
        },
        output: "",
      },
    };

    const result = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      payload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = result.find((m) => m.type === "trace");
    const sandboxPart = traceMessage?.renderParts?.find(
      (p) => p.kind === "sandbox",
    ) as Extract<ChatRenderPart, { kind: "sandbox" }> | undefined;

    expect(sandboxPart?.code).toBe("def top_level():\n    pass");
  });
});

describe("commandInput - no title fallback (VAL-A-004)", () => {
  it("returns empty command when part.code is empty, not part.title", () => {
    const sandboxPart: Extract<ChatRenderPart, { kind: "sandbox" }> = {
      kind: "sandbox",
      title: "repl_execute",
      state: "running",
      code: "",
      output: "some output",
      language: "text",
    };

    const agentPart = chatRenderPartToAgentToolPart(sandboxPart, "msg-1", 0);

    expect(agentPart).toBeDefined();
    expect(agentPart?.type).toBe("tool-Bash");
    expect(agentPart?.input).toEqual({
      command: "",
      description: "repl_execute",
      language: "text",
    });
  });

  it("returns empty command when part.code is undefined, not part.title", () => {
    const sandboxPart: Extract<ChatRenderPart, { kind: "sandbox" }> = {
      kind: "sandbox",
      title: "Sandbox stdout",
      state: "running",
      output: "streaming output...",
      language: "text",
    };

    const agentPart = chatRenderPartToAgentToolPart(sandboxPart, "msg-1", 0);

    expect(agentPart).toBeDefined();
    expect((agentPart?.input as Record<string, unknown>)?.command).toBe("");
    expect((agentPart?.input as Record<string, unknown>)?.command).not.toBe("Sandbox stdout");
  });

  it("returns actual code when part.code is present", () => {
    const sandboxPart: Extract<ChatRenderPart, { kind: "sandbox" }> = {
      kind: "sandbox",
      title: "repl_execute",
      state: "output-available",
      code: "print('hello')",
      output: "hello",
      language: "python",
    };

    const agentPart = chatRenderPartToAgentToolPart(sandboxPart, "msg-1", 0);

    expect(agentPart).toBeDefined();
    expect((agentPart?.input as Record<string, unknown>)?.command).toBe("print('hello')");
    expect((agentPart?.input as Record<string, unknown>)?.language).toBe("python");
  });
});

describe("Language detection unchanged (VAL-A-005)", () => {
  it("preserves language field from sandbox part", () => {
    const sandboxPart: Extract<ChatRenderPart, { kind: "sandbox" }> = {
      kind: "sandbox",
      title: "repl_execute",
      state: "output-available",
      code: "print('hi')",
      output: "hi",
      language: "python",
    };

    const agentPart = chatRenderPartToAgentToolPart(sandboxPart, "msg-1", 0);

    expect(agentPart).toBeDefined();
    expect((agentPart?.input as Record<string, unknown>)?.language).toBe("python");
    expect(agentPart?.toolName).toBe("repl_execute");
  });

  it("preserves bash language for shell commands", () => {
    const sandboxPart: Extract<ChatRenderPart, { kind: "sandbox" }> = {
      kind: "sandbox",
      title: "repl_execute",
      state: "output-available",
      code: "ls -la",
      output: "total 0",
      language: "bash",
    };

    const agentPart = chatRenderPartToAgentToolPart(sandboxPart, "msg-1", 0);

    expect(agentPart).toBeDefined();
    expect((agentPart?.input as Record<string, unknown>)?.language).toBe("bash");
  });
});

describe("getBetterCode upsert path (VAL-A-022)", () => {
  it("merges tool_call and tool_result with better code preserved", () => {
    const messages = makeTraceMessages();

    // First frame: tool_call with code_preview
    const callPayload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      code_preview: "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a",
      step: {
        type: "repl",
        label: "repl_execute",
        input: {},
        output: "",
      },
      step_index: 0,
    };

    const afterCall = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      callPayload,
      appendTracePart,
      { traceSource: "live" },
    );

    // Second frame: tool_result with same step_index (should merge, not duplicate)
    const resultPayload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      code_preview: "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a",
      step: {
        type: "repl",
        label: "repl_execute",
        input: {
          code: "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a",
        },
        output: "0 1 1 2 3",
      },
      step_index: 0,
    };

    const afterResult = appendToolLikePart(
      afterCall,
      "tool_result",
      "repl_execute",
      resultPayload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = afterResult.find((m) => m.type === "trace");
    const sandboxParts = traceMessage?.renderParts?.filter((p) => p.kind === "sandbox") ?? [];

    // Should be exactly one merged sandbox part, not two
    expect(sandboxParts).toHaveLength(1);

    const mergedPart = sandboxParts[0] as Extract<ChatRenderPart, { kind: "sandbox" }>;
    // The code should be the longer/better version, not empty or title
    expect(mergedPart.code).toContain("def fib");
    expect(mergedPart.code).not.toBe("repl_execute");
  });

  it("does not produce duplicate sandbox cards for same step", () => {
    const messages = makeTraceMessages();

    const callPayload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      code: "x = 1",
      step: { type: "repl", label: "repl_execute" },
      step_index: 2,
    };

    const afterCall = appendToolLikePart(
      messages,
      "tool_call",
      "repl_execute",
      callPayload,
      appendTracePart,
      { traceSource: "live" },
    );

    const resultPayload = {
      source_type: "sandbox_exec",
      tool_name: "repl_execute",
      code: "x = 1\nprint(x)",
      step: { type: "repl", label: "repl_execute", output: "1" },
      step_index: 2,
    };

    const afterResult = appendToolLikePart(
      afterCall,
      "tool_result",
      "repl_execute",
      resultPayload,
      appendTracePart,
      { traceSource: "live" },
    );

    const traceMessage = afterResult.find((m) => m.type === "trace");
    const sandboxParts = traceMessage?.renderParts?.filter((p) => p.kind === "sandbox") ?? [];

    expect(sandboxParts).toHaveLength(1);
    // Merged code should prefer the longer version
    const mergedPart = sandboxParts[0] as Extract<ChatRenderPart, { kind: "sandbox" }>;
    expect(mergedPart.code).toBe("x = 1\nprint(x)");
  });
});
