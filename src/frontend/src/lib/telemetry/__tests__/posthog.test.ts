import { describe, expect, it } from "vite-plus/test";

import * as posthogConfig from "@/lib/telemetry/posthog";

const HOST = "https://eu.i.posthog.com";

describe("resolvePostHogWebConfig", () => {
  it("uses canonical env key", () => {
    const resolved = posthogConfig.resolvePostHogWebConfig({
      VITE_PUBLIC_POSTHOG_API_KEY: "phc_canonical",
      VITE_PUBLIC_POSTHOG_HOST: HOST,
    });

    expect(resolved).toEqual({
      apiKey: "phc_canonical",
      host: HOST,
      keySource: "canonical_env",
    });
  });

  it("uses project default host when host env is unset", () => {
    const resolved = posthogConfig.resolvePostHogWebConfig({
      VITE_PUBLIC_POSTHOG_API_KEY: "phc_canonical",
    });

    expect(resolved.host).toBe(posthogConfig.PROJECT_POSTHOG_DEFAULT_HOST);
  });

  it("can fall back to project-owned default key when configured", () => {
    const resolved = posthogConfig.resolvePostHogWebConfig(
      {},
      { projectDefaultApiKey: "phc_project_default" },
    );

    expect(resolved).toEqual({
      apiKey: "phc_project_default",
      host: posthogConfig.PROJECT_POSTHOG_DEFAULT_HOST,
      keySource: "project_default",
    });
  });

  it("returns no key when no envs/default are configured", () => {
    const resolved = posthogConfig.resolvePostHogWebConfig({});

    expect(resolved).toEqual({
      apiKey: null,
      host: posthogConfig.PROJECT_POSTHOG_DEFAULT_HOST,
      keySource: "none",
    });
  });
});
