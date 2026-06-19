import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ChatStatus, UIMessage } from "ai";
import { afterEach, describe, expect, it } from "vite-plus/test";

import { MessageList } from "@/components/agent-elements/message-list";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Root[] = [];

function renderMessageList(messages: UIMessage[], status: ChatStatus = "ready") {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedRoots.push(root);

  act(() => {
    root.render(
      <MessageList
        messages={messages}
        status={status}
        showCopyToolbar={false}
        slots={{
          ToolRenderer: ({ part, nestedTools }) => (
            <div
              data-tool-type={part.type}
              data-nested-types={nestedTools?.map((tool) => tool.type).join(",") ?? ""}
            >
              {part.type}
            </div>
          ),
        }}
      />,
    );
  });

  return container;
}

afterEach(() => {
  while (mountedRoots.length > 0) {
    const root = mountedRoots.pop();
    if (root) {
      act(() => {
        root.unmount();
      });
    }
  }
  document.body.innerHTML = "";
});

describe("MessageList inline activity rendering", () => {
  it("renders activity from multiple assistant trace messages as separate inline rows", () => {
    const container = renderMessageList([
      {
        id: "user-1",
        role: "user",
        parts: [{ type: "text", text: "inspect skills" }],
      } as UIMessage,
      {
        id: "trace-status",
        role: "assistant",
        parts: [
          {
            type: "tool-Status",
            toolCallId: "trace-status:status:0",
            state: "output-available",
            input: { message: "Calling tool find_files" },
            output: { message: "Calling tool find_files" },
          },
        ],
      } as UIMessage,
      {
        id: "trace-search",
        role: "assistant",
        parts: [
          {
            type: "tool-Grep",
            toolCallId: "trace-search:find_files:0",
            state: "output-available",
            input: { query: "skills" },
            output: { results: [{ source: "github", title: "AGENTS.md", date: "" }] },
          },
        ],
      } as UIMessage,
      {
        id: "assistant-final",
        role: "assistant",
        parts: [{ type: "text", text: "Skills are documented in AGENTS.md." }],
      } as UIMessage,
    ]);

    const renderedTools = [...container.querySelectorAll("[data-tool-type]")];
    expect(renderedTools).toHaveLength(2);
    expect(renderedTools.map((tool) => tool.getAttribute("data-tool-type"))).toEqual([
      "tool-Status",
      "tool-Grep",
    ]);
    expect(renderedTools.every((tool) => tool.getAttribute("data-nested-types") === "")).toBe(true);
    expect(container.textContent).toContain("Skills are documented in AGENTS.md.");
  });

  it("preserves explicit ToolGroup nested tools", () => {
    const container = renderMessageList([
      {
        id: "user-1",
        role: "user",
        parts: [{ type: "text", text: "inspect skills" }],
      } as UIMessage,
      {
        id: "trace-group",
        role: "assistant",
        parts: [
          {
            type: "tool-Group",
            toolCallId: "explicit-group",
            state: "input-streaming",
            input: { description: "Runtime activity" },
            nestedTools: [
              {
                type: "tool-Status",
                toolCallId: "status-1",
                state: "output-available",
                input: { message: "Calling tool find_files" },
                output: { message: "Calling tool find_files" },
              },
              {
                type: "tool-Grep",
                toolCallId: "grep-1",
                state: "output-available",
                input: { query: "skills" },
                output: { results: [{ source: "github", title: "AGENTS.md", date: "" }] },
              },
            ],
          },
        ],
      } as unknown as UIMessage,
    ]);

    const renderedTools = [...container.querySelectorAll("[data-tool-type]")];
    expect(renderedTools).toHaveLength(1);
    expect(renderedTools[0]?.getAttribute("data-tool-type")).toBe("tool-Group");
    expect(renderedTools[0]?.getAttribute("data-nested-types")).toBe("tool-Status,tool-Grep");
  });
});
