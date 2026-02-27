# Auth Modes (Dev vs Entra)

`fleet-rlm` uses an auth abstraction under `src/fleet_rlm/server/auth/`.

All routes consume normalized identity fields:

- `tenant_claim` (`tid`)
- `user_claim` (`oid`)
- `email`
- `name`

## Configuration

Primary env/config controls:

- `AUTH_MODE=dev|entra` (default `dev`)
- `AUTH_REQUIRED=true|false`
- `DEV_JWT_SECRET`
- `ALLOW_DEBUG_AUTH`
- `ALLOW_QUERY_AUTH_TOKENS`
- `ENTRA_JWKS_URL` (future)
- `ENTRA_ISSUER` (future)
- `ENTRA_AUDIENCE` (future)

Guardrails from server config:
- in `staging/production`: `AUTH_REQUIRED=true`, debug auth disabled, wildcard CORS blocked

## `AUTH_MODE=dev`

Dev provider accepts:

1. Debug headers:
- `X-Debug-Tenant-Id`
- `X-Debug-User-Id`
- `X-Debug-Email`
- `X-Debug-Name`

2. `Authorization: Bearer <HS256 token>` with claims:
- `tid`
- `oid`
- optional `email`, `name`

3. WebSocket query fallbacks (when enabled):
- `debug_tenant_id`, `debug_user_id`
- optional `debug_email`, `debug_name`
- `access_token` (HS256)

If `AUTH_REQUIRED=false`, HTTP/WS can fall back to unauthenticated defaults (`default` / `anonymous`) when auth fails.

## `AUTH_MODE=entra`

Current state:

- scaffolded provider exists
- token verification is not implemented yet
- requests fail closed with explicit errors

This mode is reserved for future JWKS/OIDC validation wiring.

## Route Enforcement

- `AUTH_REQUIRED=true`: enforce auth on non-health HTTP routes and WebSockets
- `AUTH_REQUIRED=false`: allow fallback identity in dev-style local workflows

## Identity Authority

Auth claims are canonical tenant/user authority.

WS payload/query values such as `workspace_id` and `user_id` are compatibility fields and not authoritative identity in authenticated flows.
