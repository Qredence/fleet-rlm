import { describe, expect, it } from "vite-plus/test";

import { getWorkspaceRuntimeGuard } from "@/features/workspace/runtime-guard";
import type { RuntimeStatusResponse } from "@/lib/rlm-api";

function makeStatus(overrides: Partial<RuntimeStatusResponse> = {}): RuntimeStatusResponse {
  return {
    app_env: "local",
    write_enabled: true,
    ready: true,
    sandbox_provider: "daytona",
    active_models: {
      planner: "openai/gpt-4.1-mini",
      delegate: "openai/gpt-4.1-mini",
      delegate_small: "openai/gpt-4.1-mini",
    },
    llm: {
      model_set: true,
      api_key_set: true,
      planner_configured: true,
    },
    daytona: {
      configured: true,
      api_key_set: true,
      api_url_set: true,
      target_set: true,
    },
    tests: {
      lm: null,
      daytona: null,
    },
    guidance: [],
    ...overrides,
  };
}

describe("getWorkspaceRuntimeGuard", () => {
  it("blocks Workbench runs when runtime preflight checks are missing", () => {
    const guard = getWorkspaceRuntimeGuard(
      makeStatus({
        ready: false,
        llm: {
          model_set: true,
          api_key_set: false,
          planner_configured: false,
        },
        guidance: ["DSPY_LLM_API_KEY (or DSPY_LM_API_KEY) is not set."],
      }),
    );

    expect(guard.blocked).toBe(true);
    expect(guard.showWarning).toBe(true);
    expect(guard.title).toBe("Runtime configuration required");
    expect(guard.guidance).toContain("DSPY_LLM_API_KEY (or DSPY_LM_API_KEY) is not set.");
  });

  it("surfaces cached failing runtime tests as blocking guidance", () => {
    const guard = getWorkspaceRuntimeGuard(
      makeStatus({
        ready: false,
        tests: {
          lm: {
            kind: "lm",
            ok: false,
            preflight_ok: true,
            checked_at: "2026-04-16T10:00:00Z",
            checks: {},
            guidance: ["Check API connectivity and credentials."],
            error: "LM test timed out after 20s.",
          },
          daytona: null,
        },
      }),
    );

    expect(guard.blocked).toBe(true);
    expect(guard.guidance).toContain("LM test timed out after 20s.");
    expect(guard.guidance).toContain("Check API connectivity and credentials.");
  });

  it("keeps warning-only state when runtime checks are merely recommended", () => {
    const guard = getWorkspaceRuntimeGuard(
      makeStatus({
        ready: false,
        guidance: ["Run Runtime connection tests to validate live provider connectivity."],
      }),
    );

    expect(guard.blocked).toBe(false);
    expect(guard.showWarning).toBe(true);
    expect(guard.title).toBe("Runtime checks recommended");
  });
});
