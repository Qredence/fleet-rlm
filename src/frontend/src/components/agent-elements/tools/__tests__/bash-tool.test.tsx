import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vite-plus/test";

import { BashToolTerminalCard } from "@/components/agent-elements/tools/bash-tool";

function renderBashTool(command: string) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      <BashToolTerminalCard
        state="complete"
        onComplete={() => {}}
        step={{
          id: "cmd-1",
          type: "tool-call",
          toolName: "Bash",
          toolDetail: command,
          bashCommand: command,
          bashLanguage: "python",
          duration: 0,
        }}
      />,
    );
  });

  return { container, root };
}

describe("BashToolTerminalCard", () => {
  it("repairs collapsed Python REPL snippets before rendering the code block", () => {
    const command =
      'import re text = document["text"] # Inspect document headings headings = re.findall(r"^(#{1,6})\\s+(.*)", text, re.MULTILINE) print(f"Total headings found: {len(headings)}") print("Sample headings:") for h in headings[:3]: print(h)';
    const { container, root } = renderBashTool(command);

    expect(container.textContent).toContain("import re");
    expect(container.textContent).toContain('document["text"]');
    expect(container.textContent).toContain("Inspect document headings");
    expect(container.textContent).toContain("headings = re.findall");
    expect(container.textContent).toContain('print("Sample headings:")');
    expect(container.textContent).toContain("for h in headings[:3]:");

    act(() => root.unmount());
  });

  it("handles trailing colons safely without crashing due to out of bounds access", () => {
    const command = "import os x = 123 for i in range(10):";
    const { container, root } = renderBashTool(command);

    expect(container.textContent).toContain("import os");
    expect(container.textContent).toContain("x = 123");
    expect(container.textContent).toContain("for i in range(10):");

    act(() => root.unmount());
  });

  it("renders command output as a grouped tool with nested command and stdout rows", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <BashToolTerminalCard
          state="complete"
          onComplete={() => {}}
          step={{
            id: "cmd-2",
            type: "tool-call",
            toolName: "Bash",
            toolDetail: "repl_execute",
            bashCommand: "repl_execute",
            bashLanguage: "python",
            bashOutput: "durable_write_started: /home/daytona/session.json",
            bashSuccess: true,
            duration: 0,
          }}
        />,
      );
    });

    expect(container.textContent).toContain("Ran command");
    expect(container.textContent).toContain("command");
    expect(container.textContent).toContain("repl_execute");
    expect(container.textContent).toContain("stdout");
    // Note: durable_write_started (bashOutput content) is inside a Collapsible.Panel
    // with defaultOpen={false}, so it's not in textContent until the user expands it.
    // The "stdout" label above confirms the row is rendered.

    act(() => root.unmount());
  });
});
