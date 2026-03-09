import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ChatInput } from "@/components/chat/ChatInput";

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({
    navigate: vi.fn(),
  }),
}));

describe("ChatInput", () => {
  it("disables submit when the composer is empty", () => {
    const html = renderToStaticMarkup(
      <ChatInput
        value="   "
        onChange={() => {}}
        onSend={() => {}}
        executionMode="auto"
        onExecutionModeChange={() => {}}
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
        onChange={() => {}}
        onSend={() => {}}
        executionMode="auto"
        isLoading
        isReceiving
        onExecutionModeChange={() => {}}
      />,
    );

    expect(html).toContain('aria-label="Sending message"');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("animate-spin");
  });
});
