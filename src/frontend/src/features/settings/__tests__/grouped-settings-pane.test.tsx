import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ComponentProps } from "react";

import { GroupedSettingsPane } from "@/features/settings";

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
  computeRuntimeUpdates: (current: Record<string, string>, baseline: Record<string, string>) => {
    const updates: Record<string, string> = {};
    for (const key of [
      "DSPY_LM_MODEL",
      "DSPY_DELEGATE_LM_MODEL",
      "DSPY_DELEGATE_LM_SMALL_MODEL",
      "DSPY_LLM_API_KEY",
      "DSPY_LM_API_BASE",
      "DSPY_LM_MAX_TOKENS",
      "DAYTONA_API_KEY",
      "DAYTONA_API_URL",
      "DAYTONA_TARGET",
    ]) {
      if ((current[key] ?? "") !== (baseline[key] ?? "")) {
        updates[key] = current[key] ?? "";
      }
    }
    return updates;
  },
  computeLmRuntimeUpdates: (current: Record<string, string>, baseline: Record<string, string>) => {
    const updates: Record<string, string> = {};
    for (const key of [
      "DSPY_LM_MODEL",
      "DSPY_DELEGATE_LM_MODEL",
      "DSPY_DELEGATE_LM_SMALL_MODEL",
      "DSPY_LM_API_BASE",
      "DSPY_LLM_API_KEY",
    ]) {
      if ((current[key] ?? "") !== (baseline[key] ?? "")) {
        updates[key] = current[key] ?? "";
      }
    }
    return updates;
  },
  useRuntimeSettings: () => ({
    settingsQuery: {
      data: {
        categories: [
          {
            id: "llm",
            label: "LLM provider and models",
            description: "Planner and provider settings.",
            fields: [
              {
                key: "DSPY_LM_MODEL",
                label: "Planner LM model",
                description: "Model identifier for the planner runtime.",
                value: "openai/gpt-4o-mini",
                masked_value: "openai/gpt-4o-mini",
                secret: false,
                editable: true,
              },
              {
                key: "DSPY_DELEGATE_LM_MODEL",
                label: "Delegate LM model",
                description: "Optional model identifier for recursive delegate turns.",
                value: "openai/gpt-4.1-mini",
                masked_value: "openai/gpt-4.1-mini",
                secret: false,
                editable: true,
              },
              {
                key: "DSPY_DELEGATE_LM_SMALL_MODEL",
                label: "Delegate small LM model",
                description: "Optional small model used by lightweight delegate tasks.",
                value: "openai/gpt-4o-mini",
                masked_value: "openai/gpt-4o-mini",
                secret: false,
                editable: true,
              },
              {
                key: "DSPY_LM_API_BASE",
                label: "Provider API base",
                description: "Custom API endpoint for LiteLLM-compatible providers.",
                value: "https://litellm.example.com/v1",
                masked_value: "https://litellm.example.com/v1",
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
                label: "API key",
                description: "Primary provider key for planner and fallback delegate LM calls.",
                value: "[REDACTED:api-key]",
                masked_value: "[REDACTED:api-key]",
                secret: true,
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
        llm: {
          model_set: true,
          api_key_set: true,
          planner_configured: false,
        },
        daytona: { configured: true, api_key_set: true, target_set: true },
        tests: {
          daytona: null,
          lm: null,
        },
        guidance: ["Run Runtime tests from Settings -> Runtime."],
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
    testAllConnections: vi.fn(async () => ({
      daytona: { ok: true },
      lm: { ok: true },
    })),
  }),
}));

vi.mock("@/features/settings/use-llm-profiles", () => ({
  useLlmProfiles: () => ({
    data: [],
    isPending: false,
    isError: false,
    error: null,
  }),
  useLlmRoleBindings: () => ({
    data: { bindings: [] },
    isPending: false,
    isError: false,
    error: null,
  }),
  useLlmProfileModels: () => ({
    data: { models: [] },
    isPending: false,
    isError: false,
    error: null,
  }),
  useLlmProfilesMutations: () => ({
    createProfile: { isPending: false, mutate: vi.fn() },
    importFromEnv: { isPending: false, mutate: vi.fn() },
    saveRoleBindings: { isPending: false, mutate: vi.fn() },
    testProfile: { isPending: false, mutate: vi.fn() },
    removeProfile: { isPending: false, mutate: vi.fn() },
    refreshProfileModels: { isPending: false, mutate: vi.fn() },
  }),
}));

describe("GroupedSettingsPane", () => {
  function renderGroupedSettingsPane(props: ComponentProps<typeof GroupedSettingsPane>) {
    const queryClient = new QueryClient();
    return renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <GroupedSettingsPane {...props} />
      </QueryClientProvider>,
    );
  }

  it("renders the grouped settings surface by default", () => {
    const html = renderGroupedSettingsPane({ isDark: false, onToggleTheme: vi.fn() });

    expect(html).toContain("Theme");
    expect(html).toContain("Anonymous telemetry");
    expect(html).toContain("LLM Providers");
    expect(html).toContain("Import from .env");
    expect(html).toContain("Add provider profile");
    expect(html).toContain("Planner model");
    expect(html).toContain("Delegate model");
    expect(html).toContain("Delegate small model");
    expect(html).toContain("Runtime Status");
    expect(html).toContain("Test Credentials + Connection");
    expect(html).toContain("Run Runtime tests from Settings -&gt; Runtime.");

    expect(html).not.toContain("Notifications");
    expect(html).not.toContain("Personalization");
    expect(html).not.toContain("Billing");
    expect(html).not.toContain("Account");
    expect(html).not.toContain("Data &amp; Privacy");
  });

  it("renders telemetry-only content when section is telemetry", () => {
    const html = renderGroupedSettingsPane({
      isDark: false,
      onToggleTheme: vi.fn(),
      section: "telemetry",
    });

    expect(html).toContain("Anonymous telemetry");
    expect(html).toContain("Telemetry scope");
    expect(html).not.toContain("Theme");
    expect(html).not.toContain("Planner model");
    expect(html).not.toContain("Runtime Status");
  });

  it("renders runtime-only content when section is runtime", () => {
    const html = renderGroupedSettingsPane({
      isDark: false,
      onToggleTheme: vi.fn(),
      section: "runtime",
    });

    expect(html).toContain("Runtime Status");
    expect(html).toContain("Test Credentials + Connection");
    expect(html).toContain("Runtime Configuration");
    expect(html).not.toContain("Anonymous telemetry");
    expect(html).not.toContain("LiteLLM integration");
  });
});
