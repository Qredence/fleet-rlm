/**
 * Unit tests for the rlm-api config module.
 *
 * Verifies that `rlmApiConfig.wsUrl` (via `getActiveWsUrl`) is derived
 * correctly from environment variables.
 */
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

async function loadRlmApiConfigModule() {
  vi.resetModules();
  return import("@/lib/rlm-api/config");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("rlmApiConfig — wsUrl derivation", () => {
  // ── explicit VITE_FLEET_WS_URL ─────────────────────────────────────────────
  it("uses VITE_FLEET_WS_URL directly when set", async () => {
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://custom-host:9000/api/v1/ws/chat");
    vi.stubEnv("VITE_FLEET_API_URL", "");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.wsUrl).toBe("ws://custom-host:9000/api/v1/ws/chat");
  });

  // ── derive from VITE_FLEET_API_URL ─────────────────────────────────────────
  it("derives wsUrl from VITE_FLEET_API_URL when WS_URL is absent", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_WS_URL", "");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.wsUrl).toBe("ws://localhost:8000/api/v1/ws/chat");
  });

  it("maps https:// to wss:// when deriving from VITE_FLEET_API_URL", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "https://api.example.com");
    vi.stubEnv("VITE_FLEET_WS_URL", "");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.wsUrl).toBe("wss://api.example.com/api/v1/ws/chat");
  });

  it("always sets the path to /api/v1/ws/chat when deriving from API URL", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000/some/other/path");
    vi.stubEnv("VITE_FLEET_WS_URL", "");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.wsUrl).toBe("ws://localhost:8000/api/v1/ws/chat");
  });

  // ── VITE_FLEET_WS_URL takes precedence ──────────────────────────────────────
  it("WS_URL takes precedence over API_URL", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_WS_URL", "ws://explicit:1234/api/v1/ws/chat");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.wsUrl).toBe("ws://explicit:1234/api/v1/ws/chat");
  });

  // ── other config fields ──────────────────────────────────────────────────────
  it("correctly reads workspace and user IDs from env", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_WS_URL", "");
    vi.stubEnv("VITE_FLEET_WORKSPACE_ID", "my-workspace");
    vi.stubEnv("VITE_FLEET_USER_ID", "my-user");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.workspaceId).toBe("my-workspace");
    expect(rlmApiConfig.userId).toBe("my-user");
  });

  it("falls back to default workspace and user when env vars are absent", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_WS_URL", "");
    vi.stubEnv("VITE_FLEET_WORKSPACE_ID", "");
    vi.stubEnv("VITE_FLEET_USER_ID", "");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.workspaceId).toBe("default");
    expect(rlmApiConfig.userId).toBe("fleetwebapp-user");
  });

  // ── mock mode ─────────────────────────────────────────────────────────────────
  it("sets mockMode to false when VITE_MOCK_MODE is unset", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_WS_URL", "");
    vi.stubEnv("VITE_MOCK_MODE", "");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.mockMode).toBe(false);
  });

  it("sets mockMode to true when VITE_MOCK_MODE=true", async () => {
    vi.stubEnv("VITE_FLEET_API_URL", "http://localhost:8000");
    vi.stubEnv("VITE_FLEET_WS_URL", "");
    vi.stubEnv("VITE_MOCK_MODE", "true");

    const { rlmApiConfig } = await loadRlmApiConfigModule();
    expect(rlmApiConfig.mockMode).toBe(true);
  });
});
