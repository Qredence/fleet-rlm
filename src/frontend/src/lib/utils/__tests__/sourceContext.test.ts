import { describe, expect, it } from "vitest";

import { detectContextPaths } from "@/lib/utils/sourceContext";

describe("sourceContext", () => {
  it("detects absolute and home-relative host paths from prompt text", () => {
    expect(
      detectContextPaths(
        "Use /Users/zocho/Documents/spec.pdf and ~/notes/diligence.md for context.",
      ),
    ).toEqual(["/Users/zocho/Documents/spec.pdf", "~/notes/diligence.md"]);
  });

  it("deduplicates repeated host paths and ignores URLs", () => {
    expect(
      detectContextPaths(
        "Inspect https://github.com/qredence/fleet-rlm plus /tmp/context.md and /tmp/context.md.",
      ),
    ).toEqual(["/tmp/context.md"]);
  });
});
