import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { OptimizationForm } from "@/features/optimization/optimization-form";
import { optimizationEndpoints } from "@/lib/rlm-api/optimization";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const reflectAndReviseModule = {
  slug: "reflect-and-revise",
  label: "Reflect & Revise",
  description: "Refine answers with iterative feedback.",
  program_spec: "pkg.module:build_program",
  required_dataset_keys: ["question", "assistant_response"],
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

const modulesQueryState: {
  data: (typeof reflectAndReviseModule)[] | undefined;
  isError: boolean;
} = {
  data: [reflectAndReviseModule],
  isError: false,
};

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: readonly unknown[] }) => {
    if (queryKey[0] === "optimization" && queryKey[1] === "status") {
      return {
        data: {
          available: true,
          mlflow_enabled: true,
          gepa_installed: true,
          guidance: [],
        },
        isLoading: false,
        isError: false,
      };
    }

    if (queryKey[0] === "optimization" && queryKey[1] === "modules") {
      return {
        data: modulesQueryState.data,
        isLoading: false,
        isError: modulesQueryState.isError,
      };
    }

    if (queryKey[0] === "optimization" && queryKey[1] === "datasets") {
      return {
        data: {
          items: [
            {
              id: 12,
              name: "Refine Dataset",
              row_count: 4,
              format: "jsonl",
              module_slug: "reflect-and-revise",
              created_at: "2026-04-14T09:31:00.000Z",
            },
          ],
          total: 1,
          limit: 100,
          offset: 0,
          has_more: false,
        },
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

describe("OptimizationForm", () => {
  beforeEach(() => {
    mutationState.isPending = false;
    mutationState.mutate.mockReset();
    mutationState.config = null;
    queryClientState.invalidateQueries.mockReset();
    modulesQueryState.data = [reflectAndReviseModule];
    modulesQueryState.isError = false;
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

  it("submits a prefilled GEPA run from the optimization workflow", async () => {
    const onRunCreated = vi.fn();
    vi.mocked(optimizationEndpoints.createRun).mockResolvedValue({
      run_id: 77,
      status: "running",
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <OptimizationForm
          initialDraft={{
            moduleSlug: "reflect-and-revise",
            datasetId: 12,
            datasetName: "Refine Dataset",
            auto: "medium",
            trainRatio: 0.75,
          }}
          draftVersion={1}
          onRunCreated={onRunCreated}
        />,
      );
    });

    await act(async () => {
      await Promise.resolve();
    });

    const runButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Run GEPA"),
    );
    expect(runButton).toBeTruthy();
    expect(runButton?.hasAttribute("disabled")).toBe(false);

    await act(async () => {
      runButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(optimizationEndpoints.createRun).toHaveBeenCalledWith(
      {
        dataset_id: 12,
        dataset_path: null,
        program_spec: "pkg.module:build_program",
        output_path: null,
        auto: "medium",
        train_ratio: 0.75,
        module_slug: "reflect-and-revise",
      },
      expect.any(AbortSignal),
    );
    expect(queryClientState.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["optimization", "runs"],
    });
    expect(onRunCreated).toHaveBeenCalledOnce();

    act(() => {
      root.unmount();
    });
  });

  it("recovers a draft after module hydration retries", async () => {
    vi.mocked(optimizationEndpoints.createRun).mockResolvedValue({
      run_id: 88,
      status: "running",
    });
    modulesQueryState.data = undefined;
    modulesQueryState.isError = true;

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const draftProps = {
      initialDraft: {
        moduleSlug: "reflect-and-revise",
        datasetId: 12,
        datasetName: "Refine Dataset",
        auto: "medium" as const,
        trainRatio: 0.75,
      },
      draftVersion: 2,
    };

    act(() => {
      root.render(<OptimizationForm {...draftProps} />);
    });

    await act(async () => {
      await Promise.resolve();
    });

    let runButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Run GEPA"),
    );
    expect(runButton).toBeTruthy();
    expect(runButton?.hasAttribute("disabled")).toBe(true);
    expect(container.querySelector('input[aria-label="Program spec"]')).toBeTruthy();

    modulesQueryState.data = [reflectAndReviseModule];
    modulesQueryState.isError = false;

    await act(async () => {
      root.render(<OptimizationForm {...draftProps} />);
      await Promise.resolve();
    });

    const moduleTrigger = Array.from(container.querySelectorAll("button")).find(
      (button) => button.getAttribute("aria-label") === "Module selection",
    );
    expect(moduleTrigger?.textContent).toContain("Reflect & Revise");
    expect(container.querySelector('input[aria-label="Program spec"]')).toBeNull();

    runButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Run GEPA"),
    );
    expect(runButton?.hasAttribute("disabled")).toBe(false);

    await act(async () => {
      runButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(optimizationEndpoints.createRun).toHaveBeenCalledWith(
      {
        dataset_id: 12,
        dataset_path: null,
        program_spec: "pkg.module:build_program",
        output_path: null,
        auto: "medium",
        train_ratio: 0.75,
        module_slug: "reflect-and-revise",
      },
      expect.any(AbortSignal),
    );

    act(() => {
      root.unmount();
    });
  });
});
