import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  optimizationEndpoints,
  type GEPAOptimizationRequest,
  type UploadOptimizationDatasetInput,
} from "@/lib/rlm-api";

export const optimizationQueryKeys = {
  all: ["optimization"] as const,
  status: () => [...optimizationQueryKeys.all, "status"] as const,
  modules: () => [...optimizationQueryKeys.all, "modules"] as const,
  datasets: (moduleSlug: string | null | undefined) =>
    [...optimizationQueryKeys.all, "datasets", moduleSlug ?? "all"] as const,
  runs: () => [...optimizationQueryKeys.all, "runs"] as const,
  runDetails: (runId: string | null | undefined) =>
    [...optimizationQueryKeys.all, "runs", runId ?? "none", "details"] as const,
};

export function useOptimizationStatus() {
  return useQuery({
    queryKey: optimizationQueryKeys.status(),
    queryFn: ({ signal }) => optimizationEndpoints.status(signal),
  });
}

export function useOptimizationModules() {
  return useQuery({
    queryKey: optimizationQueryKeys.modules(),
    queryFn: ({ signal }) => optimizationEndpoints.modules(signal),
  });
}

export function useOptimizationDatasets(
  moduleSlug?: string | null,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: optimizationQueryKeys.datasets(moduleSlug),
    queryFn: ({ signal }) =>
      optimizationEndpoints.datasets({ moduleSlug: moduleSlug || null, limit: 100 }, signal),
    ...options,
  });
}

export function useOptimizationRuns() {
  return useQuery({
    queryKey: optimizationQueryKeys.runs(),
    queryFn: ({ signal }) => optimizationEndpoints.runs({ limit: 50 }, signal),
    refetchInterval: (query) => {
      const runs = query.state.data;
      return runs?.some((run) => run.status === "running") ? 4000 : false;
    },
  });
}

export function useOptimizationRunDetails(runId: string | null) {
  return useQuery({
    queryKey: optimizationQueryKeys.runDetails(runId),
    queryFn: ({ signal }) => optimizationEndpoints.runDetails(runId ?? "", signal),
    enabled: Boolean(runId),
  });
}

export function useOptimizationMutations() {
  const queryClient = useQueryClient();

  const uploadDataset = useMutation({
    mutationFn: (input: UploadOptimizationDatasetInput) =>
      optimizationEndpoints.uploadDataset(input),
  });

  const createRun = useMutation({
    mutationFn: (request: GEPAOptimizationRequest) => optimizationEndpoints.createRun(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: optimizationQueryKeys.runs() });
    },
  });

  const createPromotionDraft = useMutation({
    mutationFn: (runId: string) => optimizationEndpoints.createPromotionDraft(runId),
    onSuccess: (_data, runId) => {
      void queryClient.invalidateQueries({ queryKey: optimizationQueryKeys.runDetails(runId) });
    },
  });

  const exportSessionTraces = useMutation({
    mutationFn: (input: { sessionId: string }) =>
      optimizationEndpoints.exportSessionTraces(input.sessionId, { format: "both" }),
  });

  return {
    uploadDataset,
    createRun,
    createPromotionDraft,
    exportSessionTraces,
  };
}
