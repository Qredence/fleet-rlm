import { RlmApiError, rlmApiClient } from "@/lib/rlm-api/client";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import {
  applyMockRuntimeUpdates,
  getMockLmTest,
  getMockModalTest,
  getMockDaytonaTest,
  getMockRuntimeSettings,
  getMockRuntimeStatus,
} from "@/lib/data/mock/runtime";
import type {
  RuntimeConnectivityTestResponse,
  RuntimeSettingsSnapshot,
  RuntimeSettingsUpdateResponse,
  RuntimeStatusResponse,
} from "@/lib/rlm-api/types";

export interface RuntimeSettingsPatchInput {
  updates: Record<string, string>;
}

const localLoopbackHosts = new Set(["127.0.0.1", "localhost"]);

function hasLocalBackendBaseUrl(): boolean {
  if (!rlmApiConfig.baseUrl) return false;

  try {
    return localLoopbackHosts.has(new URL(rlmApiConfig.baseUrl).hostname);
  } catch {
    return false;
  }
}

function isRetryableRuntimeFailure(error: unknown): boolean {
  if (error instanceof RlmApiError) {
    return (
      error.status === 404 || error.status === 502 || error.status === 503 || error.status === 504
    );
  }

  return error instanceof SyntaxError || error instanceof TypeError;
}

function shouldUseRuntimeReadFallback(error: unknown): boolean {
  const supportsFrontendFallback =
    rlmApiConfig.mockMode || rlmApiConfig.e2eMode || hasLocalBackendBaseUrl();

  return supportsFrontendFallback && isRetryableRuntimeFailure(error);
}

function shouldUseRuntimeWriteFallback(error: unknown): boolean {
  const supportsFrontendFallback = rlmApiConfig.mockMode || rlmApiConfig.e2eMode;

  return supportsFrontendFallback && isRetryableRuntimeFailure(error);
}

async function withRuntimeFallback<T>(
  request: () => Promise<T>,
  fallback: () => T,
  shouldFallback: (error: unknown) => boolean = shouldUseRuntimeReadFallback,
): Promise<T> {
  try {
    return await request();
  } catch (error) {
    if (shouldFallback(error)) {
      return fallback();
    }
    throw error;
  }
}

export const runtimeEndpoints = {
  settings(signal?: AbortSignal) {
    return withRuntimeFallback(
      () => rlmApiClient.get<RuntimeSettingsSnapshot>("/api/v1/runtime/settings", signal),
      () => getMockRuntimeSettings(),
    );
  },

  patchSettings(input: RuntimeSettingsPatchInput, signal?: AbortSignal) {
    return withRuntimeFallback(
      () =>
        rlmApiClient.patch<RuntimeSettingsUpdateResponse>(
          "/api/v1/runtime/settings",
          input,
          signal,
        ),
      () => applyMockRuntimeUpdates(input.updates),
      shouldUseRuntimeWriteFallback,
    );
  },

  testModal(signal?: AbortSignal) {
    return withRuntimeFallback(
      () =>
        rlmApiClient.post<RuntimeConnectivityTestResponse>(
          "/api/v1/runtime/tests/modal",
          undefined,
          signal,
        ),
      () => getMockModalTest(),
    );
  },

  testLm(signal?: AbortSignal) {
    return withRuntimeFallback(
      () =>
        rlmApiClient.post<RuntimeConnectivityTestResponse>(
          "/api/v1/runtime/tests/lm",
          undefined,
          signal,
        ),
      () => getMockLmTest(),
    );
  },

  testDaytona(signal?: AbortSignal) {
    return withRuntimeFallback(
      () =>
        rlmApiClient.post<RuntimeConnectivityTestResponse>(
          "/api/v1/runtime/tests/daytona",
          undefined,
          signal,
        ),
      () => getMockDaytonaTest(),
    );
  },

  status(signal?: AbortSignal) {
    return withRuntimeFallback(
      () => rlmApiClient.get<RuntimeStatusResponse>("/api/v1/runtime/status", signal),
      () => getMockRuntimeStatus(),
    );
  },
};
