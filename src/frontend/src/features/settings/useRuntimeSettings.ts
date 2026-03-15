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
  "DSPY_DELEGATE_LM_MODEL",
  "DSPY_DELEGATE_LM_SMALL_MODEL",
  "DSPY_LLM_API_KEY",
  "DSPY_LM_API_BASE",
  "DSPY_LM_MAX_TOKENS",
  "DAYTONA_API_KEY",
  "DAYTONA_API_URL",
  "DAYTONA_TARGET",
  "MODAL_TOKEN_ID",
  "MODAL_TOKEN_SECRET",
  "SECRET_NAME",
  "VOLUME_NAME",
] as const;

export type RuntimeEditableKey = (typeof RUNTIME_EDITABLE_KEYS)[number];
export const RUNTIME_SECRET_EDITABLE_KEYS = [
  "DSPY_LLM_API_KEY",
  "DAYTONA_API_KEY",
  "MODAL_TOKEN_ID",
  "MODAL_TOKEN_SECRET",
] as const;
export type RuntimeSecretEditableKey = (typeof RUNTIME_SECRET_EDITABLE_KEYS)[number];

export const RUNTIME_LM_EDITABLE_KEYS = [
  "DSPY_LM_MODEL",
  "DSPY_DELEGATE_LM_MODEL",
  "DSPY_DELEGATE_LM_SMALL_MODEL",
  "DSPY_LLM_API_KEY",
  "DSPY_LM_API_BASE",
] as const;

export type RuntimeLmEditableKey = (typeof RUNTIME_LM_EDITABLE_KEYS)[number];
export const RUNTIME_LM_SECRET_EDITABLE_KEYS = ["DSPY_LLM_API_KEY"] as const;
export type RuntimeLmSecretEditableKey = (typeof RUNTIME_LM_SECRET_EDITABLE_KEYS)[number];

export interface RuntimeUpdateComputationOptions<SecretKey extends RuntimeSecretEditableKey> {
  secretInputs?: Partial<Record<SecretKey, string>>;
  clearedSecrets?: Iterable<SecretKey>;
}

function toSecretSet<SecretKey extends RuntimeSecretEditableKey>(
  options?: RuntimeUpdateComputationOptions<SecretKey>,
): Set<SecretKey> {
  if (!options?.clearedSecrets) return new Set<SecretKey>();
  return new Set(options.clearedSecrets);
}

function computeSecretUpdate<SecretKey extends RuntimeSecretEditableKey>(
  key: SecretKey,
  updates: Record<string, string>,
  options?: RuntimeUpdateComputationOptions<SecretKey>,
): void {
  const nextSecretValue = options?.secretInputs?.[key] ?? "";
  const clearedSecretSet = toSecretSet(options);
  if (nextSecretValue.trim() !== "") {
    updates[key] = nextSecretValue;
    return;
  }
  if (clearedSecretSet.has(key)) {
    updates[key] = "";
  }
}

export function computeRuntimeUpdates(
  current: Record<string, string>,
  baseline: Record<string, string>,
  options?: RuntimeUpdateComputationOptions<RuntimeSecretEditableKey>,
): Record<string, string> {
  const updates: Record<string, string> = {};
  for (const key of RUNTIME_EDITABLE_KEYS) {
    if ((RUNTIME_SECRET_EDITABLE_KEYS as readonly string[]).includes(key)) {
      computeSecretUpdate(
        key as RuntimeSecretEditableKey,
        updates,
        options as RuntimeUpdateComputationOptions<RuntimeSecretEditableKey>,
      );
      continue;
    }
    const next = current[key] ?? "";
    const prev = baseline[key] ?? "";
    if (next !== prev) {
      updates[key] = next;
    }
  }
  return updates;
}

export function computeLmRuntimeUpdates(
  current: Record<string, string>,
  baseline: Record<string, string>,
  options?: RuntimeUpdateComputationOptions<RuntimeLmSecretEditableKey>,
): Record<string, string> {
  const updates: Record<string, string> = {};
  for (const key of RUNTIME_LM_EDITABLE_KEYS) {
    if ((RUNTIME_LM_SECRET_EDITABLE_KEYS as readonly string[]).includes(key)) {
      computeSecretUpdate(
        key as RuntimeLmSecretEditableKey,
        updates,
        options as RuntimeUpdateComputationOptions<RuntimeLmSecretEditableKey>,
      );
      continue;
    }
    const next = current[key] ?? "";
    const prev = baseline[key] ?? "";
    if (next !== prev) {
      updates[key] = next;
    }
  }
  return updates;
}

export function useRuntimeStatus(options?: { enabled?: boolean; refetchIntervalMs?: number }) {
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
    mutationFn: (updates: Record<string, string>) => runtimeEndpoints.patchSettings({ updates }),
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
