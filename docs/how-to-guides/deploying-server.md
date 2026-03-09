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
| `/api/v1/*` | REST API endpoints |
| `/api/v1/ws/chat` | WebSocket chat runtime |
| `/api/v1/ws/execution` | WebSocket execution stream |

## Production Environment Variables

### Required Configuration

| Variable | Description | Example |
|----------|-------------|---------|
| `APP_ENV` | Runtime environment (`local`, `staging`, `production`) | `production` |
| `AUTH_MODE` | Authentication mode (`dev` or `entra`) | `entra` |
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

# Authentication (Entra ID)
AUTH_MODE=entra
AUTH_REQUIRED=true

# Entra Configuration
ENTRA_JWKS_URL=https://login.microsoftonline.com/common/discovery/v2.0/keys
ENTRA_AUDIENCE=api://your-api-app-client-id
ENTRA_ISSUER_TEMPLATE=https://login.microsoftonline.com/{tenantid}/v2.0

# CORS (explicit origins only)
CORS_ALLOWED_ORIGINS=https://app.yourdomain.com,https://admin.yourdomain.com

# Optional: Delegate model for sub-agent calls
DSPY_DELEGATE_LM_MODEL=openai/gpt-4o-mini
```

## AUTH_MODE=entra Configuration

Microsoft Entra ID (formerly Azure AD) provides production-grade multitenant authentication.

### Prerequisites

1. **API App Registration** in Microsoft Entra:
   - Create an app registration in your Entra tenant
   - Note the Application (client) ID for `ENTRA_AUDIENCE`
   - Expose an API scope (e.g., `api://<client-id>/access_as_user`)

2. **Frontend App Registration** (SPA):
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

1. Client obtains token from Entra (via frontend MSAL)
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

WebSocket connections support two authentication methods:

1. **Header-based** (preferred):
   ```
   Authorization: Bearer <entra-token>
   ```

2. **Query parameter** (for browsers with limited header support):
   ```
   wss://your-server/api/v1/ws/chat?access_token=<entra-token>
   ```

Query parameter auth requires `ALLOW_QUERY_AUTH_TOKENS=true` (automatic in Entra mode).

## DATABASE_URL Setup

Fleet-RLM uses Neon PostgreSQL for persistence with Row-Level Security (RLS) for tenant isolation.

### Connection String Format

```bash
DATABASE_URL=postgresql://<user>:<password>@<host>/<database>?sslmode=require
```

For Neon specifically:

```bash
DATABASE_URL=postgresql://neondb_owner:password@ep-cool-darkness-123456.us-east-2.aws.neon.tech/neondb?sslmode=require
```

### Requirements

- **SSL required**: Always use `sslmode=require` or higher
- **Direct connection**: Use the non-pooler endpoint for server runtime
- **Migrations**: Run with Alembic (see [Database Architecture](../reference/database.md))

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
  "sandbox_provider": "modal"
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
  "version": "0.4.95"
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
| `sandbox_provider` | Active sandbox provider (`modal`, `local`, `daytona`) |

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
      - AUTH_MODE=entra
      - AUTH_REQUIRED=true
      - DATABASE_URL=${DATABASE_URL}
      - DSPY_LM_MODEL=${DSPY_LM_MODEL}
      - DSPY_LLM_API_KEY=${DSPY_LLM_API_KEY}
      - ENTRA_JWKS_URL=${ENTRA_JWKS_URL}
      - ENTRA_AUDIENCE=${ENTRA_AUDIENCE}
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
    AUTH_MODE=entra \
    AUTH_REQUIRED=true \
    DATABASE_URL=secretref:database-url \
    DSPY_LM_MODEL=secretref:dspy-model \
    DSPY_LLM_API_KEY=secretref:dspy-api-key \
    ENTRA_JWKS_URL=secretref:entra-jwks \
    ENTRA_AUDIENCE=secretref:entra-audience
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
              value: "entra"
            - name: AUTH_REQUIRED
              value: "true"
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: fleet-secrets
                  key: database-url
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
            - name: ENTRA_JWKS_URL
              valueFrom:
                secretKeyRef:
                  name: fleet-secrets
                  key: entra-jwks
            - name: ENTRA_AUDIENCE
              valueFrom:
                secretKeyRef:
                  name: fleet-secrets
                  key: entra-audience
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

### Entra Mode Validation

The server will **fail to start** if:

1. `AUTH_REQUIRED=false` with `AUTH_MODE=entra`
2. `DATABASE_REQUIRED=false` with `AUTH_MODE=entra`
3. `ENTRA_JWKS_URL` not configured
4. `ENTRA_AUDIENCE` not configured
5. `ENTRA_ISSUER_TEMPLATE` missing `{tenantid}` placeholder

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

```
ValueError: AUTH_REQUIRED must be true when APP_ENV is staging/production
```

Set `AUTH_REQUIRED=true` or verify `AUTH_MODE=entra` (auto-enables auth).

### Server Won't Start: Database Configuration

```
ValueError: DATABASE_URL is required when database_required=true
```

Set `DATABASE_URL` for Neon PostgreSQL, or set `DATABASE_REQUIRED=false` (not recommended for production).

### Server Won't Start: Entra Configuration

```
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
- [Configuring Modal](./configuring-modal.md)
- [Runtime Settings](./runtime-settings.md)
