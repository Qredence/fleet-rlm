import { rlmApiClient } from "@/lib/rlm-api/client";
import type { TraceFeedbackRequest, TraceFeedbackResponse } from "@/lib/rlm-api/types";

export const traceEndpoints = {
  createFeedback(input: TraceFeedbackRequest, signal?: AbortSignal) {
    return rlmApiClient.post<TraceFeedbackResponse>("/api/v1/traces/feedback", input, signal);
  },
};
