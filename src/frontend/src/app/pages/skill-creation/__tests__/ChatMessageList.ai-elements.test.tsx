import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatMessageList } from "@/app/pages/skill-creation/ChatMessageList";
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
            steps: [{ id: "s1", label: "Inspect adapter", status: "complete" }],
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
    expect(html).toContain("Render queue");
    expect(html).toContain("Executing PythonInterpreter");
    expect(html).toContain("grep");
    expect(html).toContain("Python REPL");
    expect(html).toContain("APP_ENV");
    expect(html).toContain("Approve action?");
    expect(html).toContain("Sources");
    expect(html).toContain("trace.json");
    expect(html).toContain("[1]");
    expect(html).toContain("Done with sources");
  });

  it("shows shimmer while typing with no streaming assistant message", () => {
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

    expect(html).toContain('data-slot="shimmer"');
    expect(html).toContain("Agentic Fleet Session");
  });
});
