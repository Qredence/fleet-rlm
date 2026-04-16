import { RlmApiError, rlmApiClient } from "@/lib/rlm-api/client";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import {
  applyMockRuntimeUpdates,
  getMockLmTest,
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
const RUNTIME_CONNECTION_TEST_TIMEOUT_MS = 10_000;

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

async function runRuntimeConnectionTest(
  path: "/api/v1/runtime/tests/lm" | "/api/v1/runtime/tests/daytona",
  label: "LM" | "Daytona",
  fallback: () => RuntimeConnectivityTestResponse,
  signal?: AbortSignal,
) {
  return withRuntimeFallback(async () => {
    try {
      return await rlmApiClient.post<RuntimeConnectivityTestResponse>(
        path,
        undefined,
        signal,
        RUNTIME_CONNECTION_TEST_TIMEOUT_MS,
      );
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        throw new Error(
          `${label} test request timed out after ${Math.ceil(RUNTIME_CONNECTION_TEST_TIMEOUT_MS / 1000)} seconds.`,
        );
      }
      throw error;
    }
  }, fallback);
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

  testLm(signal?: AbortSignal) {
    return runRuntimeConnectionTest(
      "/api/v1/runtime/tests/lm",
      "LM",
      () => getMockLmTest(),
      signal,
    );
  },

  testDaytona(signal?: AbortSignal) {
    return runRuntimeConnectionTest(
      "/api/v1/runtime/tests/daytona",
      "Daytona",
      () => getMockDaytonaTest(),
      signal,
    );
  },

  status(signal?: AbortSignal) {
    return withRuntimeFallback(
      () => rlmApiClient.get<RuntimeStatusResponse>("/api/v1/runtime/status", signal),
      () => getMockRuntimeStatus(),
    );
  },
};
