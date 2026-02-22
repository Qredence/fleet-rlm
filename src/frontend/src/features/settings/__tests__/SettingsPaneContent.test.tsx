import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { SettingsPaneContent } from "@/features/settings/SettingsPaneContent";

vi.mock("@/features/settings/useRuntimeSettings", () => ({
  useRuntimeSettings: () => ({
    settingsQuery: {
      data: {
        values: {
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
  it("renders only the grouped v0.4.8 settings surface", () => {
    const html = renderToStaticMarkup(
      <SettingsPaneContent isDark={false} onToggleTheme={vi.fn()} />,
    );

    expect(html).toContain("Theme");
    expect(html).toContain("Anonymous telemetry");
    expect(html).toContain("LiteLLM integration");
    expect(html).toContain("Custom API endpoint");
    expect(html).toContain("API key");

    expect(html).not.toContain("Notifications");
    expect(html).not.toContain("Personalization");
    expect(html).not.toContain("Billing");
    expect(html).not.toContain("Account");
    expect(html).not.toContain("Data &amp; Privacy");
  });
});
