import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryFunctionContext } from "@tanstack/react-query";

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

export const optimizationQueryOptions = {
  status: () => ({
    queryKey: optimizationQueryKeys.status(),
    queryFn: ({ signal }: QueryFunctionContext) => optimizationEndpoints.status(signal),
  }),
  modules: () => ({
    queryKey: optimizationQueryKeys.modules(),
    queryFn: ({ signal }: QueryFunctionContext) => optimizationEndpoints.modules(signal),
  }),
  datasets: (moduleSlug?: string | null) => ({
    queryKey: optimizationQueryKeys.datasets(moduleSlug),
    queryFn: ({ signal }: QueryFunctionContext) =>
      optimizationEndpoints.datasets({ moduleSlug: moduleSlug || null, limit: 100 }, signal),
  }),
  runs: () => ({
    queryKey: optimizationQueryKeys.runs(),
    queryFn: ({ signal }: QueryFunctionContext) =>
      optimizationEndpoints.runs({ limit: 50 }, signal),
    refetchInterval: (query: { state: { data?: { status: string }[] } }) => {
      const runs = query.state.data;
      return runs?.some((run) => run.status === "running") ? 4000 : false;
    },
  }),
  runDetails: (runId: string | null) => ({
    queryKey: optimizationQueryKeys.runDetails(runId),
    queryFn: ({ signal }: QueryFunctionContext) => {
      if (!runId)
        return null as unknown as Awaited<ReturnType<typeof optimizationEndpoints.runDetails>>;
      return optimizationEndpoints.runDetails(runId, signal);
    },
  }),
};

export function useOptimizationStatus() {
  return useQuery(optimizationQueryOptions.status());
}

export function useOptimizationModules() {
  return useQuery(optimizationQueryOptions.modules());
}

export function useOptimizationDatasets(
  moduleSlug?: string | null,
  options?: { enabled?: boolean },
) {
  return useQuery({
    ...optimizationQueryOptions.datasets(moduleSlug),
    ...options,
  });
}

export function useOptimizationRuns() {
  return useQuery(optimizationQueryOptions.runs());
}

export function useOptimizationRunDetails(runId: string | null) {
  return useQuery({
    ...optimizationQueryOptions.runDetails(runId),
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
