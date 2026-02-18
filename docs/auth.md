# Auth Modes (Dev vs Entra)

The backend uses an auth abstraction with normalized identity output:

- `tenant_claim` (`tid`)
- `user_claim` (`oid`)
- `email`
- `name`

All server logic consumes this normalized shape.

## Configuration

- `AUTH_MODE=dev|entra` (default `dev`)
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

- Non-health HTTP routes require auth.
- WebSocket routes (`/ws/chat`, `/ws/execution`) require auth at handshake.
- Missing/invalid auth is rejected (`401` for HTTP, `1008` close for WS).

## Identity Authority

Auth claims are canonical for tenant/user authority.

For WebSocket payload/query compatibility, `workspace_id`/`user_id` can still be sent but are non-authoritative.
