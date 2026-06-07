import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi, afterEach } from "vite-plus/test";

vi.mock("lottie-react", () => ({
  default: {
    default: function LottieMock() {
      return <div data-testid="lottie-mock" />;
    },
  },
  "module.exports": undefined,
}));

import { WorkspaceMessageList } from "@/features/workspace/conversation/transcript/workspace-message-list";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

function mount(
  messages: ChatMessage[],
  options?: Partial<Parameters<typeof WorkspaceMessageList>[0]>,
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const props = {
    messages,
    isTyping: false,
    isMobile: false,
    showEmptyState: false,
    onSuggestionClick: vi.fn(),
    onResolveHitl: vi.fn(),
    onResolveClarification: vi.fn(),
    value: "",
    onChange: vi.fn(),
    onSend: vi.fn(),
    executionMode: "auto" as const,
    onExecutionModeChange: vi.fn(),
    ...options,
  };

  act(() => {
    root.render(<WorkspaceMessageList {...props} />);
  });

  return { container, root, props };
}

describe("WorkspaceMessageList Agent Elements integration", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders assistant text, reasoning, and tools through AgentChat", () => {
    const { container, root } = mount([
      { id: "u1", type: "user", content: "inspect" },
      {
        id: "trace-1",
        type: "trace",
        content: "",
        renderParts: [
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Reading the workspace" }],
            isStreaming: false,
          },
          {
            kind: "tool",
            title: "bash",
            toolType: "bash",
            state: "output-available",
            input: { command: "ls" },
            output: "src",
          },
        ],
      },
      { id: "a1", type: "assistant", content: "Found src", streaming: false },
    ]);

    expect(container.textContent).toContain("inspect");
    expect(container.textContent).toContain("Thought");
    expect(container.textContent).toContain("Ran command");
    expect(container.textContent).toContain("Found src");

    act(() => root.unmount());
  });

  it("renders HITL as Agent Elements QuestionTool and dispatches approval", () => {
    const onResolveHitl = vi.fn();
    const { container, root } = mount(
      [
        {
          id: "hitl-1",
          type: "hitl",
          content: "Approval needed",
          hitlData: {
            question: "Approve command?",
            actions: [
              { label: "Approve", variant: "primary" },
              { label: "Reject", variant: "secondary" },
            ],
          },
        },
      ],
      { onResolveHitl },
    );

    const approveButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Approve"),
    );
    expect(approveButton).toBeTruthy();

    act(() => {
      approveButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const sendButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Send"),
    );
    expect(sendButton).toBeTruthy();

    act(() => {
      sendButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onResolveHitl).toHaveBeenCalledWith("hitl-1", "Approve");

    act(() => root.unmount());
  });

  it("maps file and search events to the expected Agent Elements cards", () => {
    const { container, root } = mount([
      { id: "u1", type: "user", content: "inspect the workspace" },
      {
        id: "trace-2",
        type: "trace",
        content: "",
        renderParts: [
          {
            kind: "status_note",
            text: "Execution started",
            tone: "neutral",
          },
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "I should inspect the repository files." }],
            isStreaming: false,
          },
          {
            kind: "tool",
            title: "List files",
            toolType: "list_files",
            state: "output-available",
            input: { path: "src/frontend", pattern: "src/frontend/**/*" },
            output: { results: [] },
          },
          {
            kind: "tool",
            title: "Load document",
            toolType: "load_document",
            state: "output-available",
            input: { path: "README.md", alias: "active" },
            output: { text: "README content" },
          },
          {
            kind: "tool",
            title: "Read file slice",
            toolType: "read_file_slice",
            state: "output-available",
            input: { path: "src/app.tsx", start_line: 1, num_lines: 40 },
            output: { text: "slice content" },
          },
        ],
      },
      {
        id: "a1",
        type: "assistant",
        content: "Done inspecting.",
        streaming: false,
      },
    ]);

    expect(container.textContent).toContain("Execution started");
    expect(container.textContent).toContain("Thought");
    expect(container.textContent).toContain("Found 0 results");
    expect(container.textContent).toContain("README.md");
    expect(container.textContent).toContain("app.tsx");
    expect(container.textContent).toContain("Done inspecting.");

    act(() => root.unmount());
  });

  it("renders delegated agent work through the Agent Elements SubagentTool path", () => {
    const { container, root } = mount([
      { id: "u1", type: "user", content: "delegate this" },
      {
        id: "trace-agent",
        type: "trace",
        content: "",
        renderParts: [
          {
            kind: "tool",
            title: "Delegate",
            toolType: "delegate_agent",
            state: "output-available",
            input: {
              subagent_type: "Research agent",
              description: "Inspect the RLM trajectory",
            },
            output: { status: "completed" },
          },
        ],
      },
    ]);

    expect(container.textContent).toContain("Research agent completed");

    act(() => root.unmount());
  });

  it("renders forced and URL RLM event rows through Agent Elements", () => {
    const { container, root } = mount([
      { id: "u1", type: "user", content: "Analyze https://example.com" },
      {
        id: "trace-rlm",
        type: "trace",
        content: "",
        renderParts: [
          {
            kind: "status_note",
            text: "Route: url_document_rlm | source: https://example.com",
            tone: "neutral",
          },
          {
            kind: "status_note",
            text: "Execution started",
            tone: "neutral",
          },
          {
            kind: "reasoning",
            parts: [{ type: "text", text: "Summarize the fetched page" }],
            isStreaming: false,
          },
          {
            kind: "sandbox",
            title: "summary",
            state: "output-available",
            code: "print('Example Domain')",
            output: "Example Domain",
            language: "python",
          },
          {
            kind: "tool",
            title: "mcp__docs__fetch",
            toolType: "mcp__docs__fetch",
            state: "output-available",
            input: { url: "https://example.com" },
            output: { text: "Example Domain" },
          },
        ],
      },
      {
        id: "a1",
        type: "assistant",
        content: "- Example Domain is reserved for examples.",
        streaming: false,
      },
    ]);

    expect(container.textContent).toContain("Route: url_document_rlm");
    expect(container.textContent).toContain("Execution started");
    expect(container.textContent).toContain("Thought");
    expect(container.textContent).toContain("Ran command");
    expect(container.textContent).toContain("Example Domain");
    expect(container.textContent).toContain("Fetched");
    expect(container.textContent).toContain("example.com");
    expect(container.textContent).toContain("Example Domain is reserved");

    act(() => root.unmount());
  });

  it("opens the attachment menu and stages a document chip", () => {
    const { container, root } = mount([]);

    const attachButton = container.querySelector('button[aria-label="Attach"]');
    expect(attachButton).toBeTruthy();

    act(() => {
      attachButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const addDocumentButton = Array.from(document.body.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Add document"),
    );
    const connectorsButton = Array.from(document.body.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Connectors"),
    );
    expect(addDocumentButton).toBeTruthy();
    expect(connectorsButton).toBeTruthy();
    expect(connectorsButton).toHaveProperty("disabled", true);

    act(() => {
      addDocumentButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(fileInput).toBeTruthy();
    Object.defineProperty(fileInput, "files", {
      configurable: true,
      value: [new File(["hello"], "notes.md", { type: "text/markdown" })],
    });

    act(() => {
      fileInput?.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(container.textContent).toContain("notes.md");

    act(() => root.unmount());
  });

  it("renders the runtime model picker from active model status", () => {
    const onOpenModelSettings = vi.fn();
    const { container, root } = mount([], {
      activeModels: {
        planner: "openai/gemini-3-flash-preview",
        delegate: "openai/gemini-3-pro-preview",
        delegate_small: null,
      },
      onOpenModelSettings,
    });

    expect(container.textContent).toContain("openai/gemini-3-flash-preview");

    const modelButton = container.querySelector('button[aria-label^="Active model"]');
    expect(modelButton).toBeTruthy();

    act(() => {
      modelButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(document.body.textContent).toContain("openai/gemini-3-pro-preview");

    const settingsButton = Array.from(document.body.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Model settings"),
    );
    expect(settingsButton).toBeTruthy();

    act(() => {
      settingsButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onOpenModelSettings).toHaveBeenCalledOnce();

    act(() => root.unmount());
  });

  it("renders the pending planning loader without a lazy component crash", async () => {
    const { container, root } = mount([{ id: "u1", type: "user", content: "start working" }], {
      isTyping: true,
    });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain("start working");
    expect(container.textContent).toContain("Processing...");

    act(() => root.unmount());
  });
});
