import { rlmApiClient } from "@/lib/rlm-api/client";
import type { ServiceInfoResponse } from "@/lib/rlm-api/types";

export const infoEndpoints = {
  /**
   * Fetch a stable snapshot of build metadata and active feature flags
   * for the running server instance (`GET /api/v1/info`).
   */
  get(signal?: AbortSignal) {
    return rlmApiClient.get<ServiceInfoResponse>("/api/v1/info", signal);
  },
};
