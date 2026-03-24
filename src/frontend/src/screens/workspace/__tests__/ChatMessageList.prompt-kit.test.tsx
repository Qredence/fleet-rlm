import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vite-plus/test";
import { WorkspaceMessageList } from "@/app/workspace/workspace-message-list";
import type { ChatMessage } from "@/screens/workspace/use-workspace";
import { useNavigationStore } from "@/stores/navigationStore";
import { useWorkspaceUiStore } from "@/screens/workspace/use-workspace";

function renderChatMessageList(messages: ChatMessage[]) {
  return renderToStaticMarkup(
    <WorkspaceMessageList
      messages={messages}
      isTyping={false}
      isMobile={false}
      onSuggestionClick={() => {}}
      onResolveHitl={() => {}}
      onResolveClarification={() => {}}
      showHistory={false}
      hasHistory={false}
      historyPanel={null}
    />,
  );
}

function mountChatMessageList(messages: ChatMessage[]) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      <WorkspaceMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );
  });

  return { container, root };
}

describe("WorkspaceMessageList (prompt-kit render parts)", () => {
  beforeEach(() => {
    useNavigationStore.setState({
      isCanvasOpen: false,
      activeNav: "workspace",
    });
    useWorkspaceUiStore.setState({
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectory",
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders assistant zones, standalone trace rows, confirmation, and evidence in the new composition layout", () => {
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
            title: "Trajectory",
            steps: [
              {
                id: "s1",
                index: 0,
                label: "Inspect adapter",
                status: "complete",
                details: [
                  "Tool · read_file",
                  "Input · pattern=WorkspaceMessageList",
                  "Observation · match",
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
            input: { pattern: "WorkspaceMessageList" },
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

    const html = renderChatMessageList(messages);

    expect(html).toContain('data-slot="assistant-trajectory"');
    expect(html).toContain('data-slot="assistant-evidence-preview"');
    expect(html).toContain("Render queue");
    expect(html).toContain("Executing PythonInterpreter");
    expect(html).not.toContain('data-slot="assistant-execution-highlights"');
    expect(html).not.toContain('data-slot="assistant-summary-bar"');
    expect(html).toContain("Python REPL");
    expect(html).toContain("Fleet docs");
    expect(html).toContain("Conversation docs");
    expect(html).toContain("Approve action?");
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

    const html = renderChatMessageList(messages);

    const reasoningIndex = html.indexOf('data-slot="assistant-trajectory"');
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

    const html = renderChatMessageList(messages);

    expect(html.match(/data-slot="assistant-trajectory"/g)?.length).toBe(1);
    expect(html).toContain("Prefix ");
    expect(html).toContain("suffix and final sentence.");
    expect(html).toContain('data-streamdown="inline-code"');
    expect(html).toContain(">code<");
  });

  it("renders answer and compact trajectory zones without exposing backend reasoning labels", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-reasoning-live",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            label: "reasoning",
            parts: [{ type: "text", text: "Investigating the request." }],
            isStreaming: false,
          },
        ],
      },
      {
        id: "assistant-output",
        type: "assistant",
        content: "The result is ready.",
        streaming: false,
      },
      {
        id: "trace-final-reasoning",
        type: "trace",
        content: "final reasoning",
        traceSource: "summary",
        renderParts: [
          {
            kind: "reasoning",
            label: "final_reasoning",
            parts: [{ type: "text", text: "This is the concise final rationale." }],
            isStreaming: false,
          },
        ],
      },
    ];

    const html = renderChatMessageList(messages);

    expect(html).toContain('data-slot="assistant-answer"');
    expect(html).toContain('data-slot="assistant-trajectory"');
    expect(html).not.toContain("final_reasoning");
    expect(html).not.toContain("thought_0");
    expect(html).toContain("The result is ready.");
    expect(html).toContain("This is the concise final rationale.");
  });

  it("renders trajectory summaries in order with synthesis last", () => {
    const messages: ChatMessage[] = [
      {
        id: "assistant-output",
        type: "assistant",
        content: "Architecture extracted.",
        streaming: false,
      },
      {
        id: "trace-thought-1",
        type: "trace",
        content: "trajectory thought",
        traceSource: "summary",
        renderParts: [
          {
            kind: "reasoning",
            label: "thought_1",
            parts: [{ type: "text", text: "Read deeper implementation docs." }],
            isStreaming: false,
          },
        ],
      },
      {
        id: "trace-thought-0",
        type: "trace",
        content: "trajectory thought",
        traceSource: "summary",
        renderParts: [
          {
            kind: "reasoning",
            label: "thought_0",
            parts: [{ type: "text", text: "List the repository first." }],
            isStreaming: false,
          },
        ],
      },
      {
        id: "trace-final-reasoning",
        type: "trace",
        content: "final reasoning",
        traceSource: "summary",
        renderParts: [
          {
            kind: "reasoning",
            label: "final_reasoning",
            parts: [
              {
                type: "text",
                text: "This supports the final architecture summary.",
              },
            ],
            isStreaming: false,
          },
        ],
      },
    ];

    const html = renderChatMessageList(messages);

    const answerIndex = html.indexOf("Architecture extracted.");
    const trajectoryPreviewIndex = html.indexOf('data-slot="assistant-trajectory"');
    const thought0Index = html.indexOf("List the repository first.");
    const thought1Index = html.indexOf("Read deeper implementation docs.");
    const synthesisIndex = html.indexOf("This supports the final architecture summary.");

    expect(answerIndex).toBeGreaterThanOrEqual(0);
    expect(trajectoryPreviewIndex).toBeGreaterThan(answerIndex);
    expect(thought0Index).toBeGreaterThan(trajectoryPreviewIndex);
    expect(thought1Index).toBeGreaterThan(thought0Index);
    expect(synthesisIndex).toBeGreaterThan(thought1Index);
    expect(html).toContain("Architecture extracted.");
    expect(html).toContain("Synthesis");
  });

  it("renders attached execution inside the assistant turn after answer and trajectory", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-reasoning",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Inspecting the workspace." }],
            isStreaming: false,
          },
        ],
      },
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
            stepIndex: 2,
            output: "loaded",
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
            stepIndex: 3,
            input: { path: "archive.md" },
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
            stepIndex: 3,
            output: "loaded again",
          },
        ],
      },
      {
        id: "assistant-final",
        type: "assistant",
        content: "I checked the buffer.",
        streaming: false,
      },
    ];

    const html = renderChatMessageList(messages);

    const answerIndex = html.indexOf('data-slot="assistant-answer"');
    const trajectoryIndex = html.indexOf('data-slot="assistant-trajectory"');
    const executionIndex = html.indexOf('data-slot="assistant-execution-highlights"');

    expect(answerIndex).toBeGreaterThanOrEqual(0);
    expect(executionIndex).toBeGreaterThan(answerIndex);
    expect(trajectoryIndex).toBeGreaterThan(executionIndex);
    expect(html).toContain("Read buffer");
    expect(html).toContain("Read buffer ×2");
    expect(html).toContain("I checked the buffer.");
  });

  it("renders answerless assistant turns using only non-empty zones", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-reasoning-only",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Thinking without a final answer yet." }],
            isStreaming: false,
          },
        ],
      },
    ];

    const html = renderChatMessageList(messages);

    expect(html).toContain('data-slot="assistant-trajectory"');
    expect(html).not.toContain('data-slot="assistant-answer"');
    expect(html).not.toContain('data-slot="assistant-summary-bar"');
  });

  it("opens the inspector for the selected turn from chat sections and the turn card", () => {
    const messages: ChatMessage[] = [
      {
        id: "trace-reasoning",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Inspect the selected response." }],
            isStreaming: false,
          },
        ],
      },
      {
        id: "trace-tool-call",
        type: "trace",
        content: "tool call",
        traceSource: "live",
        renderParts: [
          {
            kind: "tool",
            title: "read_file",
            toolType: "read_file",
            state: "output-available",
            stepIndex: 0,
            input: { path: "README.md" },
            output: "Loaded",
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
            title: "Inspecting supporting docs",
            status: "in_progress",
            items: [{ id: "task-1", text: "Reading README.md" }],
          },
        ],
      },
      {
        id: "assistant-1",
        type: "assistant",
        content: "I checked the docs and left supporting evidence.",
        streaming: false,
        renderParts: [
          {
            kind: "sources",
            title: "Sources",
            sources: [
              {
                sourceId: "src-1",
                kind: "file",
                title: "README.md",
                description: "Project usage notes",
              },
            ],
          },
        ],
      },
    ];

    const { container, root } = mountChatMessageList(messages);

    const trajectorySection = container.querySelector('[data-slot="assistant-trajectory"]');
    const executionPreview = container.querySelector(
      '[data-slot="assistant-execution-highlights"] button',
    );
    const evidencePreview = container.querySelector(
      '[data-slot="assistant-evidence-preview"] button',
    );
    const turnCard = container.querySelector('[data-slot="assistant-turn-content"]');

    expect(container.querySelector('[data-slot="assistant-summary-bar"]')).toBeNull();
    expect(trajectorySection).not.toBeNull();
    expect(executionPreview).not.toBeNull();
    expect(evidencePreview).not.toBeNull();
    expect(turnCard).not.toBeNull();

    act(() => {
      executionPreview?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(useWorkspaceUiStore.getState().selectedAssistantTurnId).toBe("assistant-1");
    expect(useWorkspaceUiStore.getState().activeInspectorTab).toBe("execution");
    expect(useNavigationStore.getState().isCanvasOpen).toBe(true);

    act(() => {
      evidencePreview?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(useWorkspaceUiStore.getState().activeInspectorTab).toBe("evidence");

    const refreshedTurnCard = container.querySelector('[data-slot="assistant-turn-content"]');

    act(() => {
      refreshedTurnCard?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(useWorkspaceUiStore.getState().activeInspectorTab).toBe("trajectory");
    expect(refreshedTurnCard?.className).toContain("border-accent/20");

    act(() => {
      root.unmount();
    });
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
            parts: [{ type: "text", text: "Inspecting host-loop Daytona output." }],
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
            text: "Host-loop session attached",
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

    const html = renderChatMessageList(messages);

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
      <WorkspaceMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
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
      <WorkspaceMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
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
    const reasoningIndex = html.indexOf('data-slot="assistant-trajectory"');
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
      <WorkspaceMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
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
      <WorkspaceMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
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

    const html = renderChatMessageList(messages);

    expect(html).toContain(
      "This is a long reasoning line that must remain fully visible without truncation in compact mode.",
    );
    expect(html).toContain('data-slot="assistant-trajectory"');
    // data-slot="reasoning-inline" was removed in architecture simplification
    expect(html).not.toContain('data-slot="reasoning-inline"');

    // Check for tool and task titles in order
    expect(html).toContain("load_document");
    expect(html).toContain("build_index");
    expect(html).toContain("Executing planner");
    expect(html).toContain("Saved memory");

    // Verify the order: trajectory preview -> tool call -> tool result -> task in_progress -> task completed
    const reasoningTriggerIndex = html.indexOf('data-slot="assistant-trajectory"');
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
      <WorkspaceMessageList
        messages={[]}
        isTyping={true}
        isMobile={false}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    expect(html).toContain("Generating code...");
    expect(html).toContain("Agentic Fleet Session");
  });

  it("renders a pending assistant shell with live trajectory while the turn is still typing", () => {
    const messages: ChatMessage[] = [
      {
        id: "user-1",
        type: "user",
        content: "Inspect the workspace",
      },
      {
        id: "trace-reasoning-1",
        type: "trace",
        content: "reasoning",
        traceSource: "live",
        renderParts: [
          {
            kind: "reasoning",
            parts: [
              {
                type: "text",
                text: "This reasoning should stay fully visible in the active assistant turn.",
              },
            ],
            isStreaming: true,
          },
        ],
      },
    ];

    const html = renderToStaticMarkup(
      <WorkspaceMessageList
        messages={messages}
        isTyping={true}
        isMobile={false}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    expect(html).toContain('data-slot="assistant-turn-content"');
    expect(html).toContain('data-slot="assistant-loading"');
    expect(html).toContain('data-slot="assistant-trajectory"');
    expect(html).toContain(
      "This reasoning should stay fully visible in the active assistant turn.",
    );
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
      <WorkspaceMessageList
        messages={messages}
        isTyping={true}
        isMobile={false}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    expect(html).toContain("Generating code...");
    expect(html).toContain('data-slot="assistant-turn-content"');
  });

  it("bottom-aligns non-empty conversations without using justify-end on the scroll content", () => {
    const messages: ChatMessage[] = [
      {
        id: "user-1",
        type: "user",
        content: "Scroll regression check",
      },
      {
        id: "assistant-1",
        type: "assistant",
        content: "Bottom alignment should not break vertical scrolling.",
        streaming: false,
      },
    ];

    const html = renderToStaticMarkup(
      <WorkspaceMessageList
        messages={messages}
        isTyping={false}
        isMobile={false}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />,
    );

    expect(html).toContain("mt-auto flex flex-col gap-4");
    expect(html).not.toContain("min-h-full justify-end");
  });
});
