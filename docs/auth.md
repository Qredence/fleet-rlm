# Auth Modes (Dev vs Entra)

The backend uses an auth abstraction with normalized identity output:

- `tenant_claim` (`tid`)
- `user_claim` (`oid`)
- `email`
- `name`

All server logic consumes this normalized shape.

## Configuration

- `AUTH_MODE=dev|entra` (default `dev`)
- `AUTH_REQUIRED=true|false` (default: `false` in `dev`, `true` in `entra`)
- `DEV_JWT_SECRET=...`
- Future placeholders: `ENTRA_JWKS_URL`, `ENTRA_ISSUER`, `ENTRA_AUDIENCE`

## `AUTH_MODE=dev`

`DevAuth` accepts either:

1. Debug headers:
- `X-Debug-Tenant-Id`
- `X-Debug-User-Id`
- `X-Debug-Email`
- `X-Debug-Name`

2. Local HS256 JWT (`Authorization: Bearer ...`) with claims:
- `tid`
- `oid`
- `email`
- `name`

For WebSocket clients that cannot set custom headers, `AUTH_MODE=dev` also accepts query parameters:

- `debug_tenant_id`
- `debug_user_id`
- `debug_email`
- `debug_name`
- `access_token` (HS256 token with `tid`/`oid` claims)

WebSocket evaluation order in dev mode is:

1. Header debug identity
2. Header bearer token
3. Query debug identity
4. Query `access_token`
5. Reject with `401`

When `AUTH_REQUIRED=false` in dev mode:

- Missing/invalid auth does not block requests.
- HTTP/WS routes run with fallback identity:
  - `tenant_claim=default` (or `ws_default_workspace_id`)
  - `user_claim=anonymous` (or `ws_default_user_id`)
- If valid debug headers/token are provided, those still take precedence.

Issue a token:

```bash
# from repo root
uv run python scripts/dev_issue_token.py \
  --tid "00000000-0000-0000-0000-000000000123" \
  --oid "00000000-0000-0000-0000-000000000456" \
  --email dev@example.com \
  --name "Dev User"
```

## `AUTH_MODE=entra`

`EntraAuth` is scaffolded but intentionally fail-closed today.

- App startup fails immediately when `AUTH_MODE=entra` is selected.
- HTTP returns `503` with an explicit not-implemented message.
- WebSocket closes with auth error.

Next step is JWKS validation wiring for real Entra OIDC access tokens.

## Route Enforcement

- `AUTH_REQUIRED=true`: non-health HTTP routes and WS routes enforce auth.
- `AUTH_REQUIRED=false` (dev default): routes accept unauthenticated requests and use fallback identity.
- Missing/invalid auth is rejected (`401` for HTTP, `1008` close for WS) only when auth is required.

## Identity Authority

Auth claims are canonical for tenant/user authority.

For WebSocket payload/query compatibility, `workspace_id`/`user_id` can still be sent but are non-authoritative.
