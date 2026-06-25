import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RuntimeStatusPanel } from "@/features/settings/runtime-status-panel";
import type { RuntimeStatusResponse } from "@/lib/rlm-api";

describe("RuntimeStatusPanel", () => {
  it("renders MLflow startup status and tracking link", () => {
    const status = {
      app_env: "local",
      write_enabled: true,
      settings_write_enabled: true,
      profile_write_enabled: true,
      ready: false,
      active_models: {
        planner: "openai/gpt-4.1",
        delegate: "openai/gpt-4.1-mini",
        delegate_small: "openai/gpt-4.1-mini",
      },
      sandbox_provider: "daytona",
      llm: {},
      mlflow: {
        enabled: true,
        tracking_uri: "http://127.0.0.1:5001",
        experiment_name: "fleet-rlm",
        startup_status: "ready",
        startup_error: null,
      },
      daytona: {},
      tests: {},
      guidance: [],
    } satisfies RuntimeStatusResponse;

    const html = renderToStaticMarkup(<RuntimeStatusPanel status={status} />);

    expect(html).toContain("MLflow");
    expect(html).toContain("ready");
    expect(html).toContain("http://127.0.0.1:5001");
  });
});
