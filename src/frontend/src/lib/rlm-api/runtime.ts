import { rlmApiClient } from "@/lib/rlm-api/client";
import type {
  RuntimeConnectivityTestResponse,
  RuntimeSettingsSnapshot,
  RuntimeSettingsUpdateResponse,
  RuntimeStatusResponse,
} from "@/lib/rlm-api/types";

export interface RuntimeSettingsPatchInput {
  updates: Record<string, string>;
}

export const runtimeEndpoints = {
  settings(signal?: AbortSignal) {
    return rlmApiClient.get<RuntimeSettingsSnapshot>(
      "/api/v1/runtime/settings",
      signal,
    );
  },

  patchSettings(input: RuntimeSettingsPatchInput, signal?: AbortSignal) {
    return rlmApiClient.patch<RuntimeSettingsUpdateResponse>(
      "/api/v1/runtime/settings",
      input,
      signal,
    );
  },

  testModal(signal?: AbortSignal) {
    return rlmApiClient.post<RuntimeConnectivityTestResponse>(
      "/api/v1/runtime/tests/modal",
      undefined,
      signal,
    );
  },

  testLm(signal?: AbortSignal) {
    return rlmApiClient.post<RuntimeConnectivityTestResponse>(
      "/api/v1/runtime/tests/lm",
      undefined,
      signal,
    );
  },

  status(signal?: AbortSignal) {
    return rlmApiClient.get<RuntimeStatusResponse>(
      "/api/v1/runtime/status",
      signal,
    );
  },
};
