import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PromptInput } from "@/components/chat/prompt-input/PromptInput";
import { PromptInputSubmit } from "@/components/chat/prompt-input/PromptInputSubmit";

function renderSubmit(
  options: {
    value?: string;
    isLoading?: boolean;
    isReceiving?: boolean;
  } = {},
) {
  const {
    value = "hello world",
    isLoading = false,
    isReceiving = false,
  } = options;

  return renderToStaticMarkup(
    <PromptInput
      value={value}
      onChange={() => {}}
      onSubmit={() => {}}
      isLoading={isLoading}
      isReceiving={isReceiving}
    >
      <PromptInputSubmit />
    </PromptInput>,
  );
}

describe("PromptInputSubmit", () => {
  it("uses accent background for active send button", () => {
    const html = renderSubmit();

    expect(html).toContain("bg-accent");
    expect(html).toContain('aria-label="Send message"');
  });

  it("shows receiving spinner state while response is streaming", () => {
    const html = renderSubmit({ isLoading: true, isReceiving: true });

    expect(html).toContain('aria-label="Receiving response"');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("animate-spin");
  });

  it("disables submit when there is no message content", () => {
    const html = renderSubmit({ value: "   " });

    expect(html).toContain("disabled");
  });
});
