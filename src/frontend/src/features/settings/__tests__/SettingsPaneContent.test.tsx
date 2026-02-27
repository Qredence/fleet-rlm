import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { SettingsPaneContent } from "@/features/settings/SettingsPaneContent";

vi.mock("@/features/settings/useRuntimeSettings", () => ({
  computeLmRuntimeUpdates: (
    current: Record<string, string>,
    baseline: Record<string, string>,
  ) => {
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
          DSPY_LM_API_BASE: "https://litellm.example.com/v1",
          DSPY_LLM_API_KEY: "sk-test",
        },
      },
    },
    statusQuery: {
      data: {
        app_env: "local",
        write_enabled: true,
      },
    },
    saveSettings: { isPending: false, mutate: vi.fn() },
  }),
}));

describe("SettingsPaneContent", () => {
  it("renders the grouped v0.4.8 settings surface by default", () => {
    const html = renderToStaticMarkup(
      <SettingsPaneContent isDark={false} onToggleTheme={vi.fn()} />,
    );

    expect(html).toContain("Theme");
    expect(html).toContain("Anonymous telemetry");
    expect(html).toContain("LiteLLM integration");
    expect(html).toContain("Planner LM model");
    expect(html).toContain("Delegate LM model");
    expect(html).toContain("Delegate small LM model");
    expect(html).toContain("Custom API endpoint");
    expect(html).toContain("API key");

    expect(html).not.toContain("Notifications");
    expect(html).not.toContain("Personalization");
    expect(html).not.toContain("Billing");
    expect(html).not.toContain("Account");
    expect(html).not.toContain("Data &amp; Privacy");
  });

  it("renders telemetry-only content when section is telemetry", () => {
    const html = renderToStaticMarkup(
      <SettingsPaneContent
        isDark={false}
        onToggleTheme={vi.fn()}
        section="telemetry"
      />,
    );

    expect(html).toContain("Anonymous telemetry");
    expect(html).toContain("Telemetry scope");
    expect(html).not.toContain("Theme");
    expect(html).not.toContain("Planner LM model");
  });
});
