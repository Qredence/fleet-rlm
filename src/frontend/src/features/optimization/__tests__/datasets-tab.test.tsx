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

const modulesState = {
  items: [] as Array<{
    slug: string;
    label: string;
    description?: string;
    program_spec: string;
    required_dataset_keys: string[];
  }>,
};

const mutationState = {
  isPending: false,
  mutate: vi.fn(),
  config: null as null | {
    mutationFn?: (input: unknown) => Promise<unknown>;
    onSuccess?: (result: unknown, variables: unknown) => void;
    onError?: (error: unknown) => void;
  },
};

const queryClientState = {
  invalidateQueries: vi.fn(),
};

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: readonly unknown[] }) => {
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
        data: modulesState.items,
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
  const actual =
    await vi.importActual<typeof import("@/lib/rlm-api/sessions")>("@/lib/rlm-api/sessions");

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
    modulesState.items = [];
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
          mutationState.config?.onSuccess?.(result, variables);
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

  it("prepares GEPA from local session history", async () => {
    const onPrepareRun = vi.fn();
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

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<DatasetsTab onPrepareRun={onPrepareRun} />);
    });

    const selectTrigger = Array.from(container.querySelectorAll("button")).find(
      (button) => button.getAttribute("aria-label") === "Pick module",
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
      button.textContent?.includes("Prepare GEPA Run"),
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
    expect(onPrepareRun).toHaveBeenCalledWith({
      datasetName: "Recovered optimization session",
      datasetId: 41,
      auto: "light",
      trainRatio: 0.8,
      moduleSlug: "reflect-and-revise",
    });
    expect(optimizationEndpoints.createRun).not.toHaveBeenCalled();

    act(() => {
      root.unmount();
    });
  });

  it("uses the mutation variables when the selected module changes mid-flight", async () => {
    const onPrepareRun = vi.fn();
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
    modulesState.items = [
      {
        slug: "reflect-and-revise",
        label: "Reflect & Revise",
        description: "Reflect answers",
        program_spec: "pkg.reflect:build_program",
        required_dataset_keys: [],
      },
      {
        slug: "recursive-repair",
        label: "Recursive Repair",
        description: "Repair answers",
        program_spec: "pkg.repair:build_program",
        required_dataset_keys: [],
      },
    ];

    let resolveDataset: ((value: Awaited<ReturnType<typeof datasetEndpoints.createFromTranscript>>) => void) | null =
      null;
    const pendingDataset = new Promise<
      Awaited<ReturnType<typeof datasetEndpoints.createFromTranscript>>
    >((resolve) => {
      resolveDataset = resolve;
    });
    vi.mocked(datasetEndpoints.createFromTranscript).mockReturnValue(pendingDataset);

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<DatasetsTab onPrepareRun={onPrepareRun} />);
    });

    const selectTrigger = () =>
      Array.from(container.querySelectorAll("button")).find(
        (button) => button.getAttribute("aria-label") === "Pick module",
      );

    const clickModuleOption = (label: string) => {
      act(() => {
        selectTrigger()?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });

      const option = Array.from(document.querySelectorAll('[role="option"]')).find((element) =>
        element.textContent?.includes(label),
      );
      expect(option).toBeTruthy();

      act(() => {
        option?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
    };

    clickModuleOption("Reflect & Revise");

    const optimizeButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Prepare GEPA Run"),
    );
    expect(optimizeButton?.hasAttribute("disabled")).toBe(false);

    await act(async () => {
      optimizeButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    clickModuleOption("Recursive Repair");

    await act(async () => {
      resolveDataset?.({
        id: 77,
        name: "Recovered optimization session",
        row_count: 1,
        format: "jsonl",
        module_slug: "reflect-and-revise",
        created_at: "2026-04-14T09:31:00.000Z",
      });
      await pendingDataset;
      await Promise.resolve();
    });

    expect(onPrepareRun).toHaveBeenCalledWith({
      datasetName: "Recovered optimization session",
      datasetId: 77,
      auto: "light",
      trainRatio: 0.8,
      moduleSlug: "reflect-and-revise",
      programSpec: "pkg.reflect:build_program",
    });

    act(() => {
      root.unmount();
    });
  });
});
