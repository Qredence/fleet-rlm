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
    expect(html).toContain("Thinking through adapter mapping");
    expect(html).toContain('data-slot="reasoning-inline"');
    expect(html).toContain("Render queue");
    expect(html).toContain("Executing PythonInterpreter");
    expect(html).toContain("grep");
    expect(html).toContain("Python REPL");
    expect(html).toContain("Tool: Environment variables");
    expect(html).toContain("Approve action?");
    expect(html).toContain("Sources");
    expect(html).toContain("trace.json");
    expect(html).toContain("[1]");
    expect(html).toContain("Done with sources");
    expect(html.match(/data-slot="sources-trigger"/g)?.length).toBe(1);
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

    const reasoningIndex = html.indexOf("First thought");
    const toolIndex = html.indexOf("search_files");
    const taskIndex = html.indexOf("Executing search_files");

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

    expect(html).toContain("Prefix ");
    expect(html).toContain("suffix and final sentence.");
    expect(html).toContain('data-streamdown="inline-code"');
    expect(html.match(/data-slot="reasoning-inline"/g)?.length).toBe(1);
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

    expect(
      html.match(
        /aria-label="Calling tool: read_buffer tool \(output error\)"/g,
      )?.length,
    ).toBe(1);
    expect(html).toContain("tool_call: read_buffer");
    expect(html).toContain("Status: Tool finished");
    expect(html).toContain("tool_result: read_buffer");
    expect(html).toContain("font-size:var(--text-base)");
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

    expect(
      html.match(/aria-label="Calling tool: read_buffer tool \(running\)"/g)
        ?.length,
    ).toBe(1);
    expect(
      html.match(/aria-label="Tool: read_buffer tool \(output available\)"/g)
        ?.length,
    ).toBe(1);

    const callIndex = html.indexOf("Calling tool: read_buffer");
    const reasoningIndex = html.indexOf("Inspecting the buffer output.");
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

    expect(
      html.match(
        /aria-label="Calling tool: read_buffer tool \(output available\)"/g,
      )?.length,
    ).toBe(2);
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

    expect(
      html.match(/aria-label="Calling tool: python tool \(output error\)"/g)
        ?.length,
    ).toBe(1);
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

    expect(html).toContain(
      "This is a long reasoning line that must remain fully visible without truncation in compact mode.",
    );
    expect(html).toContain('data-slot="reasoning-inline"');

    const loadDocumentLabel =
      'aria-label="Calling tool: load_document tool (running)"';
    const buildIndexLabel =
      'aria-label="Tool: build_index tool (output available)"';
    const executingPlannerLabel =
      'aria-label="Executing planner (in progress)"';
    const savedMemoryLabel = 'aria-label="Saved memory (completed)"';

    const loadDocumentIndex = html.indexOf(loadDocumentLabel);
    const buildIndex = html.indexOf(buildIndexLabel);
    const executingPlannerIndex = html.indexOf(executingPlannerLabel);
    const savedMemoryIndex = html.indexOf(savedMemoryLabel);

    expect(loadDocumentIndex).toBeGreaterThanOrEqual(0);
    expect(buildIndex).toBeGreaterThanOrEqual(0);
    expect(executingPlannerIndex).toBeGreaterThanOrEqual(0);
    expect(savedMemoryIndex).toBeGreaterThanOrEqual(0);

    expect(
      html.slice(Math.max(0, loadDocumentIndex - 220), loadDocumentIndex),
    ).toContain('data-state="open"');
    expect(html.slice(Math.max(0, buildIndex - 220), buildIndex)).toContain(
      'data-state="closed"',
    );
    expect(
      html.slice(
        Math.max(0, executingPlannerIndex - 220),
        executingPlannerIndex,
      ),
    ).toContain('data-state="open"');
    expect(
      html.slice(Math.max(0, savedMemoryIndex - 220), savedMemoryIndex),
    ).toContain('data-state="closed"');
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
