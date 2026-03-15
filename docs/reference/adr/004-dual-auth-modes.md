# ADR-004: Dual Authentication Modes (Dev/Entra)

## Status

Accepted

## Context

Fleet-RLM requires authentication that works in two distinct environments:

1. **Local development**: Developers need quick iteration without complex identity setup
2. **Production**: Enterprise customers require robust, auditable authentication

These environments have conflicting requirements:

| Requirement | Local Development | Production |
|-------------|-------------------|------------|
| Identity provider | None/local | Microsoft Entra ID |
| Token validation | Relaxed | Strict RS256 |
| Debug capabilities | Full access | Restricted |
| Tenant isolation | Optional | Mandatory |
| Setup complexity | Minimal | Enterprise-grade |

Options considered:
- **Single production auth only**: Poor developer experience, friction for contributors
- **Custom auth system**: Maintenance burden, security risks
- **Environment-aware auth modes**: Switchable providers based on deployment context
- **Third-party auth proxy**: Additional infrastructure, limited control

## Decision

We implement **dual authentication modes** controlled by the `AUTH_MODE` environment variable:

- **`dev`**: Development mode with debug headers and HS256 JWT tokens
- **`entra`**: Production mode with Microsoft Entra ID and RS256 JWT validation

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     HTTP Request / WebSocket                     │
└──────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Auth Provider Factory                       │
│                                                                  │
│   AUTH_MODE=dev          │        AUTH_MODE=entra               │
│   ┌──────────────┐       │        ┌──────────────┐              │
│   │ DevAuthProvider│     │        │EntraAuthProvider│            │
│   │ - Debug headers│     │        │ - JWKS validation│           │
│   │ - HS256 tokens│       │        │ - RS256 tokens   │           │
│   └──────────────┘       │        └──────────────┘              │
└──────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Normalized Identity                           │
│  - tenant_claim (tid)                                           │
│  - user_claim (oid)                                             │
│  - email, name (optional)                                       │
└─────────────────────────────────────────────────────────────────┘
```

### Auth Provider Interface

Both providers implement a common interface returning `NormalizedIdentity`:

```python
class AuthProvider(Protocol):
    async def authenticate_http(self, request: Request) -> NormalizedIdentity: ...
    async def authenticate_websocket(self, websocket: WebSocket) -> NormalizedIdentity: ...

@dataclass
class NormalizedIdentity:
    tenant_claim: str  # tid from token
    user_claim: str    # oid from token
    email: str | None
    name: str | None
    raw_claims: dict[str, Any]
```

### Dev Mode (`AUTH_MODE=dev`)

Development mode provides flexible authentication for local development:

#### Authentication Methods

**1. Debug Headers** (when `ALLOW_DEBUG_AUTH=true`):

```http
X-Debug-Tenant-Id: tenant-123
X-Debug-User-Id: user-456
X-Debug-Email: alice@example.com
```

**2. HS256 Bearer Token**:

```python
import jwt

token = jwt.encode(
    {"tid": "tenant-123", "oid": "user-456"},
    DEV_JWT_SECRET,
    algorithm="HS256",
)
```

**3. WebSocket Query Parameters**:

```text
ws://localhost:8000/api/v1/ws/chat?debug_tenant_id=tenant-123&debug_user_id=user-456
```

#### Configuration

| Variable | Default | Notes |
|----------|---------|-------|
| `AUTH_MODE` | `dev` | Development default |
| `ALLOW_DEBUG_AUTH` | `true` | Enable debug headers |
| `ALLOW_QUERY_AUTH_TOKENS` | `true` | Enable query param auth |
| `DEV_JWT_SECRET` | `change-me` | Must be customized in staging/prod |

### Entra Mode (`AUTH_MODE=entra`)

Production mode uses Microsoft Entra ID (Azure AD) for authentication:

#### Token Validation Flow

```
1. Extract Bearer token from Authorization header
2. Decode unverified to extract tid claim
3. Derive expected issuer: https://login.microsoftonline.com/{tid}/v2.0
4. Fetch signing key from JWKS endpoint
5. Validate RS256 signature
6. Verify iss, aud, exp, iat claims
7. Return NormalizedIdentity
```

#### Configuration

| Variable | Required | Notes |
|----------|----------|-------|
| `AUTH_MODE` | Yes | Set to `entra` |
| `ENTRA_JWKS_URL` | Yes | `https://login.microsoftonline.com/common/discovery/v2.0/keys` |
| `ENTRA_AUDIENCE` | Yes | API client ID from Entra app registration |
| `ENTRA_ISSUER_TEMPLATE` | No | Default: `https://login.microsoftonline.com/{tenantid}/v2.0` |
| `DATABASE_URL` | Yes | Required for tenant admission |

### Tenant Admission

When using Entra mode, validated tokens trigger tenant admission:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Entra Token │────▶│ Validate &  │────▶│ Lookup      │
│             │     │ Extract tid │     │ Tenant      │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                    ┌──────────────────────────┘
                    ▼
         ┌─────────────────────┐
         │ Tenant Status?      │
         │ - active → proceed  │
         │ - unknown → 403     │
         │ - suspended → 403   │
         │ - deleted → 403     │
         └─────────────────────┘
```

Tenants must be explicitly allowlisted in the database — login does not auto-provision tenants.

### Guardrails

The system enforces guardrails at startup to prevent misconfiguration:

**In staging/production (`APP_ENV=staging|production`):**
- `AUTH_REQUIRED` must be `true`
- `ALLOW_DEBUG_AUTH` must be `false`
- `ALLOW_QUERY_AUTH_TOKENS` must be `false` (unless `AUTH_MODE=entra`)
- `DEV_JWT_SECRET` must be customized from default

**When `AUTH_MODE=entra`:**
- `AUTH_REQUIRED` is enforced `true`
- `DATABASE_REQUIRED` is enforced `true`
- All Entra configuration variables must be set

## Consequences

### Positive

- **Developer velocity**: Dev mode enables rapid local iteration without identity setup
- **Production security**: Entra mode provides enterprise-grade authentication
- **Single codebase**: Same auth interface for both modes
- **Guardrails**: Startup checks prevent security misconfigurations
- **Normalized identity**: Both modes produce the same identity structure

### Negative

- **Complexity**: Two auth implementations increase maintenance surface
- **Configuration burden**: Multiple environment variables to manage
- **Testing overhead**: Must test both auth paths

### Neutral

- Tenant admission only applies to Entra mode
- WebSocket auth supports query parameter tokens (useful for browser clients)
- Debug headers are Entra-like claims (`tid`, `oid`) for consistency

## References

- `src/fleet_rlm/server/auth/factory.py` — Auth provider factory
- `src/fleet_rlm/server/auth/dev.py` — Development auth provider
- `src/fleet_rlm/server/auth/entra.py` — Entra auth provider
- `src/fleet_rlm/server/auth/base.py` — Auth provider interface
- `src/fleet_rlm/server/auth/types.py` — NormalizedIdentity type
- `src/fleet_rlm/server/auth/admission.py` — Tenant admission logic
- `docs/reference/auth.md` — Full auth documentation
