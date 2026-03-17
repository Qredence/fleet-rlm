import { RlmApiError, rlmApiClient } from "@/lib/rlm-api/client";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import {
  applyMockRuntimeUpdates,
  getMockLmTest,
  getMockModalTest,
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

function shouldUseRuntimeFallback(error: unknown): boolean {
  const localLoopbackHosts = new Set(["127.0.0.1", "localhost"]);
  let hasLocalBackendBaseUrl = false;
  if (rlmApiConfig.baseUrl) {
    try {
      hasLocalBackendBaseUrl = localLoopbackHosts.has(
        new URL(rlmApiConfig.baseUrl).hostname,
      );
    } catch {
      hasLocalBackendBaseUrl = false;
    }
  }

  const supportsFrontendFallback =
    !rlmApiConfig.baseUrl ||
    hasLocalBackendBaseUrl ||
    import.meta.env.VITE_E2E === "1";

  if (rlmApiConfig.mockMode) return true;
  if (!supportsFrontendFallback) return false;
  if (error instanceof RlmApiError) {
    return (
      error.status === 404 ||
      error.status === 502 ||
      error.status === 503 ||
      error.status === 504
    );
  }
  return error instanceof SyntaxError || error instanceof TypeError;
}

async function withRuntimeFallback<T>(
  request: () => Promise<T>,
  fallback: () => T,
): Promise<T> {
  try {
    return await request();
  } catch (error) {
    if (shouldUseRuntimeFallback(error)) {
      return fallback();
    }
    throw error;
  }
}

export const runtimeEndpoints = {
  settings(signal?: AbortSignal) {
    return withRuntimeFallback(
      () =>
        rlmApiClient.get<RuntimeSettingsSnapshot>(
          "/api/v1/runtime/settings",
          signal,
        ),
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

  status(signal?: AbortSignal) {
    return withRuntimeFallback(
      () =>
        rlmApiClient.get<RuntimeStatusResponse>(
          "/api/v1/runtime/status",
          signal,
        ),
      () => getMockRuntimeStatus(),
    );
  },
};
