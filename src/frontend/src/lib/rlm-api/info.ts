import { typedClient, unwrap, withTimeout } from "@/lib/rlm-api/typed-client";

export const infoEndpoints = {
  /**
   * Fetch a stable snapshot of build metadata and active feature flags
   * for the running server instance (`GET /api/v1/info`).
   */
  get(signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/info", { signal: withTimeout(signal) }),
    );
  },
};
