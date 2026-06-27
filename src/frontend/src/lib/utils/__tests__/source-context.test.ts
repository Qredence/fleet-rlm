import { describe, expect, it } from "vite-plus/test";

import { detectContextPaths } from "@/lib/utils/source-context";

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

  it("drops bare URL-route tokens like /docs but keeps multi-segment paths", () => {
    // /docs is a single-segment route with no extension -> dropped.
    expect(detectContextPaths("see the /docs endpoint")).toEqual([]);

    // ~/ relative and multi-segment absolutes are kept.
    expect(detectContextPaths("check ~/notes.md and /etc/hosts")).toEqual([
      "~/notes.md",
      "/etc/hosts",
    ]);

    // /api/v1/x is multi-segment -> kept (only single-segment routes drop).
    expect(detectContextPaths("hit /api/v1/x")).toEqual(["/api/v1/x"]);
  });
});
