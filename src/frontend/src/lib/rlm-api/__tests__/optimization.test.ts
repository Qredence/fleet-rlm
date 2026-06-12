import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { RlmApiError } from "@/lib/rlm-api/client";

async function loadOptimizationModule() {
  vi.resetModules();
  return import("@/lib/rlm-api/optimization");
}

afterEach(() => {
  vi.doUnmock("@/lib/rlm-api/client");
  vi.restoreAllMocks();
});

describe("optimizationEndpoints", () => {
  it("uploads datasets as multipart form data before run creation", async () => {
    const postForm = vi.fn().mockResolvedValue({ id: "dataset-1" });
    const post = vi.fn().mockResolvedValue({ run_id: "run-1", status: "running" });
    vi.doMock("@/lib/rlm-api/client", () => ({
      RlmApiError,
      rlmApiClient: {
        get: vi.fn(),
        patch: vi.fn(),
        delete: vi.fn(),
        postForm,
        post,
      },
    }));

    const { optimizationEndpoints } = await loadOptimizationModule();
    const file = new File(['{"query":"q"}'], "dataset.jsonl", { type: "application/json" });

    await optimizationEndpoints.uploadDataset({ file, moduleSlug: "longcot-reasoner" });
    await optimizationEndpoints.createRun({
      optimizer: "gepa",
      dataset_id: "dataset-1",
      module_slug: "longcot-reasoner",
    });

    expect(postForm).toHaveBeenCalledTimes(1);
    expect(postForm.mock.calls[0]?.[0]).toBe("/api/v1/optimization/datasets");
    expect(postForm.mock.calls[0]?.[1]).toBeInstanceOf(FormData);
    expect(post).toHaveBeenCalledWith(
      "/api/v1/optimization/runs",
      {
        optimizer: "gepa",
        dataset_id: "dataset-1",
        module_slug: "longcot-reasoner",
      },
      undefined,
      120_000,
    );
  });

  it("fetches run history with pagination query parameters", async () => {
    const get = vi.fn().mockResolvedValue([]);
    vi.doMock("@/lib/rlm-api/client", () => ({
      RlmApiError,
      rlmApiClient: {
        get,
        patch: vi.fn(),
        delete: vi.fn(),
        postForm: vi.fn(),
        post: vi.fn(),
      },
    }));

    const { optimizationEndpoints } = await loadOptimizationModule();
    await optimizationEndpoints.runs({ status: "running", limit: 20, offset: 40 });

    expect(get).toHaveBeenCalledWith(
      "/api/v1/optimization/runs?status=running&limit=20&offset=40",
      undefined,
    );
  });

  it("fetches registered datasets with optional module filtering", async () => {
    const get = vi.fn().mockResolvedValue([]);
    vi.doMock("@/lib/rlm-api/client", () => ({
      RlmApiError,
      rlmApiClient: {
        get,
        patch: vi.fn(),
        delete: vi.fn(),
        postForm: vi.fn(),
        post: vi.fn(),
      },
    }));

    const { optimizationEndpoints } = await loadOptimizationModule();
    await optimizationEndpoints.datasets({ moduleSlug: "longcot-reasoner", limit: 25 });

    expect(get).toHaveBeenCalledWith(
      "/api/v1/optimization/datasets?module_slug=longcot-reasoner&limit=25",
      undefined,
    );
  });

  it("exports session traces as raw and distilled artifacts", async () => {
    const post = vi.fn().mockResolvedValue({ session_id: "session-1", trace_count: 2 });
    vi.doMock("@/lib/rlm-api/client", () => ({
      RlmApiError,
      rlmApiClient: {
        get: vi.fn(),
        patch: vi.fn(),
        delete: vi.fn(),
        postForm: vi.fn(),
        post,
      },
    }));

    const { optimizationEndpoints } = await loadOptimizationModule();
    await optimizationEndpoints.exportSessionTraces("session-1", { format: "both" });

    expect(post).toHaveBeenCalledWith(
      "/api/v1/sessions/session-1/trace-export",
      { format: "both" },
      undefined,
      120_000,
    );
  });

  it("fetches detailed GEPA run reports and creates promotion drafts", async () => {
    const get = vi.fn().mockResolvedValue({ run: { id: "277" } });
    const post = vi.fn().mockResolvedValue({ draft_id: "promotion-draft-277" });
    vi.doMock("@/lib/rlm-api/client", () => ({
      RlmApiError,
      rlmApiClient: {
        get,
        patch: vi.fn(),
        delete: vi.fn(),
        postForm: vi.fn(),
        post,
      },
    }));

    const { optimizationEndpoints } = await loadOptimizationModule();
    await optimizationEndpoints.runDetails("277");
    await optimizationEndpoints.createPromotionDraft("277");

    expect(get).toHaveBeenCalledWith("/api/v1/optimization/runs/277/details", undefined);
    expect(post).toHaveBeenCalledWith(
      "/api/v1/optimization/runs/277/promotion-drafts",
      {},
      undefined,
      120_000,
    );
  });
});
