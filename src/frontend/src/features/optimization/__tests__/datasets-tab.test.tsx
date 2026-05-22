import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { DatasetsTab } from "@/features/optimization/datasets-tab";
import { optimizationEndpoints } from "@/lib/rlm-api/optimization";
import { sessionEndpoints } from "@/lib/rlm-api/sessions";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const modulesState = {
  items: [] as Array<{
    slug: string;
    label: string;
    description?: string;
    program_spec: string;
    required_dataset_keys: string[];
  }>,
};

const sessionsState = {
  isError: false,
  items: [] as Array<{
    id: string;
    title: string;
    status: string;
    model_name: string | null;
    external_session_id: string | null;
    created_at: string;
    updated_at: string;
  }>,
};

const reflectAndReviseModule = {
  slug: "reflect-and-revise",
  label: "Reflect & Revise",
  description: "Reflect answers",
  program_spec: "pkg.reflect:build_program",
  required_dataset_keys: [],
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
        data: {
          items: sessionsState.items,
          total: sessionsState.items.length,
          limit: 10,
          offset: 0,
          has_more: false,
        },
        isLoading: false,
        isError: sessionsState.isError,
        error: sessionsState.isError ? new Error("Durable sessions API failed") : null,
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

vi.mock("@/lib/rlm-api/optimization", async () => {
  const actual = await vi.importActual<typeof import("@/lib/rlm-api/optimization")>(
    "@/lib/rlm-api/optimization",
  );

  return {
    ...actual,
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

describe("DatasetsTab sessions", () => {
  beforeEach(() => {
    sessionsState.isError = false;
    sessionsState.items = [];
    modulesState.items = [reflectAndReviseModule];
    mutationState.isPending = false;
    mutationState.mutate.mockReset();
    mutationState.config = null;
    queryClientState.invalidateQueries.mockReset();
    vi.mocked(sessionEndpoints.exportSession).mockReset();
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

  it("reports durable session API failures instead of using local history fallback", () => {
    sessionsState.isError = true;

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<DatasetsTab />);
    });

    expect(container.textContent).toContain("Failed to load sessions: Durable sessions API failed");
    expect(container.textContent).not.toContain("Showing local session history");

    act(() => {
      root.unmount();
    });
  });

  it("prepares GEPA from canonical durable session export", async () => {
    const onPrepareRun = vi.fn();
    sessionsState.items = [
      {
        id: "550e8400-e29b-41d4-a716-446655440000",
        title: "Durable optimization session",
        status: "active",
        model_name: null,
        external_session_id: null,
        created_at: "2026-04-14T09:00:00.000Z",
        updated_at: "2026-04-14T09:30:00.000Z",
      },
    ];
    vi.mocked(sessionEndpoints.exportSession).mockResolvedValue({
      id: "41",
      name: "Durable optimization session",
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

    expect(sessionEndpoints.exportSession).toHaveBeenCalledWith(
      "550e8400-e29b-41d4-a716-446655440000",
      "reflect-and-revise",
    );
    expect(onPrepareRun).toHaveBeenCalledWith({
      datasetName: "Durable optimization session",
      datasetId: "41",
      auto: "light",
      trainRatio: 0.8,
      moduleSlug: "reflect-and-revise",
      programSpec: "pkg.reflect:build_program",
    });
    expect(optimizationEndpoints.createRun).not.toHaveBeenCalled();

    act(() => {
      root.unmount();
    });
  });

  it("uses the mutation variables when the selected module changes mid-flight", async () => {
    const onPrepareRun = vi.fn();
    sessionsState.items = [
      {
        id: "550e8400-e29b-41d4-a716-446655440000",
        title: "Durable optimization session",
        status: "active",
        model_name: null,
        external_session_id: null,
        created_at: "2026-04-14T09:00:00.000Z",
        updated_at: "2026-04-14T09:30:00.000Z",
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

    let resolveDataset:
      | ((value: Awaited<ReturnType<typeof sessionEndpoints.exportSession>>) => void)
      | null = null;
    const pendingDataset = new Promise<
      Awaited<ReturnType<typeof sessionEndpoints.exportSession>>
    >((resolve) => {
      resolveDataset = resolve;
    });
    vi.mocked(sessionEndpoints.exportSession).mockReturnValue(pendingDataset);

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
        id: "77",
        name: "Durable optimization session",
        row_count: 1,
        format: "jsonl",
        module_slug: "reflect-and-revise",
        created_at: "2026-04-14T09:31:00.000Z",
      });
      await pendingDataset;
      await Promise.resolve();
    });

    expect(onPrepareRun).toHaveBeenCalledWith({
      datasetName: "Durable optimization session",
      datasetId: "77",
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
