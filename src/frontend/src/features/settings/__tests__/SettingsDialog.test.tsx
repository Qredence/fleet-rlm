import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";

import { SettingsDialog } from "@/features/settings/SettingsDialog";

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock("@/hooks/useNavigation", () => ({
  useNavigation: () => ({
    isDark: false,
    toggleTheme: vi.fn(),
  }),
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/features/settings/useRuntimeSettings", () => ({
  computeRuntimeUpdates: () => ({}),
  computeLmRuntimeUpdates: () => ({}),
  useRuntimeSettings: () => ({
    settingsQuery: {
      data: {
        values: {
          DSPY_LM_MODEL: "openai/gpt-4o-mini",
          DSPY_DELEGATE_LM_MODEL: "openai/gpt-4.1-mini",
          DSPY_DELEGATE_LM_SMALL_MODEL: "openai/gpt-4o-mini",
          DSPY_LLM_API_KEY: "sk-test",
          DSPY_LM_API_BASE: "https://litellm.example.com/v1",
          DSPY_LM_MAX_TOKENS: "64000",
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
        llm: { model_set: true, api_key_set: true, planner_configured: true },
        modal: {
          credentials_available: true,
          credentials_from_env: true,
          credentials_from_profile: false,
          secret_name_set: true,
        },
        tests: { modal: null, lm: null },
        guidance: [],
      },
    },
    saveSettings: { isPending: false, mutate: vi.fn() },
    testModalConnection: {
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

describe("SettingsDialog", () => {
  it("activates the runtime section when initialSection is runtime", () => {
    const html = renderToStaticMarkup(
      <SettingsDialog open onOpenChange={vi.fn()} initialSection="runtime" />,
    );

    expect(html).toContain("Runtime");
    expect(html).toContain("Runtime Status");
    expect(html).toContain("Test Credentials + Connection");
    expect(html).not.toContain("Control theme and interface appearance.");
  });
});
