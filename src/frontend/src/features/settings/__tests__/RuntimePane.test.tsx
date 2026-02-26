import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { RuntimePane } from "@/features/settings/RuntimePane";

vi.mock("@/features/settings/useRuntimeSettings", () => ({
  useRuntimeSettings: () => ({
    settingsQuery: {
      data: {
        env_path: "/tmp/.env",
        keys: [],
        values: {
          DSPY_LM_MODEL: "openai/gemini-3-flash-preview",
          DSPY_LLM_API_KEY: "sk-...yz",
          DSPY_LM_API_BASE: "https://api.example.com/v1",
          DSPY_LM_MAX_TOKENS: "64000",
          MODAL_TOKEN_ID: "tok...12",
          MODAL_TOKEN_SECRET: "***",
          SECRET_NAME: "LITELLM",
          VOLUME_NAME: "rlm-volume-dspy",
        },
        masked_values: {},
      },
    },
    statusQuery: {
      data: {
        app_env: "local",
        write_enabled: true,
        ready: false,
        llm: { model_set: true, api_key_set: true, planner_configured: false },
        modal: { credentials_available: true, secret_name_set: false },
        tests: {
          modal: {
            kind: "modal",
            ok: false,
            preflight_ok: false,
            checked_at: "2026-02-20T00:00:00+00:00",
            checks: {},
            guidance: [],
            error: "Modal preflight checks failed.",
          },
          lm: {
            kind: "lm",
            ok: true,
            preflight_ok: true,
            checked_at: "2026-02-20T00:00:05+00:00",
            checks: {},
            guidance: [],
            output_preview: "OK",
          },
        },
        guidance: ["Run Runtime connection tests to validate connectivity."],
      },
    },
    saveSettings: { isPending: false, mutate: vi.fn() },
    testModalConnection: { isPending: false, mutate: vi.fn() },
    testLmConnection: {
      isPending: false,
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
    },
    testAllConnections: vi.fn(),
  }),
  computeRuntimeUpdates: (
    current: Record<string, string>,
    baseline: Record<string, string>,
  ) => {
    const updates: Record<string, string> = {};
    for (const key of Object.keys(current)) {
      if ((current[key] ?? "") !== (baseline[key] ?? "")) {
        updates[key] = current[key] ?? "";
      }
    }
    return updates;
  },
}));

describe("RuntimePane", () => {
  it("renders masked runtime values and smoke-test states", () => {
    const html = renderToStaticMarkup(<RuntimePane />);

    expect(html).toContain("Runtime Status");
    expect(html).toContain("Needs Attention");
    expect(html).toContain("sk-...yz");
    expect(html).toContain("Modal Smoke");
    expect(html).toContain("Preflight failed");
    expect(html).toContain("LM Smoke");
    expect(html).toContain("Pass");
  });
});
