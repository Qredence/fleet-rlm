import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { SessionList } from "@/features/history/session-list";
import { RlmApiError } from "@/lib/rlm-api/client";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

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

describe("SessionList", () => {
  beforeEach(() => {
    queryState.data = undefined;
    queryState.isLoading = false;
    queryState.isError = false;
    queryState.error = undefined;
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("reports durable sessions API failures instead of using local history", async () => {
    queryState.isError = true;
    queryState.error = new RlmApiError(404, "Not Found");

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<SessionList selectedSession={null} onSelect={() => undefined} />);
    });

    expect(container.textContent).toContain("Failed to load sessions: [404] Not Found");
    expect(container.textContent).not.toContain("Recovered local session");

    act(() => {
      root.unmount();
    });
  });
});
