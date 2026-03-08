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
- `ENTRA_JWKS_URL`
- `ENTRA_AUDIENCE`
- `ENTRA_ISSUER_TEMPLATE` (optional override; defaults to `https://login.microsoftonline.com/{tenantid}/v2.0`)

Guardrails from server config:
- in `staging/production`: `AUTH_REQUIRED=true`, debug auth disabled, wildcard CORS blocked
- in `AUTH_MODE=entra`: `DATABASE_REQUIRED=true` because tenant admission is DB-backed

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

Entra mode is now the real multitenant auth path:

- validates bearer tokens against Entra JWKS
- requires `tid` plus `oid` or `sub`
- derives the expected issuer from `ENTRA_ISSUER_TEMPLATE`
- reuses the same access token for HTTP and WebSocket auth
- treats the Neon `tenants` table as the tenant allowlist source of truth

Admission policy:

- unknown tenants are rejected with `403`
- suspended/deleted tenants are rejected with `403`
- users may be upserted inside an already-allowed tenant
- tenant creation is onboarding work, not a side effect of login/runtime bootstrap

Frontend SPA expectations:

- default authority is `https://login.microsoftonline.com/organizations`
- canonical redirect and post-logout path is `/login`
- delegated scope format is `api://<api-app-client-id>/access_as_user`

## Route Enforcement

- `AUTH_REQUIRED=true`: enforce auth on non-health HTTP routes and WebSockets
- `AUTH_REQUIRED=false`: allow fallback identity in dev-style local workflows

## Identity Authority

Auth claims are canonical tenant/user authority.

WS payload/query values such as `workspace_id` and `user_id` are compatibility fields and not authoritative identity in authenticated flows.

`GET /api/v1/auth/me` is the canonical frontend identity/bootstrap endpoint and returns both external claim identifiers (`tenant_claim`, `user_claim`) and resolved internal IDs (`tenant_id`, `user_id`) when Entra + Neon admission succeeds.
