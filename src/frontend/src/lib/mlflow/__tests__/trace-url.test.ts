import { describe, expect, it } from "vitest";

import { buildMlflowTraceUrl, normalizeTrackingUri } from "@/lib/mlflow/trace-url";

describe("mlflow trace url helpers", () => {
  it("normalizes tracking URIs", () => {
    expect(normalizeTrackingUri("http://127.0.0.1:5001/")).toBe("http://127.0.0.1:5001");
  });

  it("builds experiment-scoped trace links", () => {
    expect(
      buildMlflowTraceUrl({
        trackingUri: "http://127.0.0.1:5001",
        experimentId: "1",
        traceId: "tr-abc",
      }),
    ).toBe("http://127.0.0.1:5001/#/experiments/1/traces/tr-abc");
  });

  it("falls back to trace search when experiment id is missing", () => {
    expect(
      buildMlflowTraceUrl({
        trackingUri: "http://127.0.0.1:5001",
        traceId: "tr-abc",
      }),
    ).toBe("http://127.0.0.1:5001/#/traces?search=tr-abc");
  });
});
