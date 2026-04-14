import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { SessionList } from "@/features/history/session-list";
import { RlmApiError } from "@/lib/rlm-api/client";
import type { Conversation } from "@/features/workspace/workspace-layout-contract";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const workspaceHistoryState = {
  conversations: [] as Conversation[],
};
const queryState = {
  data: undefined as unknown,
  isLoading: false,
  isError: false,
  error: undefined as unknown,
};

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => queryState,
}));

vi.mock("@/lib/rlm-api/sessions", () => ({
  sessionKeys: {
    list: (params: Record<string, unknown>) => ["sessions", "list", params],
  },
  sessionEndpoints: {
    listSessions: vi.fn(),
  },
}));

vi.mock("@/features/workspace/workspace-layout-contract", () => ({
  useWorkspaceLayoutHistory: () => workspaceHistoryState.conversations,
}));

describe("SessionList", () => {
  beforeEach(() => {
    workspaceHistoryState.conversations = [];
    queryState.data = undefined;
    queryState.isLoading = false;
    queryState.isError = false;
    queryState.error = undefined;
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("falls back to local history when the durable sessions API returns 404", async () => {
    workspaceHistoryState.conversations = [
      {
        id: "conv-local-1",
        title: "Recovered local session",
        messages: [
          {
            id: "user-1",
            type: "user",
            content: "Find my prior session",
          },
        ],
        phase: "complete",
        createdAt: "2026-04-14T09:00:00.000Z",
        updatedAt: "2026-04-14T09:30:00.000Z",
      },
    ];
    queryState.isError = true;
    queryState.error = new RlmApiError(404, "Not Found");

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<SessionList selectedSession={null} onSelect={() => undefined} />);
    });

    expect(container.textContent).toContain("Recovered local session");
    expect(container.textContent).not.toContain("Failed to load sessions");

    act(() => {
      root.unmount();
    });
  });
});
