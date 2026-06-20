import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { RlmApiError } from "@/lib/rlm-api/client";

async function loadOptimizationModule() {
  vi.resetModules();
  return import("@/lib/rlm-api/optimization");
}

function mockResponse(data: unknown, status = 200) {
  return {
    data,
    error: undefined,
    response: { ok: status >= 200 && status < 300, status } as Response,
  };
}

function mockTypedClient(overrides: {
  GET?: ReturnType<typeof vi.fn>;
  POST?: ReturnType<typeof vi.fn>;
  PATCH?: ReturnType<typeof vi.fn>;
  DELETE?: ReturnType<typeof vi.fn>;
}) {
  vi.doMock("@/lib/rlm-api/typed-client", () => ({
    typedClient: {
      GET: overrides.GET ?? vi.fn(),
      POST: overrides.POST ?? vi.fn(),
      PATCH: overrides.PATCH ?? vi.fn(),
      DELETE: overrides.DELETE ?? vi.fn(),
    },
    unwrap: vi.fn(async (promise: Promise<{ data?: unknown; error?: unknown }>) => {
      const result = await promise;
      if (result.error !== undefined) {
        throw new RlmApiError(500, "error");
      }
      return result.data;
    }),
    withTimeout: vi.fn((signal?: AbortSignal) => signal),
  }));
}

afterEach(() => {
  vi.doUnmock("@/lib/rlm-api/typed-client");
  vi.restoreAllMocks();
});

describe("optimizationEndpoints", () => {
  it("uploads datasets as multipart form data before run creation", async () => {
    const POST = vi.fn();
    POST.mockResolvedValueOnce(mockResponse({ id: "dataset-1" }));
    POST.mockResolvedValueOnce(mockResponse({ run_id: "run-1", status: "running" }));
    mockTypedClient({ POST });

    const { optimizationEndpoints } = await loadOptimizationModule();
    const file = new File(['{"query":"q"}'], "dataset.jsonl", { type: "application/json" });

    await optimizationEndpoints.uploadDataset({ file, moduleSlug: "longcot-reasoner" });
    await optimizationEndpoints.createRun({
      optimizer: "gepa",
      dataset_id: "dataset-1",
      module_slug: "longcot-reasoner",
    });

    expect(POST).toHaveBeenCalledTimes(2);
    expect(POST.mock.calls[0]?.[0]).toBe("/api/v1/optimization/datasets");
    expect(POST.mock.calls[1]?.[0]).toBe("/api/v1/optimization/runs");
  });

  it("fetches run history with pagination query parameters", async () => {
    const GET = vi.fn().mockResolvedValue(mockResponse([]));
    mockTypedClient({ GET });

    const { optimizationEndpoints } = await loadOptimizationModule();
    await optimizationEndpoints.runs({ status: "running", limit: 20, offset: 40 });

    expect(GET).toHaveBeenCalledTimes(1);
    expect(GET.mock.calls[0]?.[0]).toBe("/api/v1/optimization/runs");
    expect(GET.mock.calls[0]?.[1]?.params?.query).toEqual({
      status: "running",
      limit: 20,
      offset: 40,
    });
  });

  it("fetches registered datasets with optional module filtering", async () => {
    const GET = vi.fn().mockResolvedValue(mockResponse([]));
    mockTypedClient({ GET });

    const { optimizationEndpoints } = await loadOptimizationModule();
    await optimizationEndpoints.datasets({ moduleSlug: "longcot-reasoner", limit: 25 });

    expect(GET).toHaveBeenCalledTimes(1);
    expect(GET.mock.calls[0]?.[0]).toBe("/api/v1/optimization/datasets");
    expect(GET.mock.calls[0]?.[1]?.params?.query).toEqual({
      module_slug: "longcot-reasoner",
      limit: 25,
      offset: undefined,
    });
  });

  it("exports session traces as raw and distilled artifacts", async () => {
    const POST = vi.fn().mockResolvedValue(
      mockResponse({ session_id: "session-1", trace_count: 2 }),
    );
    mockTypedClient({ POST });

    const { optimizationEndpoints } = await loadOptimizationModule();
    await optimizationEndpoints.exportSessionTraces("session-1", { format: "both" });

    expect(POST).toHaveBeenCalledTimes(1);
    expect(POST.mock.calls[0]?.[0]).toBe("/api/v1/sessions/{session_id}/trace-export");
    expect(POST.mock.calls[0]?.[1]?.params?.path).toEqual({ session_id: "session-1" });
  });

  it("fetches detailed GEPA run reports and creates promotion drafts", async () => {
    const GET = vi.fn().mockResolvedValue(mockResponse({ run: { id: "277" } }));
    const POST = vi.fn().mockResolvedValue(
      mockResponse({ draft_id: "promotion-draft-277" }),
    );
    mockTypedClient({ GET, POST });

    const { optimizationEndpoints } = await loadOptimizationModule();
    await optimizationEndpoints.runDetails("277");
    await optimizationEndpoints.createPromotionDraft("277");

    expect(GET).toHaveBeenCalledWith(
      "/api/v1/optimization/runs/{run_id}/details",
      expect.objectContaining({ params: { path: { run_id: "277" } } }),
    );
    expect(POST).toHaveBeenCalledWith(
      "/api/v1/optimization/runs/{run_id}/promotion-drafts",
      expect.objectContaining({ params: { path: { run_id: "277" } } }),
    );
  });
});
