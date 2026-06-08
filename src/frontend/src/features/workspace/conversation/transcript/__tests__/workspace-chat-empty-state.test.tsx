import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { WorkspaceChatEmptyStateHero } from "@/features/workspace/conversation/transcript/workspace-chat-empty-state";

describe("WorkspaceChatEmptyStateHero", () => {
  it("renders the workspace hero copy without suggestion chips", () => {
    const html = renderToStaticMarkup(<WorkspaceChatEmptyStateHero isMobile={false} />);

    expect(html).toContain("Start a conversation");
    expect(html).toContain("Type a message below");
    expect(html).toContain("begin working with the AI assistant");
    expect(html).not.toContain("Build a feature");
  });
});
