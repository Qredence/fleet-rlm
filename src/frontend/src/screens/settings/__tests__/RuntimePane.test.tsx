import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";

import { RuntimePane } from "@/screens/settings/runtime-pane";
import { shouldHydrateRuntimeForm } from "@/screens/settings/runtime-pane-hydration";

vi.mock("@/screens/settings/hooks/use-runtime-settings", () => ({
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
        masked_values: {
          DSPY_LM_MODEL: "openai/gemini-3-flash-preview",
          DSPY_LLM_API_KEY: "sk-...yz",
          DSPY_LM_API_BASE: "https://api.example.com/v1",
          DSPY_LM_MAX_TOKENS: "64000",
          MODAL_TOKEN_ID: "tok...12",
          MODAL_TOKEN_SECRET: "***",
          SECRET_NAME: "LITELLM",
          VOLUME_NAME: "rlm-volume-dspy",
        },
      },
    },
    statusQuery: {
      data: {
        app_env: "local",
        write_enabled: true,
        ready: false,
        sandbox_provider: "modal",
        active_models: {
          planner: "openai/gemini-3-flash-preview",
          delegate: "openai/gemini-3-flash-preview",
          delegate_small: "openai/gemini-3-flash-preview",
        },
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
    testDaytonaConnection: {
      isPending: false,
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
    },
    testLmConnection: {
      isPending: false,
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
    },
    testAllConnections: vi.fn(),
  }),
  computeRuntimeUpdates: (current: Record<string, string>, baseline: Record<string, string>) => {
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
  it("hydrates runtime form only when snapshot exists and no unsaved edits", () => {
    expect(shouldHydrateRuntimeForm(undefined, false)).toBe(false);
    expect(shouldHydrateRuntimeForm({ values: {} }, true)).toBe(false);
    expect(shouldHydrateRuntimeForm({ values: {} }, false)).toBe(true);
  });

  it("renders masked runtime values and smoke-test states", () => {
    const html = renderToStaticMarkup(<RuntimePane />);

    expect(html).toContain("Runtime Status");
    expect(html).toContain("Needs Attention");
    expect(html).toContain("Active Models");
    expect(html).toContain("Planner: openai/gemini-3-flash-preview");
    expect(html).toContain("sk-...yz");
    expect(html).toContain("Write-only input. Configured value");
    expect(html).toContain("Clear saved value");
    expect(html).toContain("Runtime Configuration");
    expect(html).toContain("Modal Smoke");
    expect(html).toContain("Preflight failed");
    expect(html).toContain("LM Smoke");
    expect(html).toContain("Pass");
    expect(html).toContain("Test Credentials + Connection");
    expect(html).toContain("Test Modal");
    expect(html).toContain("Test LM");
    expect(html).toContain("Test Daytona");
    expect(html).toContain("Test All Connections");
  });
});
