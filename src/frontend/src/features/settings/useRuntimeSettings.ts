import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { runtimeEndpoints } from "@/lib/rlm-api/runtime";

export const runtimeKeys = {
  all: ["runtime"] as const,
  settings: () => [...runtimeKeys.all, "settings"] as const,
  status: () => [...runtimeKeys.all, "status"] as const,
};

export const RUNTIME_EDITABLE_KEYS = [
  "DSPY_LM_MODEL",
  "DSPY_LLM_API_KEY",
  "DSPY_LM_API_BASE",
  "DSPY_LM_MAX_TOKENS",
  "MODAL_TOKEN_ID",
  "MODAL_TOKEN_SECRET",
  "SECRET_NAME",
  "VOLUME_NAME",
] as const;

export type RuntimeEditableKey = (typeof RUNTIME_EDITABLE_KEYS)[number];

export function computeRuntimeUpdates(
  current: Record<string, string>,
  baseline: Record<string, string>,
): Record<string, string> {
  const updates: Record<string, string> = {};
  for (const key of RUNTIME_EDITABLE_KEYS) {
    const next = current[key] ?? "";
    const prev = baseline[key] ?? "";
    if (next !== prev) {
      updates[key] = next;
    }
  }
  return updates;
}

export function useRuntimeStatus(options?: {
  enabled?: boolean;
  refetchIntervalMs?: number;
}) {
  return useQuery({
    queryKey: runtimeKeys.status(),
    queryFn: ({ signal }) => runtimeEndpoints.status(signal),
    staleTime: 10_000,
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
    enabled: options?.enabled ?? true,
  });
}

export function useRuntimeSettings() {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: runtimeKeys.settings(),
    queryFn: ({ signal }) => runtimeEndpoints.settings(signal),
    staleTime: 5_000,
  });

  const statusQuery = useRuntimeStatus();

  const saveSettings = useMutation({
    mutationFn: (updates: Record<string, string>) =>
      runtimeEndpoints.patchSettings({ updates }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: runtimeKeys.settings() }),
        queryClient.invalidateQueries({ queryKey: runtimeKeys.status() }),
      ]);
    },
  });

  const testModalConnection = useMutation({
    mutationFn: () => runtimeEndpoints.testModal(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: runtimeKeys.status() });
    },
  });

  const testLmConnection = useMutation({
    mutationFn: () => runtimeEndpoints.testLm(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: runtimeKeys.status() });
    },
  });

  const testAllConnections = useCallback(async () => {
    const modal = await testModalConnection.mutateAsync();
    const lm = await testLmConnection.mutateAsync();
    return { modal, lm };
  }, [testLmConnection, testModalConnection]);

  return {
    settingsQuery,
    statusQuery,
    saveSettings,
    testModalConnection,
    testLmConnection,
    testAllConnections,
  };
}
