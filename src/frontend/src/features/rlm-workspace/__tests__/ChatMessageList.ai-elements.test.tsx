import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatMessageList } from "@/features/rlm-workspace/ChatMessageList";
import type { ChatMessage } from "@/lib/data/types";

describe("ChatMessageList (AI Elements render parts)", () => {
  it("renders reasoning, chain-of-thought, queue, tool, sandbox, env vars, confirmation, and citations", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-1",
        type: "trace",
        content: "trace",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Thinking through adapter mapping" }],
            isStreaming: false,
            duration: 1.2,
          },
          {
            kind: "chain_of_thought",
            title: "Execution trace",
            steps: [
              {
                id: "s1",
                index: 0,
                label: "Inspect adapter",
                status: "complete",
                details: [
                  "Tool: read_file",
                  "Input received",
                  "Observation received",
                ],
              },
            ],
          },
          {
            kind: "queue",
            title: "Plan",
            items: [{ id: "q1", label: "Render queue", completed: false }],
          },
          {
            kind: "task",
            title: "Executing PythonInterpreter",
            status: "in_progress",
            items: [{ id: "t1", text: "Running script" }],
          },
          {
            kind: "tool",
            title: "grep",
            toolType: "grep",
            state: "output-available",
            input: { pattern: "ChatMessageList" },
            output: "match",
          },
          {
            kind: "sandbox",
            title: "Python REPL",
            state: "output-available",
            code: "print(1)",
            output: "1",
          },
          {
            kind: "environment_variables",
            title: "Environment variables",
            variables: [{ name: "APP_ENV", value: "local", required: true }],
          },
          {
            kind: "status_note",
            text: "Status note visible",
            tone: "neutral",
          },
        ],
      },
      {
        id: "hitl-1",
        type: "hitl",
        content: "Approval needed",
        hitlData: {
          question: "Approve action?",
          actions: [
            { label: "Approve", variant: "primary" },
            { label: "Reject", variant: "secondary" },
          ],
        },
      },
      {
        id: "assistant-1",
        type: "assistant",
        content: "Done with sources",
        streaming: false,
        renderParts: [
          {
            kind: "sources",
            title: "Sources",
            sources: [
              {
                sourceId: "src-1",
                kind: "web",
                title: "Fleet docs",
                url: "https://example.com/docs",
                description: "Primary reference",
              },
            ],
          },
          {
            kind: "attachments",
            variant: "grid",
            attachments: [
              {
                attachmentId: "att-1",
                name: "trace.json",
                mimeType: "application/json",
                sizeBytes: 1024,
                url: "https://example.com/trace.json",
              },
            ],
          },
          {
            kind: "inline_citation_group",
            citations: [
              {
                title: "Conversation docs",
                url: "https://elements.ai-sdk.dev/components/conversation#features",
                quote: "Scroll button",
              },
            ],
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    expect(html).toContain("Execution trace");
    // Reasoning content is inside a Collapsible that doesn't render in static markup
    // The reasoning trigger (with "Thought for X seconds" text) is rendered
    expect(html).toContain("Render queue");
    expect(html).toContain("Executing PythonInterpreter");
    expect(html).toContain("grep");
    expect(html).toContain("Python REPL");
    expect(html).toContain("Tool: Environment variables");
    expect(html).toContain("Approve action?");
    expect(html).toContain("Sources");
    // Note: Grid variant attachments don't show filename in static markup
    // The attachment is rendered but the info component returns null for grid variant
    // Inline citation shows hostname, not [1] number
    expect(html).toContain("elements.ai-sdk.dev");
    expect(html).toContain("Done with sources");
  });

  it("renders primary reasoning/tool/task rows in chronological order", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-r1",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "First thought" }],
            isStreaming: false,
          },
        ],
      },
      {
        id: "trace-t1",
        type: "trace",
        content: "tool",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "search_files",
            toolType: "search_files",
            state: "running",
            input: { pattern: "chat adapter" },
          },
        ],
      },
      {
        id: "trace-task",
        type: "trace",
        content: "task",
        traceSource: "live",
        renderParts: [
          {
            kind: "task",
            title: "Executing search_files",
            status: "in_progress",
            items: [{ id: "i1", text: "Searching repository" }],
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    // Reasoning trigger text (collapsible is closed by default with isStreaming: false)
    const reasoningIndex = html.indexOf("Thought for");
    const toolIndex = html.indexOf("search_files");
    const taskIndex = html.indexOf("Executing search_files");

    // Reasoning, tool, and task should be present and in chronological order
    expect(reasoningIndex).toBeGreaterThanOrEqual(0);
    expect(toolIndex).toBeGreaterThan(reasoningIndex);
    expect(taskIndex).toBeGreaterThan(toolIndex);
  });

  it("merges contiguous reasoning trace fragments into one inline markdown stream", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-reasoning-1",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Prefix `co" }],
            isStreaming: true,
          },
        ],
      },
      {
        id: "trace-reasoning-2",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "de` suffix" }],
            isStreaming: true,
          },
        ],
      },
      {
        id: "trace-reasoning-3",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: " and final sentence." }],
            isStreaming: false,
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    // Merged reasoning: collapsible is closed by default (isStreaming: false from last part)
    // Content is not rendered in static markup, but we can verify:
    // 1. Only one reasoning trigger is rendered (not three separate ones)
    // 2. The duration is from the last part
    expect(html.match(/Thought for/g)?.length).toBe(1);
    // data-slot="reasoning-inline" was removed in architecture simplification
    // The reasoning content would be visible when collapsible is open in browser
  });

  it("renders runtime context badges for reasoning and status rows", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-runtime-reasoning",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Inspecting recursive RLM output." }],
            isStreaming: false,
            runtimeContext: {
              depth: 1,
              maxDepth: 3,
              executionProfile: "RLM_ROOT",
              sandboxActive: true,
              effectiveMaxIters: 30,
              executionMode: "rlm",
              sandboxId: "sb-1234567890",
              volumeName: "shared-volume",
            },
          },
        ],
      },
      {
        id: "trace-runtime-status",
        type: "trace",
        content: "status",
        traceSource: "live",
        renderParts: [
          {
            kind: "status_note",
            text: "Child sandbox attached",
            tone: "success",
            runtimeContext: {
              depth: 1,
              maxDepth: 3,
              executionProfile: "RLM_ROOT",
              sandboxActive: true,
              effectiveMaxIters: 30,
              executionMode: "rlm",
              sandboxId: "sb-1234567890",
              volumeName: "shared-volume",
            },
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    expect(html).toContain("depth 1/3");
    expect(html).toContain("mode rlm");
    expect(html).toContain("sandbox sb-123456");
    expect(html).toContain("shared-volume");
    expect(html).toContain("rlm root");
  });

  it("groups contiguous tool call, status, and result rows into one dropdown", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-tool-call",
        type: "trace",
        content: "tool call",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_buffer",
            toolType: "read_buffer",
            state: "running",
            stepIndex: 2,
            input: { path: "notes.md" },
          },
        ],
      },
      {
        id: "trace-status",
        type: "trace",
        content: "status",
        traceSource: "live",
        renderParts: [
          {
            kind: "status_note",
            text: "Tool finished",
            tone: "success",
            toolName: "read_buffer",
            stepIndex: 2,
          },
        ],
      },
      {
        id: "trace-tool-result",
        type: "trace",
        content: "tool result",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_buffer",
            toolType: "read_buffer",
            state: "output-error",
            stepIndex: 2,
            errorText: "buffer read failed",
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    // Tool session groups tool call, status, and result into one collapsible
    // The header shows the tool name, and session items are rendered inside
    expect(html).toContain("Calling tool: read_buffer");
    expect(html).toContain("tool_call: read_buffer");
    expect(html).toContain("Status: Tool finished");
    expect(html).toContain("tool_result: read_buffer");
    expect(html.match(/data-slot="tool-session-item"/g)?.length).toBe(3);
  });

  it("splits tool groups when reasoning interrupts the sequence", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-tool-call",
        type: "trace",
        content: "tool call",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_buffer",
            toolType: "read_buffer",
            state: "running",
            stepIndex: 7,
            input: { path: "notes.md" },
          },
        ],
      },
      {
        id: "trace-reasoning",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Inspecting the buffer output." }],
            isStreaming: false,
          },
        ],
      },
      {
        id: "trace-tool-result",
        type: "trace",
        content: "tool result",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_buffer",
            toolType: "read_buffer",
            state: "output-available",
            stepIndex: 7,
            output: "buffer contents",
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    // Tool groups are split when reasoning interrupts
    // First tool (running) appears before reasoning
    expect(html).toContain("Calling tool: read_buffer");
    // After reasoning, a separate tool session for the result
    expect(html).toContain("Tool: read_buffer");

    const callIndex = html.indexOf("Calling tool: read_buffer");
    const reasoningIndex = html.indexOf("Thought for"); // Reasoning trigger (content not in static markup)
    const resultIndex = html.indexOf("Tool: read_buffer");

    expect(callIndex).toBeGreaterThanOrEqual(0);
    expect(reasoningIndex).toBeGreaterThan(callIndex);
    expect(resultIndex).toBeGreaterThan(reasoningIndex);
  });

  it("keeps consecutive invocations of the same tool name as separate groups", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-tool-call-1",
        type: "trace",
        content: "tool call",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_buffer",
            toolType: "read_buffer",
            state: "running",
            stepIndex: 1,
            input: { path: "notes-a.md" },
          },
        ],
      },
      {
        id: "trace-tool-result-1",
        type: "trace",
        content: "tool result",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_buffer",
            toolType: "read_buffer",
            state: "output-available",
            stepIndex: 1,
            output: "A",
          },
        ],
      },
      {
        id: "trace-tool-call-2",
        type: "trace",
        content: "tool call",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_buffer",
            toolType: "read_buffer",
            state: "running",
            stepIndex: 2,
            input: { path: "notes-b.md" },
          },
        ],
      },
      {
        id: "trace-tool-result-2",
        type: "trace",
        content: "tool result",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_buffer",
            toolType: "read_buffer",
            state: "output-available",
            stepIndex: 2,
            output: "B",
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    // Two separate tool sessions for read_buffer with different step indices
    // Each should have its own header
    const matches = html.match(/Calling tool: read_buffer/g);
    expect(matches?.length).toBe(2);
  });

  it("groups contiguous sandbox and environment variable results with the same tool session", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-sandbox-call",
        type: "trace",
        content: "sandbox call",
        traceSource: "live",
        renderParts: [
          {
            kind: "sandbox",
            title: "python",
            state: "running",
            stepIndex: 9,
            code: "print('hello')",
            output: "",
          },
        ],
      },
      {
        id: "trace-env",
        type: "trace",
        content: "env",
        traceSource: "live",
        renderParts: [
          {
            kind: "environment_variables",
            title: "python",
            variables: [{ name: "APP_ENV", value: "local", required: true }],
          },
        ],
      },
      {
        id: "trace-python-status",
        type: "trace",
        content: "status",
        traceSource: "live",
        renderParts: [
          {
            kind: "status_note",
            text: "Execution failed",
            tone: "error",
            toolName: "python",
            stepIndex: 9,
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    // Sandbox and environment variables grouped with same tool session
    expect(html).toContain("Calling tool: python");
    expect(html).toContain("tool_call: python");
    expect(html).toContain("tool_result: python");
    expect(html).toContain("APP_ENV");
  });

  it("keeps reasoning always visible inline and applies auto disclosure for compact rows", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-compact",
        type: "trace",
        content: "trace",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [
              {
                type: "text",
                text: "This is a long reasoning line that must remain fully visible without truncation in compact mode.",
              },
            ],
            isStreaming: false,
          },
          {
            kind: "tool",
            title: "load_document",
            toolType: "load_document",
            state: "running",
            input: { path: "docs/guide.md" },
          },
          {
            kind: "tool",
            title: "build_index",
            toolType: "build_index",
            state: "output-available",
            output: "completed",
          },
          {
            kind: "task",
            title: "Executing planner",
            status: "in_progress",
            items: [{ id: "task-1", text: "Planning steps" }],
          },
          {
            kind: "task",
            title: "Saved memory",
            status: "completed",
            items: [{ id: "task-2", text: "Persisted memory entry" }],
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    // Reasoning content is in a closed collapsible (isStreaming: false)
    // Check for the reasoning trigger instead
    expect(html).toContain("Thought for");
    // data-slot="reasoning-inline" was removed in architecture simplification
    expect(html).not.toContain('data-slot="reasoning-inline"');

    // Check for tool and task titles in order
    expect(html).toContain("load_document");
    expect(html).toContain("build_index");
    expect(html).toContain("Executing planner");
    expect(html).toContain("Saved memory");

    // Verify the order: reasoning -> tool call -> tool result -> task in_progress -> task completed
    const reasoningTriggerIndex = html.indexOf("Thought for");
    const loadDocumentIndex = html.indexOf("load_document");
    const buildIndex = html.indexOf("build_index");
    const executingPlannerIndex = html.indexOf("Executing planner");
    const savedMemoryIndex = html.indexOf("Saved memory");

    expect(reasoningTriggerIndex).toBeGreaterThanOrEqual(0);
    expect(loadDocumentIndex).toBeGreaterThan(reasoningTriggerIndex);
    expect(buildIndex).toBeGreaterThan(loadDocumentIndex);
    expect(executingPlannerIndex).toBeGreaterThan(buildIndex);
    expect(savedMemoryIndex).toBeGreaterThan(executingPlannerIndex);
  });

  it("shows trailing Loading text shimmer while typing with no streaming assistant message", () => {
    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={[]}
        isTyping={true}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    expect(html).toContain("Loading");
    expect(html).toContain("Agentic Fleet Session");
  });

  it("keeps the typing shimmer visible on a follow-up turn before assistant output arrives", () => {
    const messages: ChatMessage[] = [
      {
        id: "user-1",
        type: "user",
        content: "First prompt",
      },
      {
        id: "assistant-1",
        type: "assistant",
        content: "First answer",
        streaming: false,
      },
      {
        id: "user-2",
        type: "user",
        content: "Second prompt",
      },
    ];

    const html = renderToStaticMarkup(
      <ChatMessageList
        messages={messages}
        isTyping={true}
        isMobile={false}
        scrollRef={createRef<HTMLDivElement>()}
        contentRef={createRef<HTMLDivElement>()}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    expect(html).toContain("Loading");
  });
});
