/**
 * Evaluations API client for MLflow GenAI evaluation subsystem.
 *
 * Provides typed methods for:
 * - Starting evaluation runs
 * - Listing evaluation runs
 * - Fetching evaluation reports
 */

import type { components } from "@/lib/rlm-api/generated/openapi";
import { typedClient, unwrap, withTimeout } from "@/lib/rlm-api/typed-client";

export type EvaluationRequest = components["schemas"]["EvaluationRequest"];
export type EvaluationRunResponse = components["schemas"]["EvaluationRunResponse"];
export type EvaluationRunListItem = components["schemas"]["EvaluationRunListItem"];
export type EvaluationRunListResponse = components["schemas"]["EvaluationRunListResponse"];
export type EvaluationReportResponse = components["schemas"]["EvaluationReportResponse"];

export const evaluationsEndpoints = {
  /**
   * List all evaluation runs, most recent first.
   */
  list(signal?: AbortSignal) {
    return unwrap(typedClient.GET("/api/v1/evaluations", { signal: withTimeout(signal) }));
  },

  /**
   * Start a new evaluation run.
   */
  start(request: EvaluationRequest, signal?: AbortSignal) {
    return unwrap(
      typedClient.POST("/api/v1/evaluations", {
        body: request,
        signal: withTimeout(signal),
      }),
    );
  },

  /**
   * Fetch the full evaluation report for a specific run.
   */
  getReport(runId: string, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/evaluations/{run_id}", {
        params: { path: { run_id: runId } },
        signal: withTimeout(signal),
      }),
    );
  },
};
