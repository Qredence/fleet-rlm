# Deploying the API Server

This guide covers production deployment patterns for the Fleet-RLM FastAPI server, including environment configuration, authentication, database setup, and health monitoring.

## Quick Start

Start the server locally:

```bash
# Local development (default: 127.0.0.1:8000)
uv run fleet-rlm serve-api

# Production bind (all interfaces)
uv run fleet-rlm serve-api --host 0.0.0.0 --port 8000
```

For the full Web UI experience:

```bash
uv run fleet web
```

This starts both the API server and serves frontend static assets.

## Core Endpoint Groups

| Endpoint | Purpose |
|----------|---------|
| `/health` | Liveness probe (always returns `ok: true`) |
| `/ready` | Readiness probe (checks planner, database) |
| `/api/v1/auth/me` | Identity bootstrap for the SPA |
| `/api/v1/sessions/state` | Lightweight in-memory session summaries |
| `/api/v1/runtime/*` | Runtime settings, diagnostics, and volume access |
| `POST /api/v1/traces/feedback` | Trace correctness/expectation feedback |
| `/api/v1/ws/execution` | WebSocket runtime |

## Production Environment Variables

### Required Configuration

| Variable | Description | Example |
|----------|-------------|---------|
| `APP_ENV` | Runtime environment (`local`, `staging`, `production`) | `production` |
| `AUTH_MODE` | Authentication mode (`neon`, `entra`, or `dev`) | `neon` |
| `AUTH_REQUIRED` | Enforce authentication on protected routes | `true` |
| `DATABASE_URL` | Neon PostgreSQL connection string | `postgresql://...` |
| `DSPY_LM_MODEL` | LLM model identifier for the planner | `openai/gpt-4o` |
| `DSPY_LLM_API_KEY` | API key for the LLM provider | `sk-...` |

### Environment-Specific Defaults

The server applies different defaults based on `APP_ENV`:

| Setting | `local` | `staging`/`production` |
|---------|---------|------------------------|
| `AUTH_REQUIRED` | `false` | `true` (required) |
| `DATABASE_REQUIRED` | `false` | `true` (required) |
| `ALLOW_DEBUG_AUTH` | `true` | `false` (required) |
| `CORS_ALLOWED_ORIGINS` | `["*"]` | `[]` (must be explicit) |

### Full Production Environment Example

```bash
# Environment
APP_ENV=production

# LLM Configuration
DSPY_LM_MODEL=openai/gpt-4o
DSPY_LLM_API_KEY=sk-your-api-key

# Database (Neon PostgreSQL)
DATABASE_URL=postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
DATABASE_REQUIRED=true

# Authentication (Neon Auth)
AUTH_MODE=neon
AUTH_REQUIRED=true

# Neon Auth Configuration
NEON_AUTH_URL=https://<your-neon-auth-project>.neon.tech
NEON_TENANT_CLAIM=tenant_id

# CORS (explicit origins only)
CORS_ALLOWED_ORIGINS=https://app.yourdomain.com,https://admin.yourdomain.com

# Optional: Delegate model for sub-agent calls
DSPY_DELEGATE_LM_MODEL=openai/gpt-4o-mini
```

## AUTH_MODE=entra Configuration

Microsoft Entra ID (formerly Azure AD) remains a supported backend auth mode
for deployments that already issue Entra access tokens. The current frontend
product path uses Neon Auth UI; do not assume a handwritten Entra SPA
implementation is present in `src/frontend`.

### Prerequisites

1. **API App Registration** in Microsoft Entra:
   - Create an app registration in your Entra tenant
   - Note the Application (client) ID for `ENTRA_AUDIENCE`
   - Expose an API scope (e.g., `api://<client-id>/access_as_user`)

2. **Client App Registration**:
   - Create a separate app registration for the frontend
   - Add the API scope as a delegated permission
   - Configure redirect URIs

### Required Environment Variables

```bash
AUTH_MODE=entra
AUTH_REQUIRED=true

# JWKS endpoint for token validation
ENTRA_JWKS_URL=https://login.microsoftonline.com/common/discovery/v2.0/keys

# Your API app's client ID (the audience in tokens)
ENTRA_AUDIENCE=api://your-api-client-id

# Issuer template with tenant placeholder
ENTRA_ISSUER_TEMPLATE=https://login.microsoftonline.com/{tenantid}/v2.0
```

### Token Validation Flow

1. Client obtains a valid Entra access token from the configured identity client
2. Token includes `tid` (tenant ID) and `oid` (user object ID)
3. Server validates:
   - Signature against JWKS
   - Audience matches `ENTRA_AUDIENCE`
   - Issuer matches template with tenant ID substituted
   - Required claims: `exp`, `iat`, `tid`

### Tenant Admission

Entra mode uses the database for tenant allowlisting:

- **Unknown tenants** → `403 Forbidden` (tenant not allowlisted)
- **Suspended tenants** → `403 Forbidden` (tenant suspended)
- **Known tenants** → User upserted, session allowed

Tenant onboarding is an administrative action (not automatic on first login).

### WebSocket Authentication

Native browser WebSockets cannot attach arbitrary `Authorization` headers. Use
the HTTP ticket exchange before opening runtime streams:

```bash
curl -X POST \
  -H "Authorization: Bearer <auth-token>" \
  https://your-server/api/v1/auth/ws-ticket
```

Then connect with the opaque one-time ticket:

```text
wss://your-server/api/v1/ws/execution?ticket=<opaque-ticket>
```

Legacy `access_token` query authentication remains a compatibility path only
where explicitly enabled. `AUTH_MODE=neon` rejects raw JWT query parameters.

## Database Connection Setup

Fleet-RLM uses Neon PostgreSQL for persistence with Row-Level Security (RLS) for tenant isolation.

### Runtime Connection String Format

```bash
DATABASE_URL=postgresql://<user>:<password>@<host>/<database>?sslmode=require
```

For Neon runtime traffic, use the pooled endpoint:

```bash
DATABASE_URL=postgresql://neondb_owner:password@ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
```

### Admin / Migration Connection String Format

For Alembic, schema management, and direct debug/admin tasks, use the non-pooler endpoint:

```bash
DATABASE_ADMIN_URL=postgresql://neondb_owner:password@ep-cool-darkness-123456.us-east-2.aws.neon.tech/neondb?sslmode=require
```

### Requirements

- **SSL required**: Always use `sslmode=require` or higher
- **Runtime**: Use the pooled endpoint in `DATABASE_URL`
- **Migrations/admin**: Use the direct non-pooler endpoint in `DATABASE_ADMIN_URL`
- **Migrations**: Run with Alembic (see [Database Architecture](../reference/database.md))

### Upgrading: BYOK profiles and Row-Level Security

Migration `d31f6d7a8c21_scope_llm_profiles_to_users` scopes `llm_provider_profiles` and
`llm_role_bindings` to `(tenant_id, user_id)` and enables + forces Row-Level Security on both
tables. The RLS policies require `tenant_id`/`user_id` to match the per-transaction session
values set by the server (`app.tenant_id`, `app.user_id`, `app.workspace_id`).

- **Hosted first-deploys** have no prior rows and are unaffected.
- **Upgrading a populated database** (e.g. a local-dev DB with existing profiles): rows created
  before this migration have `NULL` `tenant_id`/`user_id`. Under forced RLS they never match the
  policy, so they become **invisible to every role** — including the table owner. They are not
  deleted, only hidden. Re-create profiles via the Settings UI, or re-claim existing rows by
  assigning them to a specific tenant+user (run as a `BYPASSRLS` role such as a superuser):

  ```sql
  UPDATE llm_provider_profiles
     SET tenant_id = '<tenant-uuid>', user_id = '<user-uuid>'
   WHERE tenant_id IS NULL AND user_id IS NULL;
  UPDATE llm_role_bindings
     SET tenant_id = '<tenant-uuid>', user_id = '<user-uuid>'
   WHERE tenant_id IS NULL AND user_id IS NULL;
  ```

> **Do not remove the GUC-setting code.** RLS policies key on
> `current_setting('app.user_id')` (set per transaction via `set_config(..., true)` in
> `PostgresLlmProfileStore._set_request_context`), not on Neon's gateway-set
> `auth.user_id()`. This is deliberate so the same policies work across `dev`, `entra`, and
> `neon` auth modes. The transaction-local `set_config(..., true)` argument is what makes this
> safe across pooled connections. Removing the GUC-setting step would break isolation in
> non-Neon auth modes.

### Connection Pooling

The server uses SQLAlchemy async with connection pooling:

```python
# Default pool settings in engine.py
pool_pre_ping=True
```

### Database Health Check

The `/ready` endpoint reports database status:

```bash
curl -sS https://your-server/ready | jq
```

Response:

```json
{
  "ready": true,
  "planner": "ready",
  "database": "ready",
  "database_required": true,
  "sandbox_provider": "daytona"
}
```

Database states:

| Status | Meaning |
|--------|---------|
| `ready` | Database connected and operational |
| `missing` | Database required but not configured |
| `disabled` | Database not required |

## Health Check Endpoints

### `/health` — Liveness Probe

Always returns `ok: true` with the server version. Use for Kubernetes liveness probes.

```bash
curl -sS https://your-server/health
```

Response:

```json
{
  "ok": true,
  "version": "0.6.2"
}
```

### `/ready` — Readiness Probe

Checks planner configuration and database connectivity. Use for Kubernetes readiness probes.

```bash
curl -sS https://your-server/ready
```

Response fields:

| Field | Description |
|-------|-------------|
| `ready` | Overall readiness (planner + optional database) |
| `planner` | `"ready"` or `"missing"` |
| `planner_configured` | Boolean planner status |
| `database` | `"ready"`, `"missing"`, `"disabled"`, or `"degraded"` |
| `database_required` | Whether database is required |
| `sandbox_provider` | Active sandbox provider (`daytona`) |

### Kubernetes Probes Example

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 5
```

## Deploying to FastAPI Cloud

[FastAPI Cloud](https://fastapicloud.com) is the managed platform from the FastAPI team (currently in private beta). It deploys Python apps via a single `fastapi deploy` command and offers a first-class **Neon** Postgres integration — the same provider Fleet-RLM uses locally.

This guide assumes you have a FastAPI Cloud account and have run `fastapi login` once to authenticate the CLI.

### Prerequisites

1. The repository is cleanly committed (FastAPI Cloud uploads your working tree).
2. `pyproject.toml` already declares the app entrypoint (present by default):

   ```toml
   [tool.fastapi]
   entrypoint = "fleet_rlm.api.main:app"
   ```

3. `fastapi[standard]` is in your project dependencies (present by default).
4. `uv.lock` is committed so the build environment is reproducible.

### 1. Rotate secrets before deploy

Any value that previously lived in a local `.env` must be rotated before being injected into a shared cloud environment. Minimum rotation checklist:

- `DSPY_LLM_API_KEY` / `DSPY_LM_API_KEY` (LLM provider key)
- `DAYTONA_API_KEY`
- `POSTHOG_API_KEY`
- `MLFLOW_CRYPTO_KEK_PASSPHRASE` (if MLflow is enabled)
- `DEV_JWT_SECRET` (only if you deploy with `AUTH_MODE=dev`)
- Neon `DATABASE_URL` / `DATABASE_ADMIN_URL` — see note below

If you plan to use the FastAPI Cloud Neon integration, skip rotating Neon credentials here: the platform provisions a new `DATABASE_URL` and injects it at deploy time.

Daytona is the only supported runtime substrate for sandbox execution. Do not configure retired alternative-provider secrets for new deploys.

### 2. Wire the Neon integration

In the FastAPI Cloud dashboard, link the Neon add-on and point it at the **same Neon project/branch** you use locally. The integration automatically injects `DATABASE_URL` (pooled endpoint) into your deploy. Use the pooled endpoint for runtime connections; reserve the direct endpoint (`DATABASE_ADMIN_URL`) for migrations.

### 3. Set environment variables

Configure these in the FastAPI Cloud dashboard before deploying. This is the minimum API-only config for a smoke test:

| Variable | Value | Notes |
|---|---|---|
| `APP_ENV` | `production` | Enables production guardrails in `validate_startup_or_raise` |
| `AUTH_MODE` | `neon`, `entra`, or `dev` | `neon` requires `NEON_AUTH_URL` and repository admission; `entra` requires the Entra variables below |
| `AUTH_REQUIRED` | `true` | Required in staging/production |
| `DATABASE_REQUIRED` | `true` | Fail fast if Neon isn't reachable at startup; required for `AUTH_MODE=neon` admission |
| `DATABASE_URL` | (injected by Neon add-on) | Pooled endpoint |
| `FLEET_SECRET_ENCRYPTION_KEY` | (secret) | Fernet key used to encrypt hosted per-user BYOK provider credentials |
| `DSPY_LM_MODEL` | e.g. `openai/gpt-4o` | LiteLLM model identifier with provider prefix |
| `DSPY_DELEGATE_LM_MODEL` | e.g. `openai/gpt-4o-mini` | Optional but recommended |
| `DSPY_LLM_API_KEY` | (secret) | LLM provider key |
| `CORS_ALLOWED_ORIGINS` | `https://<your-frontend-host>` | Comma-separated; `*` is rejected in production |
| `FLEET_RLM_SERVE_UI` | `false` | API-only deploy; frontend is hosted separately |
| `MLFLOW_ENABLED` | `false` (or `true` with a remote `MLFLOW_TRACKING_URI`) | Local auto-start is disabled automatically when `APP_ENV=production` |
| `MLFLOW_AUTO_START` | `false` | Belt-and-braces; prevents any local subprocess start |
| `POSTHOG_ENABLED` | `true` or `false` | |
| `POSTHOG_API_KEY` | (secret) | Required when `POSTHOG_ENABLED=true` |
| `DAYTONA_API_KEY` | (secret) | Required if sandbox execution is used |
| `DAYTONA_API_URL` | (optional override) | Defaults to Daytona's managed endpoint |

Generate `FLEET_SECRET_ENCRYPTION_KEY` with:

```bash
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

In hosted Neon mode, browser-managed LLM provider profiles are per-user BYOK
records stored in Neon with tenant/user RLS and encrypted at rest with this key.
The hosted app never imports server `DSPY_*` secrets into a user profile and
never writes user profile secrets back into `.env`.

Set `FLEET_SECRET_ENCRYPTION_KEY`, `DATABASE_URL`, `NEON_AUTH_URL`, provider
fallback keys, and Daytona credentials as FastAPI Cloud environment variables;
use `fastapi cloud env set --secret <KEY> <VALUE>` for sensitive values so they
are encrypted by the platform and not committed to this public repository. Neon
Auth itself remains deployment-owned: users bring LLM provider keys, not their
own Neon project or auth endpoint.

If `AUTH_MODE=entra`, also set:

- `ENTRA_JWKS_URL`
- `ENTRA_AUDIENCE`
- `ENTRA_ISSUER_TEMPLATE` (must contain `{tenantid}`)

When changing a locked FastAPI Cloud environment variable from the CLI, delete the old value before
setting the replacement:

```bash
# from repo root
uv run fastapi cloud env delete <VAR> -y
```

### 4. Pre-flight locally

Run the included preflight target to catch problems before the cloud build:

```bash
make cloud-preflight
```

This confirms the `fastapi` CLI is in the locked env, the app imports cleanly, and all routes enumerate.

### 5. Deploy

```bash
fastapi deploy
```

FastAPI Cloud reads `[tool.fastapi].entrypoint`, installs from `pyproject.toml` + `uv.lock`, and serves the app at a generated URL.
Use `--no-wait` for operational deploys where a separate watcher will follow status.

### 6. Verify the deployment

```bash
curl https://<assigned-host>/health
# => {"ok": true, "version": "0.6.2"}

curl https://<assigned-host>/ready
# => {"ready": true, "planner": "ready", "database": "ready", ...}

curl https://<assigned-host>/docs
# => Swagger UI HTML
```

Scaling note: FastAPI Cloud scales to zero. The first request after idling will pay startup cost — `/health` responds immediately (the LLM warmup is scheduled as a background task), but `/ready` may briefly return `false` while the planner LM initializes.

### 7. Point your frontend at the cloud API

Because `FLEET_RLM_SERVE_UI=false`, the cloud box serves JSON at `/` and does not ship the React SPA. Deploy the frontend separately (Vercel / Netlify / Cloudflare Pages) and:

1. Set `VITE_FLEET_API_URL=https://<assigned-host>` in the frontend's build environment.
2. Add the frontend's public origin(s) to `CORS_ALLOWED_ORIGINS` on the API (this triggers a redeploy).

### Troubleshooting

- **`/` returns 503 instead of the JSON banner** — `FLEET_RLM_SERVE_UI` is probably `true` (the default in local). Set it to `false` for API-only cloud deploys.
- **Startup fails with `AUTH_REQUIRED must be true...`** — `validate_startup_or_raise` rejects insecure production configs. Set `AUTH_REQUIRED=true` and verify CORS does not contain `*`.
- **`/ready` returns `database: "missing"` or `database: "degraded"`** — Neon integration either isn't linked or the pooled endpoint is unreachable. Check the dashboard and re-run `fastapi deploy`.
- **MLflow still trying to start locally** — set both `MLFLOW_ENABLED=false` and `MLFLOW_AUTO_START=false`, or point `MLFLOW_TRACKING_URI` at a remote server.

## Deployment Examples

### Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install dependencies
RUN uv sync --frozen --no-dev

# Expose port
EXPOSE 8000

# Run server
CMD ["uv", "run", "fleet-rlm", "serve-api", "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose

```yaml
version: "3.8"

services:
  fleet-rlm:
    build: .
    ports:
      - "8000:8000"
    environment:
      - APP_ENV=production
      - AUTH_MODE=neon
      - AUTH_REQUIRED=true
      - DATABASE_URL=${DATABASE_URL}
      - NEON_AUTH_URL=${NEON_AUTH_URL}
      - NEON_TENANT_CLAIM=${NEON_TENANT_CLAIM}
      - DSPY_LM_MODEL=${DSPY_LM_MODEL}
      - DSPY_LLM_API_KEY=${DSPY_LLM_API_KEY}
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped
```

### Azure Container Apps

```bash
az containerapp create \
  --name fleet-rlm \
  --resource-group your-rg \
  --environment your-container-env \
  --image your-registry.azurecr.io/fleet-rlm:latest \
  --target-port 8000 \
  --ingress external \
  --env-vars \
    APP_ENV=production \
    AUTH_MODE=neon \
    AUTH_REQUIRED=true \
    DATABASE_URL=secretref:database-url \
    DATABASE_ADMIN_URL=secretref:database-admin-url \
    NEON_AUTH_URL=secretref:neon-auth-url \
    NEON_TENANT_CLAIM=tenant_id \
    DSPY_LM_MODEL=secretref:dspy-model \
    DSPY_LLM_API_KEY=secretref:dspy-api-key
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fleet-rlm
spec:
  replicas: 2
  selector:
    matchLabels:
      app: fleet-rlm
  template:
    metadata:
      labels:
        app: fleet-rlm
    spec:
      containers:
        - name: fleet-rlm
          image: your-registry/fleet-rlm:latest
          ports:
            - containerPort: 8000
          env:
            - name: APP_ENV
              value: "production"
            - name: AUTH_MODE
              value: "neon"
            - name: AUTH_REQUIRED
              value: "true"
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: fleet-secrets
                  key: database-url
            - name: NEON_AUTH_URL
              valueFrom:
                secretKeyRef:
                  name: fleet-secrets
                  key: neon-auth-url
            - name: NEON_TENANT_CLAIM
              value: "tenant_id"
            - name: DSPY_LM_MODEL
              valueFrom:
                secretKeyRef:
                  name: fleet-secrets
                  key: dspy-model
            - name: DSPY_LLM_API_KEY
              valueFrom:
                secretKeyRef:
                  name: fleet-secrets
                  key: dspy-api-key
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /ready
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 5
          resources:
            requests:
              memory: "512Mi"
              cpu: "250m"
            limits:
              memory: "1Gi"
              cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: fleet-rlm
spec:
  selector:
    app: fleet-rlm
  ports:
    - port: 80
      targetPort: 8000
  type: ClusterIP
```

## Runtime Configuration

Override runtime settings via Hydra syntax:

```bash
uv run fleet-rlm serve-api \
  interpreter.async_execute=true \
  agent.guardrail_mode=warn \
  rlm_settings.max_iters=8
```

Common overrides:

| Override | Description |
|----------|-------------|
| `interpreter.async_execute=true` | Enable async sandbox execution |
| `agent.guardrail_mode=warn` | Warn on guardrail violations |
| `agent.guardrail_mode=strict` | Block on guardrail violations |
| `rlm_settings.max_iters=8` | Limit ReAct iterations |

## Security Guardrails

The server enforces strict validation in `staging` and `production` environments:

### Startup Validation

The server will **fail to start** if:

1. `AUTH_REQUIRED=false` in staging/production
2. `ALLOW_DEBUG_AUTH=true` in staging/production
3. `CORS_ALLOWED_ORIGINS` contains `*` in staging/production
4. `DEV_JWT_SECRET=change-me` with `AUTH_MODE=dev` in staging/production

### Auth Mode Validation

The server will **fail to start** if:

1. `AUTH_REQUIRED=false` with `AUTH_MODE=neon` or `AUTH_MODE=entra`
2. `DATABASE_REQUIRED=false` with `AUTH_MODE=neon` or `AUTH_MODE=entra`
3. `NEON_AUTH_URL` not configured with `AUTH_MODE=neon`
4. `ENTRA_JWKS_URL` or `ENTRA_AUDIENCE` not configured with `AUTH_MODE=entra`
5. `ENTRA_ISSUER_TEMPLATE` missing `{tenantid}` placeholder with `AUTH_MODE=entra`

### Debug Mode

Never enable debug features in production:

```bash
# FORBIDDEN in staging/production
ALLOW_DEBUG_AUTH=true
ALLOW_QUERY_AUTH_TOKENS=true
CORS_ALLOWED_ORIGINS=*
```

## Troubleshooting

### Server Won't Start: Auth Configuration

```text
ValueError: AUTH_REQUIRED must be true when APP_ENV is staging/production
```

Set `AUTH_REQUIRED=true` or verify `AUTH_MODE=neon`/`AUTH_MODE=entra`
(production auth modes auto-enable auth).

### Server Won't Start: Database Configuration

```text
ValueError: DATABASE_URL is required when database_required=true
```

Set `DATABASE_URL` for Neon PostgreSQL, or set `DATABASE_REQUIRED=false` (not recommended for production).

### Server Won't Start: Entra Configuration

```text
ValueError: ENTRA_JWKS_URL is required when AUTH_MODE=entra
```

Set all required Entra variables:

```bash
ENTRA_JWKS_URL=https://login.microsoftonline.com/common/discovery/v2.0/keys
ENTRA_AUDIENCE=api://your-client-id
ENTRA_ISSUER_TEMPLATE=https://login.microsoftonline.com/{tenantid}/v2.0
```

### Health Check Returns 503

Check `/ready` for component status:

```bash
curl -sS https://your-server/ready | jq
```

Common causes:

- **`planner: missing`**: `DSPY_LM_MODEL` not configured or invalid API key
- **`database: missing`**: `DATABASE_URL` not configured or connection failed

### CORS Errors

Verify `CORS_ALLOWED_ORIGINS` includes your frontend origin:

```bash
CORS_ALLOWED_ORIGINS=https://app.yourdomain.com,https://admin.yourdomain.com
```

Wildcards (`*`) are not allowed in staging/production.

## Related Documentation

- [Auth Modes Reference](../reference/auth.md)
- [Database Architecture](../reference/database.md)
- [Daytona Architecture](../reference/daytona-architecture.md)
- [Runtime Settings](./runtime-settings.md)
