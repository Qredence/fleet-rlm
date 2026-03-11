import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ChatInput } from "@/components/chat/ChatInput";

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({
    navigate: vi.fn(),
  }),
}));

describe("ChatInput", () => {
  const baseProps = {
    onChange: () => {},
    onSend: () => {},
    runtimeMode: "modal_chat" as const,
    onRuntimeModeChange: () => {},
    executionMode: "auto" as const,
    onExecutionModeChange: () => {},
  };

  it("disables submit when the composer is empty", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        value="   "
        {...baseProps}
      />,
    );

    expect(html).toContain("disabled");
    expect(html).toContain('aria-label="Submit"');
    expect(html).toContain('aria-label="Message"');
  });

  it("shows loading feedback while a response is in flight", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        value="hello"
        isLoading
        isReceiving
        {...baseProps}
      />,
    );

    expect(html).toContain('aria-label="Sending message"');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("animate-spin");
  });

  it("keeps the composer generic even in Daytona mode", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        value="summarize this repo"
        {...baseProps}
        runtimeMode="daytona_pilot"
      />,
    );

    expect(html).not.toContain("Experimental Daytona runtime");
    expect(html).not.toContain('aria-label="Daytona repository URL"');
    expect(html).not.toContain("Tools only");
    expect(html).toContain("Daytona RLM");
  });
});
