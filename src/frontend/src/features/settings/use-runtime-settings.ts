import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryFunctionContext } from "@tanstack/react-query";

import { runtimeStatusQueryKey, useRuntimeStatus } from "@/hooks/runtime/use-runtime-status";
import { runtimeEndpoints } from "@/lib/rlm-api/runtime";
import type { RuntimeSettingsSnapshot } from "@/lib/rlm-api";

export const runtimeKeys = {
  all: ["runtime"] as const,
  settings: () => [...runtimeKeys.all, "settings"] as const,
  status: () => runtimeStatusQueryKey,
};

export const runtimeSettingsQueryOptions = {
  settings: () => ({
    queryKey: runtimeKeys.settings(),
    queryFn: ({ signal }: QueryFunctionContext) => runtimeEndpoints.settings(signal),
    staleTime: 5_000,
  }),
};

export const RUNTIME_EDITABLE_KEYS = [
  "DSPY_LM_MODEL",
  "DSPY_DELEGATE_LM_MODEL",
  "DSPY_DELEGATE_LM_SMALL_MODEL",
  "DSPY_DELEGATE_LM_MAX_TOKENS",
  "DSPY_LLM_API_KEY",
  "DSPY_LM_API_KEY",
  "DSPY_DELEGATE_LM_API_KEY",
  "DSPY_LM_API_BASE",
  "DSPY_LM_MAX_TOKENS",
  "DSPY_ADAPTER",
  "DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING",
  "DAYTONA_API_KEY",
  "POSTHOG_API_KEY",
  "DAYTONA_API_URL",
  "DAYTONA_TARGET",
  "VOLUME_NAME",
  "TIMEOUT",
  "INTERPRETER_ASYNC_EXECUTE",
  "DATABASE_URL",
  "DATABASE_ADMIN_URL",
  "DATABASE_REQUIRED",
  "DB_ECHO",
  "DB_VALIDATE_ON_STARTUP",
] as const;

export type RuntimeEditableKey = string;
export const RUNTIME_SECRET_EDITABLE_KEYS = [
  "DSPY_LLM_API_KEY",
  "DSPY_LM_API_KEY",
  "DSPY_DELEGATE_LM_API_KEY",
  "DAYTONA_API_KEY",
  "POSTHOG_API_KEY",
  "DATABASE_URL",
  "DATABASE_ADMIN_URL",
] as const;
export type RuntimeSecretEditableKey = string;

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

export interface RuntimeSettingsFieldMetadata {
  key: string;
  label: string;
  description: string;
  value?: string;
  masked_value?: string;
  secret?: boolean;
  editable?: boolean;
  reload_required?: boolean;
  placeholder?: string | null;
  default?: string | null;
}

export interface RuntimeSettingsCategoryMetadata {
  id: string;
  label: string;
  description: string;
  fields?: RuntimeSettingsFieldMetadata[];
}

export type CategorizedRuntimeSettingsSnapshot = RuntimeSettingsSnapshot & {
  categories?: RuntimeSettingsCategoryMetadata[];
};

export function flattenRuntimeSettingsValues(
  snapshot?: CategorizedRuntimeSettingsSnapshot,
): Record<string, string> {
  const values: Record<string, string> = {};
  for (const category of snapshot?.categories ?? []) {
    for (const field of category.fields ?? []) {
      values[field.key] = field.value ?? "";
    }
  }
  return values;
}

export function flattenRuntimeSettingsMaskedValues(
  snapshot?: CategorizedRuntimeSettingsSnapshot,
): Record<string, string> {
  const values: Record<string, string> = {};
  for (const category of snapshot?.categories ?? []) {
    for (const field of category.fields ?? []) {
      values[field.key] = field.masked_value ?? field.value ?? "";
    }
  }
  return values;
}

export function runtimeEditableKeysFromSnapshot(
  snapshot?: CategorizedRuntimeSettingsSnapshot,
): RuntimeEditableKey[] {
  const keys = new Set<string>();
  for (const category of snapshot?.categories ?? []) {
    for (const field of category.fields ?? []) {
      if (field.editable) {
        keys.add(field.key);
      }
    }
  }
  return [...keys];
}

export function runtimeSecretKeysFromSnapshot(
  snapshot?: CategorizedRuntimeSettingsSnapshot,
): RuntimeSecretEditableKey[] {
  const keys = new Set<string>();
  for (const category of snapshot?.categories ?? []) {
    for (const field of category.fields ?? []) {
      if (field.editable && field.secret) {
        keys.add(field.key);
      }
    }
  }
  return [...keys];
}

export interface RuntimeUpdateComputationOptions<SecretKey extends string> {
  secretInputs?: Partial<Record<SecretKey, string>>;
  clearedSecrets?: Iterable<SecretKey>;
}

function toSecretSet<SecretKey extends string>(
  options?: RuntimeUpdateComputationOptions<SecretKey>,
): Set<SecretKey> {
  if (!options?.clearedSecrets) return new Set<SecretKey>();
  return new Set(options.clearedSecrets);
}

function computeSecretUpdate<SecretKey extends string>(
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
  editableKeys?: readonly string[],
  secretKeys: readonly string[] = RUNTIME_SECRET_EDITABLE_KEYS,
): Record<string, string> {
  const updates: Record<string, string> = {};
  const resolvedEditableKeys =
    editableKeys ??
    Array.from(
      new Set([
        ...Object.keys(current),
        ...Object.keys(options?.secretInputs ?? {}),
        ...Array.from(options?.clearedSecrets ?? []),
      ]),
    );
  for (const key of resolvedEditableKeys) {
    if (secretKeys.includes(key)) {
      computeSecretUpdate(key, updates, options);
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

export function useRuntimeSettings() {
  const queryClient = useQueryClient();
  const canQueryRuntime = typeof window !== "undefined";

  const settingsQuery = useQuery({
    ...runtimeSettingsQueryOptions.settings(),
    enabled: canQueryRuntime,
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

  const testLmConnection = useMutation({
    mutationFn: () => runtimeEndpoints.testLm(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: runtimeKeys.status() });
    },
  });

  const testDaytonaConnection = useMutation({
    mutationFn: () => runtimeEndpoints.testDaytona(),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: runtimeKeys.status() });
    },
  });

  const testAllConnections = useCallback(async () => {
    const lm = await testLmConnection.mutateAsync();
    const daytona = await testDaytonaConnection.mutateAsync();
    return { lm, daytona };
  }, [testDaytonaConnection, testLmConnection]);

  return {
    settingsQuery,
    statusQuery,
    saveSettings,
    testDaytonaConnection,
    testLmConnection,
    testAllConnections,
  };
}
