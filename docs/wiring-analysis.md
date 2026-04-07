# Wiring Analysis: `src/fleet_rlm` ↔ `src/frontend`

> How the Python backend and React frontend are connected at build-time and
> runtime.

---

## 1. SPA Asset Serving

The FastAPI app factory in `src/fleet_rlm/api/main.py` resolves the built
frontend via `_resolve_ui_dist_dir()`, which checks two candidate paths in
order:

| Priority | Path | When used |
|----------|------|-----------|
| 1 | `<repo_root>/src/frontend/dist` | Source checkouts (`fleet web` during development) |
| 2 | `src/fleet_rlm/ui/dist` | Packaged/installed distributions |

The first existing directory wins. If neither exists, the SPA is not mounted
and the API runs headless.

When a `ui_dir` is found, `_mount_spa(app, ui_dir)` does three things:

1. **`/assets`** — mounts `ui_dir/assets` as a `StaticFiles` directory (hashed
   JS/CSS bundles produced by Vite).
2. **`/branding`** — mounts `ui_dir/branding` as a `StaticFiles` directory
   (logos, favicons).
3. **`/{full_path:path}`** — a catch-all GET route that returns
   `ui_dir/index.html` for every non-API path, enabling client-side routing.

Because `_mount_spa` is called **after** `_register_api_routes`, the API
routes (`/health`, `/api/v1/*`) take precedence over the SPA catch-all.

---

## 2. HTTP API Contract

### Backend route registration

`_register_api_routes()` in `main.py` wires the following routers:

```
app
├── health.router          →  /health, /ready
└── APIRouter(prefix="/api/v1")
    ├── auth.router        →  /api/v1/auth/me          (GET)
    ├── ws.router          →  /api/v1/ws/execution          (WebSocket)
    │                         /api/v1/ws/execution      (WebSocket)
    ├── sessions.router    →  /api/v1/sessions/state    (GET)
    ├── runtime.router     →  /api/v1/runtime/*         (GET/POST)
    └── traces.router      →  /api/v1/traces/feedback   (POST)
```

### Frontend OpenAPI client

The frontend consumes these endpoints through a generated TypeScript client at
`src/frontend/src/lib/rlm-api/generated/openapi.ts`, produced from the
canonical `openapi.yaml` (see §6). The hand-written adapter layer lives in
`src/frontend/src/lib/rlm-api/`:

| Frontend module | Backend endpoint(s) |
|-----------------|---------------------|
| `auth.ts` | `GET /api/v1/auth/me` |
| `runtime.ts` | `GET/POST /api/v1/runtime/*` |
| `wsClient.ts` | `WS /api/v1/ws/execution`, `WS /api/v1/ws/execution` |
| `config.ts` | URL derivation for all of the above |

REST calls use the standard `fetch` API with the base URL from
`rlmApiConfig.baseUrl`.

---

## 3. WebSocket Contract

### Endpoints

| Path | Backend handler | Purpose |
|------|-----------------|---------|
| `/api/v1/ws/execution` | `chat_streaming()` in `routers/ws/endpoint.py` | Bidirectional chat streaming |
| `/api/v1/ws/execution` | `execution_stream()` in `routers/ws/endpoint.py` | Read-only artifact/execution event stream |

### Backend flow (`/ws/execution`, chat mode)

1. Authenticate the WebSocket connection (`_authenticate_websocket`).
2. Accept the socket and prepare the chat runtime (`runtime_services/chat_runtime.py`).
3. Enter a message loop: receive JSON → parse into `WsChatMessage` →
   dispatch to `_chat_message_loop` → stream response frames back.
4. Build the canonical Daytona-backed agent context
   (`runtime_services/chat_runtime.py`).

### Backend flow (`/ws/execution`)

1. Authenticate, accept, and subscribe to the `ExecutionEventEmitter`.
2. Hold the socket open; the emitter pushes artifact frames as they arrive
   from the chat runtime.

### Frontend consumers

- **`stores/chatStore.ts`** — Zustand store that owns `streamMessage()`. It
  calls `streamChatOverWs()` from `wsClient.ts`, which opens a reconnecting
  WebSocket to `rlmApiConfig.wsUrl` (`/api/v1/ws/execution`).
- **`features/rlm-workspace/useBackendChatRuntime.ts`** — React hook that
  orchestrates submit → `streamMessage` → frame callbacks → UI state
  transitions (phase, typing indicator, artifact steps).
- **`wsClient.ts: subscribeToExecutionStream()`** — opens a separate
  reconnecting WebSocket to `rlmApiConfig.wsExecutionUrl`
  (`/api/v1/ws/execution`) with the `session_id` as a query parameter.

### Message protocol

- **Client → Server**: schema-validated `WSMessage` frames covering `message`,
  `command`, and `cancel`.
- **Server → Client**: JSON envelopes emitted by the chat and execution stream
  helpers, including `event`, `command_result`, `error`, and execution stream
  frames.

---

## 4. Runtime Alignment

### The maintained runtime

The public workbench runtime is Daytona-only. There is no runtime selector in
the websocket contract anymore; the backend always builds the shared DSPy
ReAct + `dspy.RLM` agent with the Daytona interpreter/backend.

### Frontend → Backend flow

1. **Composer submit** — the frontend opens `/api/v1/ws/execution`.
2. **First message frame** — `chatStore.ts` sends a `WsMessageRequest`
   containing the user content plus any Daytona workspace controls:
   `repo_url`, `repo_ref`, `context_paths`, and `batch_concurrency`.
3. **Backend runtime prep** — `routers/ws/endpoint.py` authenticates the
   socket, prepares planner/delegate models, and builds the canonical chat
   agent through the shared runtime factory path.
4. **Turn prep** — `routers/ws/types.py` and `routers/ws/turn_setup.py`
   normalize Daytona workspace options and apply them through the interpreter's
   native workspace/session API.
5. **Execution stream** — the same `/api/v1/ws/execution` socket carries live
   chat events and workbench execution summaries.

### UI behavior

- The workbench UI assumes the Daytona-backed runtime by default.
- Run/workspace panels should react to execution metadata and session state,
  not to a user-facing runtime toggle.

---

## 5. Auth Wiring

### Backend auth modes

`ServerRuntimeConfig.auth_mode` (from `AUTH_MODE` env var) selects the
provider via `build_auth_provider()` in `api/auth/factory.py`:

| Mode | Provider | Mechanism |
|------|----------|-----------|
| `dev` | `DevAuthProvider` | Debug headers (`X-Fleet-User`, etc.) or HS256 JWT bearer tokens |
| `entra` | `EntraAuthProvider` | Microsoft Entra ID (Azure AD) RS256 JWT validation via JWKS |

Both providers implement `AuthProvider` and are used for HTTP requests
(via `HTTPIdentityDep`) and WebSocket upgrades (via
`_authenticate_websocket`).

### Frontend auth flow

1. **Entra configuration** — `src/frontend/src/lib/auth/entra.ts` reads:
   - `VITE_ENTRA_CLIENT_ID` — SPA client registration in Entra.
   - `VITE_ENTRA_AUTHORITY` — defaults to
     `https://login.microsoftonline.com/organizations`.
   - `VITE_ENTRA_SCOPES` — e.g. `api://backend-client-id/access_as_user`.
   - `VITE_ENTRA_REDIRECT_PATH` — defaults to `/login`.
2. **MSAL bootstrap** — `getMsalClient()` lazily creates a
   `PublicClientApplication` (from `@azure/msal-browser`), calls
   `initialize()` and `handleRedirectPromise()`.
3. **Token acquisition** — `initializeEntraSession()` attempts silent token
   acquisition for the active account. `loginWithEntra()` triggers a popup
   flow.
4. **Token storage** — Acquired access tokens are stored via
   `setAccessToken()` (in `lib/auth/tokenStore`).
5. **API calls** — The stored token is attached as a `Bearer` header on
   `GET /api/v1/auth/me`, which the backend validates through
   `EntraAuthProvider` and returns `AuthMeResponse` with tenant/user claims.

### Dev mode

When Entra env vars are absent (`isEntraAuthConfigured()` returns `false`),
the frontend skips MSAL entirely. The backend's `DevAuthProvider` accepts
debug headers or a simple HS256 token, enabling local development without
Azure infrastructure.

---

## 6. OpenAPI Sync Pipeline

The canonical API contract lives at `openapi.yaml` in the repo root. The
frontend keeps a local copy and a generated TypeScript client in sync via
two npm scripts defined in `src/frontend/package.json`:

When backend request/response shapes or OpenAPI-facing route/schema
descriptions change, regenerate the root spec first:

```bash
# from repo root
uv run python scripts/openapi_tools.py generate
```

### `pnpm run api:sync`

Runs two sub-steps:

1. **`api:sync-spec`** — Copies `../../openapi.yaml` (or
   `$OPENAPI_SPEC_PATH`) into `src/frontend/openapi/fleet-rlm.openapi.yaml`.
2. **`api:types`** — Runs `openapi-typescript` against the local copy to
   regenerate `src/frontend/src/lib/rlm-api/generated/openapi.ts`.

### `pnpm run api:check`

Runs `api:sync` and then asserts that neither the spec copy nor the generated
types changed during the sync. The current implementation snapshots file
contents before and after `api:sync` and fails if either tracked generated
file differs. This is used in CI to catch drift between the backend contract
and the frontend client.

### Workflow

```
openapi.yaml  ──(api:sync-spec)──►  openapi/fleet-rlm.openapi.yaml
                                           │
                                    (api:types / openapi-typescript)
                                           │
                                           ▼
                          src/lib/rlm-api/generated/openapi.ts
```

---

## 7. Environment Variable Bridge

### Core variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VITE_FLEET_API_URL` | `http://localhost:8000` | Base URL for REST API calls |
| `VITE_FLEET_WS_URL` | *(derived)* | Explicit WebSocket URL override |
| `VITE_FLEET_TRACE` | `true` | Include trace/reasoning data from backend |
| `VITE_MOCK_MODE` | `false` | Run frontend with mock data (no backend) |

### WebSocket URL derivation (`config.ts`)

The `getActiveWsUrl(path)` function resolves WebSocket URLs with this
priority:

1. **`VITE_FLEET_WS_URL` is set** — use it directly for `/ws/execution`.
2. **`VITE_FLEET_API_URL` is set** — derive by swapping the protocol
   (`http:` → `ws:`, `https:` → `wss:`) and setting the pathname to the
   target path.
3. **Neither is set (browser context)** — derive from `window.location`
   using the current origin's protocol and host.

This produces two resolved URLs exported as `rlmApiConfig.wsUrl` and
`rlmApiConfig.wsExecutionUrl`.

### Entra auth variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VITE_ENTRA_CLIENT_ID` | *(none)* | Entra SPA app registration client ID |
| `VITE_ENTRA_AUTHORITY` | `https://login.microsoftonline.com/organizations` | Entra authority URL |
| `VITE_ENTRA_SCOPES` | *(none)* | Comma-separated OAuth scopes |
| `VITE_ENTRA_REDIRECT_PATH` | `/login` | Post-auth redirect path |

When `VITE_ENTRA_CLIENT_ID` and `VITE_ENTRA_SCOPES` are both set,
`isEntraAuthConfigured()` returns `true` and the MSAL flow activates.

### Backend-side counterparts

The backend reads its own env vars (`AUTH_MODE`, `ENTRA_JWKS_URL`,
`ENTRA_ISSUER_TEMPLATE`, `ENTRA_AUDIENCE`, etc.) via `ServerRuntimeConfig`.
The frontend `VITE_ENTRA_*` vars configure the SPA-side MSAL client, while
the backend `ENTRA_*` vars configure server-side JWT validation. Both sides
must reference the same Entra app registration for tokens to validate
correctly.
