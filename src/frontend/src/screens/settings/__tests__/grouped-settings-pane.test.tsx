import { describe, expect, it, vi } from "vite-plus/test";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ComponentProps } from "react";

import { GroupedSettingsPane } from "@/screens/settings/settings-screen";

vi.mock("@/screens/settings/use-runtime-settings", () => ({
  computeRuntimeUpdates: (current: Record<string, string>, baseline: Record<string, string>) => {
    const updates: Record<string, string> = {};
    for (const key of [
      "DSPY_LM_MODEL",
      "DSPY_DELEGATE_LM_MODEL",
      "DSPY_DELEGATE_LM_SMALL_MODEL",
      "DSPY_LLM_API_KEY",
      "DSPY_LM_API_BASE",
      "DSPY_LM_MAX_TOKENS",
      "MODAL_TOKEN_ID",
      "MODAL_TOKEN_SECRET",
      "SECRET_NAME",
      "VOLUME_NAME",
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
        values: {
          DSPY_LM_MODEL: "openai/gpt-4o-mini",
          DSPY_DELEGATE_LM_MODEL: "openai/gpt-4.1-mini",
          DSPY_DELEGATE_LM_SMALL_MODEL: "openai/gpt-4o-mini",
          DSPY_LM_MAX_TOKENS: "64000",
          DSPY_LM_API_BASE: "https://litellm.example.com/v1",
          DSPY_LLM_API_KEY: "[REDACTED:api-key]",
          MODAL_TOKEN_ID: "modal-id",
          MODAL_TOKEN_SECRET: "modal-secret",
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
        llm: {
          model_set: true,
          api_key_set: true,
          planner_configured: false,
        },
        modal: {
          credentials_available: true,
          secret_name_set: true,
          credentials_from_env: true,
          credentials_from_profile: false,
        },
        tests: {
          modal: null,
          lm: null,
        },
        guidance: ["Run Runtime tests from Settings -> Runtime."],
      },
    },
    saveSettings: { isPending: false, mutate: vi.fn() },
    testModalConnection: {
      isPending: false,
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
    },
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
      modal: { ok: true },
      lm: { ok: true },
    })),
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
    expect(html).toContain("LiteLLM integration");
    expect(html).toContain("Planner LM model");
    expect(html).toContain("Delegate LM model");
    expect(html).toContain("Delegate small LM model");
    expect(html).toContain("Custom API endpoint");
    expect(html).toContain("API key");
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
    expect(html).not.toContain("Planner LM model");
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
