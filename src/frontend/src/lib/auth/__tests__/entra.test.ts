import { afterEach, describe, expect, it, vi } from "vite-plus/test";

async function loadEntraModule() {
  vi.resetModules();
  return import("@/lib/auth/entra");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("entraAuthConfig", () => {
  it("defaults authority to organizations and redirects to /login", async () => {
    vi.stubEnv("VITE_ENTRA_CLIENT_ID", "frontend-client-id");
    vi.stubEnv("VITE_ENTRA_SCOPES", "api://backend-client-id/access_as_user");

    const { entraAuthConfig, isEntraAuthConfigured } = await loadEntraModule();

    expect(entraAuthConfig.authority).toBe(
      "https://login.microsoftonline.com/organizations",
    );
    expect(entraAuthConfig.redirectPath).toBe("/login");
    expect(entraAuthConfig.scopes).toEqual([
      "api://backend-client-id/access_as_user",
    ]);
    expect(isEntraAuthConfigured()).toBe(true);
  });

  it("allows an explicit authority override", async () => {
    vi.stubEnv("VITE_ENTRA_CLIENT_ID", "frontend-client-id");
    vi.stubEnv("VITE_ENTRA_SCOPES", "api://backend-client-id/access_as_user");
    vi.stubEnv(
      "VITE_ENTRA_AUTHORITY",
      "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555",
    );

    const { entraAuthConfig } = await loadEntraModule();

    expect(entraAuthConfig.authority).toBe(
      "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555",
    );
  });
});
