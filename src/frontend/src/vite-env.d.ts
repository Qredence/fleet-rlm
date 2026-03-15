/// <reference types="vite-plus/client" />

interface ImportMetaEnv {
  /** Base URL for the fleet-rlm REST API. */
  readonly VITE_FLEET_API_URL?: string;
  /** Explicit WebSocket URL for fleet-rlm (overrides derived URL). */
  readonly VITE_FLEET_WS_URL?: string;
  /** Workspace identifier sent to fleet-rlm. */
  readonly VITE_FLEET_WORKSPACE_ID?: string;
  /** User identifier sent to fleet-rlm. */
  readonly VITE_FLEET_USER_ID?: string;
  /** Enable trace-level logging ("true"/"false"). */
  readonly VITE_FLEET_TRACE?: string;
  /** Enable mock mode — bypass backend entirely ("true"/"false"). */
  readonly VITE_MOCK_MODE?: string;
  /** Microsoft Entra (Azure AD) application client ID. */
  readonly VITE_ENTRA_CLIENT_ID?: string;
  /** Microsoft Entra authority URL. */
  readonly VITE_ENTRA_AUTHORITY?: string;
  /** Comma-separated OAuth scopes for Entra token requests. */
  readonly VITE_ENTRA_SCOPES?: string;
  /** Redirect path after Entra login/logout. */
  readonly VITE_ENTRA_REDIRECT_PATH?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
