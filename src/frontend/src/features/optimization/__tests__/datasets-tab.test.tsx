import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { DatasetsTab } from "@/features/optimization/datasets-tab";
import { RlmApiError } from "@/lib/rlm-api/client";
import type { Conversation } from "@/features/workspace/workspace-layout-contract";
import { datasetEndpoints, optimizationEndpoints } from "@/lib/rlm-api/optimization";
import { sessionEndpoints } from "@/lib/rlm-api/sessions";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const workspaceHistoryState = {
  conversations: [] as Conversation[],
};

const mutationState = {
  isPending: false,
  mutate: vi.fn(),
  config: null as null | {
    mutationFn?: (input: unknown) => Promise<unknown>;
    onSuccess?: (result: unknown) => void;
    onError?: (error: unknown) => void;
  },
};

const queryClientState = {
  invalidateQueries: vi.fn(),
};

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({
    queryKey,
  }: {
    queryKey: readonly unknown[];
  }) => {
    if (queryKey[0] === "sessions") {
      return {
        data: undefined,
        isLoading: false,
        isError: true,
        error: new RlmApiError(404, "Not Found"),
        isFetching: false,
        refetch: vi.fn(),
      };
    }

    if (queryKey[0] === "optimization" && queryKey[1] === "modules") {
      return {
        data: [],
        isLoading: false,
        isError: false,
      };
    }

    if (queryKey[0] === "optimization" && queryKey[1] === "datasets") {
      return {
        data: { items: [], total: 0, limit: 20, offset: 0, has_more: false },
        isLoading: false,
        isError: false,
      };
    }

    return {
      data: undefined,
      isLoading: false,
      isError: false,
    };
  },
  useMutation: (config: typeof mutationState.config) => {
    mutationState.config = config;
    return mutationState;
  },
  useQueryClient: () => queryClientState,
}));

vi.mock("@/features/workspace/workspace-layout-contract", () => ({
  useWorkspaceLayoutHistory: () => workspaceHistoryState.conversations,
}));

vi.mock("@/lib/rlm-api/optimization", async () => {
  const actual = await vi.importActual<typeof import("@/lib/rlm-api/optimization")>(
    "@/lib/rlm-api/optimization",
  );

  return {
    ...actual,
    datasetEndpoints: {
      ...actual.datasetEndpoints,
      createFromTranscript: vi.fn(),
    },
    optimizationEndpoints: {
      ...actual.optimizationEndpoints,
      createRun: vi.fn(),
    },
  };
});

vi.mock("@/lib/rlm-api/sessions", async () => {
  const actual = await vi.importActual<typeof import("@/lib/rlm-api/sessions")>(
    "@/lib/rlm-api/sessions",
  );

  return {
    ...actual,
    sessionEndpoints: {
      ...actual.sessionEndpoints,
      exportSession: vi.fn(),
    },
  };
});

describe("DatasetsTab sessions fallback", () => {
  beforeEach(() => {
    workspaceHistoryState.conversations = [];
    mutationState.isPending = false;
    mutationState.mutate.mockReset();
    mutationState.config = null;
    queryClientState.invalidateQueries.mockReset();
    vi.mocked(sessionEndpoints.exportSession).mockReset();
    vi.mocked(datasetEndpoints.createFromTranscript).mockReset();
    vi.mocked(optimizationEndpoints.createRun).mockReset();

    mutationState.mutate.mockImplementation((variables: unknown) => {
      void (async () => {
        try {
          const result = await mutationState.config?.mutationFn?.(variables);
          mutationState.config?.onSuccess?.(result);
        } catch (error) {
          mutationState.config?.onError?.(error);
        }
      })();
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("shows local session history when the durable sessions API is unavailable", () => {
    workspaceHistoryState.conversations = [
      {
        id: "conv-opt-1",
        title: "Recovered optimization session",
        messages: [],
        phase: "complete",
        createdAt: "2026-04-14T09:00:00.000Z",
        updatedAt: "2026-04-14T09:30:00.000Z",
      },
    ];

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<DatasetsTab />);
    });

    expect(container.textContent).toContain(
      "Showing local session history because the durable sessions API is unavailable.",
    );
    expect(container.textContent).toContain("Recovered optimization session");
    expect(container.textContent).not.toContain("Failed to load sessions");

    act(() => {
      root.unmount();
    });
  });

  it("launches GEPA from local session history", async () => {
    workspaceHistoryState.conversations = [
      {
        id: "conv-opt-1",
        title: "Recovered optimization session",
        messages: [
          { id: "u1", type: "user", content: "What is 2+2?" },
          { id: "a1", type: "assistant", content: "4" },
        ],
        phase: "complete",
        createdAt: "2026-04-14T09:00:00.000Z",
        updatedAt: "2026-04-14T09:30:00.000Z",
      },
    ];
    vi.mocked(datasetEndpoints.createFromTranscript).mockResolvedValue({
      id: 41,
      name: "Recovered optimization session",
      row_count: 1,
      format: "jsonl",
      module_slug: "reflect-and-revise",
      created_at: "2026-04-14T09:31:00.000Z",
    });
    vi.mocked(optimizationEndpoints.createRun).mockResolvedValue({
      run_id: 99,
      status: "running",
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<DatasetsTab />);
    });

    const selectTrigger = Array.from(container.querySelectorAll("button")).find((button) =>
      button.getAttribute("aria-label") === "Pick module",
    );
    expect(selectTrigger).toBeTruthy();

    act(() => {
      selectTrigger?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const option = Array.from(document.querySelectorAll('[role="option"]')).find((element) =>
      element.textContent?.includes("Reflect & Revise"),
    );
    expect(option).toBeTruthy();

    act(() => {
      option?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const optimizeButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Optimize with GEPA"),
    );
    expect(optimizeButton?.hasAttribute("disabled")).toBe(false);

    await act(async () => {
      optimizeButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(datasetEndpoints.createFromTranscript).toHaveBeenCalledWith({
      module_slug: "reflect-and-revise",
      title: "Recovered optimization session",
      turns: [{ user_message: "What is 2+2?", assistant_message: "4" }],
    });
    expect(optimizationEndpoints.createRun).toHaveBeenCalledWith({
      dataset_id: 41,
      program_spec: "",
      auto: "light",
      train_ratio: 0.8,
      module_slug: "reflect-and-revise",
    });

    act(() => {
      root.unmount();
    });
  });
});
