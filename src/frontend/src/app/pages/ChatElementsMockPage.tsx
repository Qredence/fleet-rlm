import { createRef } from "react";
import { ChatMessageList } from "@/app/pages/skill-creation/ChatMessageList";
import type { ChatMessage } from "@/lib/data/types";

const mockMessages: ChatMessage[] = [
  {
    id: "u1",
    type: "user",
    content: "Analyze this repo and explain the architecture.",
    phase: 1,
  },
  {
    id: "r1",
    type: "reasoning",
    content: "",
    phase: 1,
    reasoningData: {
      parts: [
        { type: "text", text: "Inspecting route configuration and chat pipeline." },
        { type: "text", text: "Normalizing WebSocket events into rich render parts." },
      ],
      isThinking: false,
      duration: 2.4,
    },
    renderParts: [
      {
        kind: "reasoning",
        parts: [
          { type: "text", text: "Inspecting route configuration and chat pipeline." },
          { type: "text", text: "Normalizing WebSocket events into rich render parts." },
        ],
        isStreaming: false,
        duration: 2.4,
      },
    ],
  },
  {
    id: "t1",
    type: "trace",
    content: "Execution trace",
    phase: 1,
    renderParts: [
      {
        kind: "chain_of_thought",
        title: "Execution trace",
        steps: [
          {
            id: "cot-1",
            label: "Inspect routes",
            status: "complete",
            details: ["Tool: read_file", "Observation: SkillCreationFlow mounted on index route"],
          },
          {
            id: "cot-2",
            label: "Patch event adapter",
            status: "active",
            details: ["Tool: edit_file", "Observation: renderParts preserved for tool/trajectory events"],
          },
        ],
      },
      {
        kind: "queue",
        title: "Plan",
        items: [
          { id: "q1", label: "Add streamdown wrapper", completed: true },
          { id: "q2", label: "Render AI Elements components", completed: true },
          { id: "q3", label: "Add visual mock route for Playwright", completed: false },
        ],
      },
      {
        kind: "task",
        title: "Executing PythonInterpreter",
        status: "in_progress",
        items: [
          { id: "task-1", text: "Generate fixture transcript" },
          { id: "task-2", text: "Capture screenshot", file: { name: "chat-elements.png" } },
        ],
      },
      {
        kind: "tool",
        title: "grep",
        toolType: "grep",
        state: "output-available",
        input: { pattern: "backendChatEventAdapter", path: "src/frontend/src" },
        output: "src/frontend/src/app/pages/skill-creation/backendChatEventAdapter.ts:643:export function applyWsFrameToMessages",
      },
      {
        kind: "sandbox",
        title: "Python REPL",
        state: "output-available",
        code: "print('hello from sandbox')",
        output: "hello from sandbox",
        language: "python",
      },
      {
        kind: "environment_variables",
        title: "Runtime environment",
        variables: [
          { name: "APP_ENV", value: "local", required: true },
          { name: "POSTHOG_ENABLED", value: "true" },
          { name: "LITELLM_API_BASE", value: "https://proxy.example.com/v1" },
        ],
      },
    ],
  },
  {
    id: "h1",
    type: "hitl",
    content: "Approval requested",
    phase: 1,
    hitlData: {
      question: "Apply these runtime setting changes to local environment?",
      actions: [
        { label: "Approve", variant: "primary" },
        { label: "Reject", variant: "secondary" },
      ],
    },
  },
  {
    id: "a1",
    type: "assistant",
    content:
      "I updated the chat renderer to use AI Elements conversation, reasoning, chain-of-thought, queue, task, tool, sandbox, and env var components.",
    phase: 1,
    streaming: false,
    renderParts: [
      {
        kind: "inline_citation_group",
        citations: [
          {
            title: "AI Elements Conversation",
            url: "https://elements.ai-sdk.dev/components/conversation#features",
            description: "Conversation scroll and empty state patterns.",
            quote: "Automatic scroll-to-bottom and scroll button support.",
          },
          {
            title: "AI Elements Reasoning",
            url: "https://elements.ai-sdk.dev/components/reasoning#reasoning-vs-chain-of-thought",
            description: "Reasoning vs ChainOfThought semantics.",
          },
        ],
      },
    ],
  },
];

export function ChatElementsMockPage() {
  const scrollRef = createRef<HTMLDivElement>();
  const contentRef = createRef<HTMLDivElement>();

  return (
    <div className="flex h-full min-h-screen flex-col bg-background">
      <div className="border-b border-border-subtle px-6 py-4 text-sm text-muted-foreground">
        Dev mock route: deterministic AI Elements chat transcript for visual QA
      </div>
      <ChatMessageList
        messages={mockMessages}
        isTyping={false}
        isMobile={false}
        scrollRef={scrollRef}
        contentRef={contentRef}
        isAtBottom={true}
        scrollToBottom={() => {}}
        onSuggestionClick={() => {}}
        onResolveHitl={() => {}}
        onResolveClarification={() => {}}
        showHistory={false}
        hasHistory={false}
        historyPanel={null}
      />
    </div>
  );
}
