import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";

import { RuntimeForm, shouldHydrateRuntimeForm } from "@/features/settings/runtime-form";

vi.mock("@/features/settings/use-runtime-settings", () => ({
  flattenRuntimeSettingsValues: (snapshot?: {
    categories?: Array<{ fields?: Array<{ key: string; value?: string }> }>;
  }) =>
    Object.fromEntries(
      (snapshot?.categories ?? []).flatMap((category) =>
        (category.fields ?? []).map((field) => [field.key, field.value ?? ""]),
      ),
    ),
  flattenRuntimeSettingsMaskedValues: (snapshot?: {
    categories?: Array<{ fields?: Array<{ key: string; value?: string; masked_value?: string }> }>;
  }) =>
    Object.fromEntries(
      (snapshot?.categories ?? []).flatMap((category) =>
        (category.fields ?? []).map((field) => [
          field.key,
          field.masked_value ?? field.value ?? "",
        ]),
      ),
    ),
  runtimeEditableKeysFromSnapshot: (snapshot?: {
    categories?: Array<{ fields?: Array<{ key: string; editable?: boolean }> }>;
  }) =>
    (snapshot?.categories ?? []).flatMap((category) =>
      (category.fields ?? []).filter((field) => field.editable).map((field) => field.key),
    ),
  runtimeSecretKeysFromSnapshot: (snapshot?: {
    categories?: Array<{ fields?: Array<{ key: string; editable?: boolean; secret?: boolean }> }>;
  }) =>
    (snapshot?.categories ?? []).flatMap((category) =>
      (category.fields ?? [])
        .filter((field) => field.editable && field.secret)
        .map((field) => field.key),
    ),
  useRuntimeSettings: () => ({
    settingsQuery: {
      data: {
        env_path: "/tmp/.env",
        categories: [
          {
            id: "llm",
            label: "LLM provider and models",
            description: "Planner and provider settings.",
            fields: [
              {
                key: "DSPY_LM_MODEL",
                label: "Planner LM model",
                description: "Planner model identifier.",
                value: "openai/gemini-3-flash-preview",
                masked_value: "openai/gemini-3-flash-preview",
                secret: false,
                editable: true,
              },
              {
                key: "DSPY_LM_API_BASE",
                label: "Provider API base",
                description: "Optional base URL for LM provider routing.",
                value: "https://api.example.com/v1",
                masked_value: "https://api.example.com/v1",
                secret: false,
                editable: true,
              },
              {
                key: "DSPY_LM_MAX_TOKENS",
                label: "Planner max tokens",
                description: "Maximum token budget per planner response.",
                value: "64000",
                masked_value: "64000",
                secret: false,
                editable: true,
              },
              {
                key: "FLEET_RLM_ACTION_MAX_TOKENS",
                label: "RLM action max tokens",
                description:
                  "Maximum model tokens for each RLM action-generation call. This is separate from REPL max_output_chars.",
                value: "4096",
                masked_value: "4096",
                secret: false,
                editable: true,
              },
            ],
          },
          {
            id: "api_keys",
            label: "API keys and credentials",
            description: "Write-only credentials.",
            fields: [
              {
                key: "DSPY_LLM_API_KEY",
                label: "Primary LM API key",
                description:
                  "Primary provider key for LM calls. Leave unchanged to keep current value.",
                value: "sk-...yz",
                masked_value: "sk-...yz",
                secret: true,
                editable: true,
              },
              {
                key: "DAYTONA_API_KEY",
                label: "Daytona API key",
                description: "API key for Daytona Workspace provisioning.",
                value: "daytona-...12",
                masked_value: "daytona-...12",
                secret: true,
                editable: true,
              },
            ],
          },
          {
            id: "sandbox_volumes",
            label: "Sandbox and volumes",
            description: "Daytona runtime settings.",
            fields: [
              {
                key: "DAYTONA_API_URL",
                label: "Daytona API URL",
                description: "URL for Daytona API.",
                value: "https://daytona.example.com",
                masked_value: "https://daytona.example.com",
                secret: false,
                editable: true,
              },
              {
                key: "DAYTONA_TARGET",
                label: "Daytona target",
                description: "Execution target/backend for Daytona provisioning.",
                value: "local",
                masked_value: "local",
                secret: false,
                editable: true,
              },
            ],
          },
        ],
      },
    },
    statusQuery: {
      data: {
        app_env: "local",
        write_enabled: true,
        settings_write_enabled: true,
        profile_write_enabled: true,
        ready: false,
        sandbox_provider: "daytona",
        active_models: {
          planner: "openai/gemini-3-flash-preview",
          delegate: "openai/gemini-3-flash-preview",
          delegate_small: "openai/gemini-3-flash-preview",
        },
        llm: { model_set: true, api_key_set: true, planner_configured: false },
        daytona: { configured: true, api_key_set: true, target_set: true },
        tests: {
          daytona: {
            kind: "daytona",
            ok: false,
            preflight_ok: false,
            checked_at: "2026-02-20T00:00:00+00:00",
            checks: {},
            guidance: [],
            error: "Daytona preflight checks failed.",
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

vi.mock("@/features/settings/use-llm-profiles", () => ({
  useLlmRoleBindings: () => ({
    data: {
      bindings: [
        {
          role: "planner",
          profile_id: "profile-google",
          profile_name: "Gemini",
          model_id: "gemini-3-flash-preview",
        },
      ],
    },
    isPending: false,
  }),
  useLlmProfileModels: () => ({
    data: {
      models: [
        {
          id: "gemini-3-flash-preview",
          label: "gemini-3-flash-preview",
        },
      ],
    },
    isPending: false,
  }),
}));

describe("RuntimeForm", () => {
  it("hydrates runtime form only when snapshot exists and no unsaved edits", () => {
    expect(shouldHydrateRuntimeForm(undefined, false)).toBe(false);
    expect(shouldHydrateRuntimeForm({ categories: [] }, true)).toBe(false);
    expect(shouldHydrateRuntimeForm({ categories: [] }, false)).toBe(true);
  });

  it("renders masked runtime values and smoke-test states", () => {
    const html = renderToStaticMarkup(<RuntimeForm />);

    expect(html).toContain("Runtime Status");
    expect(html).toContain("Needs Attention");
    expect(html).toContain("Active Models");
    expect(html).toContain("Planner: openai/gemini-3-flash-preview");
    expect(html).toContain("sk-...yz");
    expect(html).toContain("Write-only input. Configured value");
    expect(html).toContain("Clear saved value");
    expect(html).toContain("Runtime Configuration");
    expect(html).toContain("RLM action max tokens");
    expect(html).toContain("separate from REPL max_output_chars");
    expect(html).toContain("Execution target/backend for Daytona provisioning");
    expect(html).toContain("Daytona API URL");
    expect(html).toContain("Daytona Smoke");
    expect(html).toContain("Preflight failed");
    expect(html).toContain("LM Smoke");
    expect(html).toContain("Pass");
    expect(html).toContain("Test Credentials + Connection");
    expect(html).toContain("Test LM");
    expect(html).toContain("Test Daytona");
    expect(html).toContain("Test All Connections");
  });
});
