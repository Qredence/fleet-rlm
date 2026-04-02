# Auth Modes (Dev vs Entra)

`fleet-rlm` supports two authentication modes via `AUTH_MODE`:

- **`dev`** — Development mode with debug headers and HS256 JWT tokens
- **`entra`** — Production mode with Microsoft Entra ID and RS256 JWT validation

All routes consume normalized identity fields from auth providers:

| Field | Description | Source |
|-------|-------------|--------|
| `tenant_claim` | Tenant identifier | `tid` claim |
| `user_claim` | User identifier | `oid` or `sub` claim |
| `email` | User email (optional) | `email`, `preferred_username`, or `upn` claim |
| `name` | User display name (optional) | `name` claim |

---

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `AUTH_MODE` | Auth provider mode (`dev` or `entra`) | `dev` | No |
| `AUTH_REQUIRED` | Enforce authentication on all routes | `true` when `AUTH_MODE=entra` | No |
| `ALLOW_DEBUG_AUTH` | Enable debug header authentication | `true` in local env | No |
| `ALLOW_QUERY_AUTH_TOKENS` | Allow tokens in WebSocket query params | `true` in local env or `AUTH_MODE=entra` | No |
| `DEV_JWT_SECRET` | Secret for HS256 token signing/verification | `change-me` | In staging/prod |
| `ENTRA_JWKS_URL` | Entra JWKS endpoint URL | — | When `AUTH_MODE=entra` |
| `ENTRA_AUDIENCE` | Expected token audience (API client ID) | — | When `AUTH_MODE=entra` |
| `ENTRA_ISSUER_TEMPLATE` | Issuer URL template with `{tenantid}` placeholder | `https://login.microsoftonline.com/{tenantid}/v2.0` | No |

### Guardrails

The server enforces these guardrails at startup:

**When `APP_ENV=staging` or `APP_ENV=production`:**
- `AUTH_REQUIRED` must be `true`
- `ALLOW_DEBUG_AUTH` must be `false`
- `ALLOW_QUERY_AUTH_TOKENS` must be `false` (unless `AUTH_MODE=entra`)
- `CORS_ALLOWED_ORIGINS` cannot contain `*`
- `DEV_JWT_SECRET` must be customized from default value

**When `AUTH_MODE=entra`:**
- `AUTH_REQUIRED` must be `true`
- `DATABASE_REQUIRED` must be `true` (tenant admission requires database)
- `ENTRA_JWKS_URL` must be configured
- `ENTRA_AUDIENCE` must be configured
- `ENTRA_ISSUER_TEMPLATE` must contain `{tenantid}` placeholder

---

## AUTH_MODE=dev

Development mode provides flexible authentication for local development and testing.

### Authentication Methods

#### 1. Debug Headers

When `ALLOW_DEBUG_AUTH=true`, the following headers authenticate requests:

```http
X-Debug-Tenant-Id: <tenant-id>
X-Debug-User-Id: <user-id>
X-Debug-Email: <email>        # Optional
X-Debug-Name: <name>          # Optional
```

**Example:**
```bash
curl -H "X-Debug-Tenant-Id: tenant-123" \
     -H "X-Debug-User-Id: user-456" \
     -H "X-Debug-Email: alice@example.com" \
     http://localhost:8000/api/v1/auth/me
```

#### 2. Bearer Token (HS256)

Sign and verify JWT tokens using the `DEV_JWT_SECRET`:

**Required claims:**
- `tid` — Tenant ID
- `oid` — User/object ID

**Optional claims:**
- `email`
- `name`

**Example token payload:**
```json
{
  "tid": "tenant-123",
  "oid": "user-456",
  "email": "alice@example.com",
  "name": "Alice Smith"
}
```

**Example request:**
```bash
# Generate a dev token (using python-jose or similar)
TOKEN=$(python -c "import jwt; print(jwt.encode({'tid': 'tenant-123', 'oid': 'user-456'}, 'change-me', algorithm='HS256'))")

curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/v1/auth/me
```

#### 3. WebSocket Query Parameters

When `ALLOW_QUERY_AUTH_TOKENS=true`, WebSocket connections can bootstrap via query parameters when a bearer `Authorization` header is not available:

**Debug auth (when `ALLOW_DEBUG_AUTH=true`):**

```text
ws://localhost:8000/api/v1/ws/chat?debug_tenant_id=tenant-123&debug_user_id=user-456
```

**Token auth:**

```text
ws://localhost:8000/api/v1/ws/chat?access_token=<hs256-jwt>
```

Prefer `Authorization: Bearer ...` on HTTP requests and on websocket clients that can forward headers. Use `access_token` query bootstrap only on websocket paths that explicitly support it.

### Fallback Behavior

When `AUTH_REQUIRED=false`:
- If authentication fails, requests fall back to default identity
- Default tenant: `default`
- Default user: `anonymous`

This is useful for local development but **must not** be used in staging/production.

---

## AUTH_MODE=entra

Entra mode provides production-grade multitenant authentication using Microsoft Entra ID (Azure AD).

### Token Validation Process

1. **Extract Bearer Token** — From `Authorization: Bearer <token>` header or WebSocket `access_token` query parameter

2. **Decode Unverified Claims** — Extract `tid` claim to determine the tenant

3. **Derive Expected Issuer** — Replace `{tenantid}` in `ENTRA_ISSUER_TEMPLATE` with the token's `tid`:
   ```
   https://login.microsoftonline.com/{tenantid}/v2.0
   ```

4. **Fetch Signing Key** — Use JWKS client to fetch the public key matching the token's `kid`

5. **Verify Signature** — Validate RS256 signature against the signing key

6. **Verify Claims** — Validate:
   - `iss` matches expected issuer
   - `aud` matches `ENTRA_AUDIENCE`
   - `exp` and `iat` are valid (required claims)
   - `tid` is present

### Required Configuration

```bash
# Required for AUTH_MODE=entra
AUTH_MODE=entra
ENTRA_JWKS_URL=https://login.microsoftonline.com/common/discovery/v2.0/keys
ENTRA_AUDIENCE=api://your-api-client-id
DATABASE_URL=postgresql://...  # Required for tenant admission

# Optional (defaults shown)
ENTRA_ISSUER_TEMPLATE=https://login.microsoftonline.com/{tenantid}/v2.0
```

### JWKS Configuration

The JWKS URL provides public keys for signature verification:

| Environment | JWKS URL |
|-------------|----------|
| Azure Public Cloud | `https://login.microsoftonline.com/common/discovery/v2.0/keys` |
| Azure Government | `https://login.microsoftonline.us/common/discovery/v2.0/keys` |
| Custom Sovereign Cloud | Use your cloud's discovery endpoint |

JWKS client behavior:
- Keys are cached for 5 minutes (`lifespan=300`)
- Automatic key refresh on cache expiry
- Network failures return `503 Service Unavailable`

### Token Claim Mapping

Entra tokens map to normalized identity:

| Entra Claim | Normalized Field | Notes |
|-------------|------------------|-------|
| `tid` | `tenant_claim` | Required; used for issuer derivation |
| `oid` | `user_claim` | Preferred; falls back to `sub` |
| `sub` | `user_claim` | Used if `oid` is missing |
| `preferred_username` | `email` | Primary email source |
| `email` | `email` | Fallback |
| `upn` | `email` | Fallback for legacy tokens |
| `name` | `name` | Display name |

### WebSocket Authentication

For WebSocket connections, include the access token in the query string:

```text
wss://your-api.com/api/v1/ws/chat?access_token=<entra-access-token>
```

**Note:** Query tokens are enabled by default for Entra mode (`ALLOW_QUERY_AUTH_TOKENS=true`).

---

## AUTH_REQUIRED Setting

Controls whether authentication is enforced on non-health routes.

| Value | Behavior |
|-------|----------|
| `true` | All non-health HTTP routes and WebSockets require valid authentication |
| `false` | Failed authentication falls back to default identity (`default`/`anonymous`) |

**Default:**
- `true` when `AUTH_MODE=entra`
- `false` when `AUTH_MODE=dev` and `APP_ENV=local`
- `true` when `APP_ENV=staging` or `APP_ENV=production` (enforced)

---

## ALLOW_DEBUG_AUTH Setting

Controls whether debug headers (`X-Debug-*`) are accepted for authentication.

| Value | Behavior |
|-------|----------|
| `true` | Debug headers accepted as authentication credentials |
| `false` | Debug headers ignored; only Bearer tokens accepted |

**Default:**
- `true` when `APP_ENV=local`
- `false` when `APP_ENV=staging` or `APP_ENV=production` (enforced)

**Security Note:** Debug headers must never be enabled in production environments as they bypass real authentication.

---

## Neon Tenant Admission Flow

When `AUTH_MODE=entra`, the system performs tenant admission against the Neon database after token validation.

### Admission Process

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Entra Token    │────▶│  JWKS Validate  │────▶│  Normalized     │
│  Validation     │     │  & Claims       │     │  Identity       │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                                                         ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Tenant/User    │◀────│  Repository     │◀────│  Admission      │
│  IDs Resolved   │     │  Lookup         │     │  Check          │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

1. **Token Validation** — Entra token validated, claims extracted
2. **Tenant Lookup** — Query `tenants` table by `entra_tenant_id` matching `tid` claim
3. **Admission Check**:
   - **Unknown tenant** → `403 Forbidden` ("Tenant is not allowlisted for Fleet RLM.")
   - **Suspended tenant** → `403 Forbidden` ("Tenant access is suspended for Fleet RLM.")
   - **Deleted tenant** → `403 Forbidden` ("Tenant access has been removed for Fleet RLM.")
   - **Active tenant** → Proceed to user resolution
4. **User Resolution** — Upsert user into `users` table with membership in the tenant
5. **Return Identity** — Internal `tenant_id` and `user_id` returned for request context

### Database Requirements

Entra mode requires database for tenant admission:

- `DATABASE_URL` must be configured
- `DATABASE_REQUIRED=true` is enforced
- `tenants` table is the tenant allowlist source of truth

### Tenant Onboarding

Tenant creation is **not** a side effect of login. New tenants must be explicitly added to the database:

```sql
INSERT INTO tenants (id, entra_tenant_id, status, plan)
VALUES (gen_random_uuid(), '<entra-tenant-id>', 'active', 'free');
```

### API Response

The `/api/v1/auth/me` endpoint returns both external claims and internal IDs:

```json
{
  "tenant_claim": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "user_claim": "ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj",
  "email": "alice@example.com",
  "name": "Alice Smith",
  "tenant_id": "uuid-internal-tenant-id",
  "user_id": "uuid-internal-user-id"
}
```

---

## Route Enforcement

### HTTP Routes

- `GET /health` — No auth required
- `GET /ready` — No auth required
- All other routes — Auth required when `AUTH_REQUIRED=true`

### WebSocket Routes

- `WS /api/v1/ws/chat` — Auth required when `AUTH_REQUIRED=true`
- `WS /api/v1/ws/execution` — Auth required when `AUTH_REQUIRED=true`

---

## Identity Authority

Auth claims are the **canonical** source of tenant/user identity:

- `tenant_claim` and `user_claim` from auth are authoritative
- `workspace_id` and `user_id` on websocket payloads and query strings are unsupported and should be rejected
- `session_id` is the only authoritative client-controlled websocket selector
- Internal `tenant_id` and `user_id` are resolved via database lookup during admission

**Frontend SPA expectations:**
- Default authority: `https://login.microsoftonline.com/organizations`
- Redirect path: `/login`
- Post-logout path: `/login`
- Delegated scope format: `api://<api-app-client-id>/access_as_user`

---

## Error Responses

| Status Code | Description |
|-------------|-------------|
| `401 Unauthorized` | Missing or invalid authentication token |
| `403 Forbidden` | Tenant not allowlisted or tenant status not active |
| `503 Service Unavailable` | JWKS configuration missing or network failure |
