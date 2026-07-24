import { describe, expect, it } from "vitest";

import type { Message } from "../store.js";
import { committedTokenCounts, observedTokenCounts } from "../usage-summary.js";

describe("observed usage summaries", () => {
  it("accepts DSPy prompt/completion and input/output token names without double counting aliases", () => {
    expect(
      observedTokenCounts({
        root: { prompt_tokens: 10, input_tokens: 99, completion_tokens: 4, output_tokens: 88 },
        sub: { input_tokens: 7, output_tokens: 3 },
      }),
    ).toEqual({ input: 17, output: 7 });
  });

  it("keeps absent observations unknown instead of inventing zero usage", () => {
    expect(observedTokenCounts({ root: { total_tokens: 12 } })).toEqual({
      input: null,
      output: null,
    });
    expect(observedTokenCounts({})).toEqual({ input: null, output: null });
  });

  it("sums only committed usage messages present in the conversation", () => {
    const usage = (
      id: string,
      inputTokens: number | null,
      outputTokens: number | null,
    ): Message => ({
      id,
      kind: "usage",
      runId: id,
      iterations: 1,
      inputTokens,
      outputTokens,
      durationMs: 1,
      observedLmUsage: {},
      ts: 1,
    });

    expect(
      committedTokenCounts([
        usage("one", 10, 2),
        { id: "error", kind: "error", text: "failed", ts: 2 },
        usage("two", 4, null),
      ]),
    ).toEqual({ input: 14, output: 2 });
  });
});
