import { describe, expect, it } from "vitest";

import {
  detectRepoContext,
  normalizeRepoUrl,
  resolveRepoContext,
} from "@/lib/utils/repoContext";

describe("repoContext", () => {
  it("detects a bare GitHub repo URL from prompt text", () => {
    expect(
      detectRepoContext(
        "Please inspect https://github.com/qredence/fleet-rlm and summarize it.",
      ),
    ).toEqual({
      repoUrl: "https://github.com/qredence/fleet-rlm",
      source: "prompt_url",
      matchedText: "https://github.com/qredence/fleet-rlm",
    });
  });

  it("detects an @url repo mention from prompt text", () => {
    expect(
      detectRepoContext(
        "Analyze @https://github.com/qredence/fleet-rlm/tree/main/src for the tracing flow.",
      ),
    ).toEqual({
      repoUrl: "https://github.com/qredence/fleet-rlm",
      source: "prompt_mention",
      matchedText: "@https://github.com/qredence/fleet-rlm/tree/main/src",
    });
  });

  it("ignores non-repository URLs", () => {
    expect(
      detectRepoContext(
        "Use https://example.com/docs and then answer the question.",
      ),
    ).toBeNull();
  });

  it("keeps manual overrides authoritative when they are valid", () => {
    expect(
      resolveRepoContext({
        manualRepoUrl: "https://gitlab.com/example/project",
        promptText: "Also inspect https://github.com/qredence/fleet-rlm",
      }),
    ).toMatchObject({
      repoUrl: "https://gitlab.com/example/project",
      source: "manual",
    });
  });

  it("does not fall back to detected repos while an invalid manual override is present", () => {
    expect(
      resolveRepoContext({
        manualRepoUrl: "not-a-repo",
        promptText: "Analyze https://github.com/qredence/fleet-rlm",
      }),
    ).toBeNull();
  });

  it("normalizes manual repo URLs for supported hosts", () => {
    expect(
      normalizeRepoUrl("https://github.com/qredence/fleet-rlm.git"),
    ).toBe("https://github.com/qredence/fleet-rlm");
  });
});
