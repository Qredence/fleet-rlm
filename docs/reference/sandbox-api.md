# Sandbox API Reference

This reference documents the Daytona sandbox management HTTP API exposed under `/api/v1/sandboxes`.

All endpoints require authentication. See [Auth Modes](auth.md) for details.

---

## Overview

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/sandboxes` | List active sandboxes |
| `GET` | `/api/v1/sandboxes/{sandbox_id}` | Get sandbox details |
| `DELETE` | `/api/v1/sandboxes/{sandbox_id}` | Stop and delete a sandbox |
| `POST` | `/api/v1/sandboxes/{sandbox_id}/archive` | Archive a sandbox to cold storage |

---

## Authentication

All sandbox endpoints require a valid authentication token. Calls without a token return `401 Unauthorized`.

```bash
curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/v1/sandboxes
```

---

## Endpoints

### `GET /api/v1/sandboxes`

List active Daytona sandboxes with id, state, created_at, and volume info.

#### Query Parameters

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `page` | integer | no | `1` | >= 1 | Page number for pagination |
| `limit` | integer | no | `100` | 1-1000 | Maximum sandboxes per page |

#### Response

```json
{
  "items": [
    {
      "id": "sandbox-123",
      "name": "my-sandbox",
      "state": "started",
      "created_at": "2026-03-09T12:00:00Z",
      "volume_name": "fleet-rlm-volume",
      "labels": {},
      "cpu": 2,
      "memory": 4,
      "disk": 10
    }
  ],
  "total": 1,
  "page": 1,
  "total_pages": 1
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `items` | array | List of [`SandboxListItem`](#sandboxlistitem) objects |
| `total` | integer | Total number of sandboxes across all pages |
| `page` | integer | Current page number |
| `total_pages` | integer | Total number of pages |

#### Error Responses

| Status | Description |
|--------|-------------|
| `401 Unauthorized` | Missing or invalid authentication token |
| `503 Service Unavailable` | Sandbox services are unavailable because server startup is incomplete |

---

### `GET /api/v1/sandboxes/{sandbox_id}`

Return full sandbox details including state, config, and volume.

#### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `sandbox_id` | string | Unique sandbox identifier |

#### Response

```json
{
  "id": "sandbox-123",
  "name": "my-sandbox",
  "state": "started",
  "created_at": "2026-03-09T12:00:00Z",
  "volume_name": "fleet-rlm-volume",
  "labels": {},
  "cpu": 2,
  "memory": 4,
  "disk": 10,
  "env_vars": {},
  "image": "daytonaio/workspace-resume:latest",
  "snapshot": null,
  "language": null,
  "auto_stop_interval": 30,
  "auto_archive_interval": 60,
  "auto_delete_interval": null,
  "ephemeral": false,
  "network_block_all": false,
  "network_allow_list": null,
  "volumes": []
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Sandbox identifier |
| `name` | string | Sandbox name |
| `state` | string | Sandbox state (e.g. `started`, `stopped`, `archived`) |
| `created_at` | string \| null | ISO-8601 creation timestamp |
| `volume_name` | string \| null | Name of the persistent volume attached to the sandbox |
| `labels` | object | Custom labels attached to the sandbox |
| `cpu` | integer \| null | Allocated CPU cores |
| `memory` | integer \| null | Allocated memory in GiB |
| `disk` | integer \| null | Allocated disk in GiB |
| `env_vars` | object | Environment variables configured for the sandbox |
| `image` | string \| null | Base image or declarative image used by the sandbox |
| `snapshot` | string \| null | Snapshot name used to create the sandbox |
| `language` | string \| null | Programming language of the sandbox |
| `auto_stop_interval` | integer \| null | Minutes of inactivity before auto-stopping |
| `auto_archive_interval` | integer \| null | Minutes after stop before archiving to cold storage |
| `auto_delete_interval` | integer \| null | Minutes after archive before permanent deletion |
| `ephemeral` | boolean \| null | Whether the sandbox is ephemeral |
| `network_block_all` | boolean \| null | Whether all outbound network is blocked |
| `network_allow_list` | string \| null | Comma-separated list of allowed domains |
| `volumes` | array | Detailed volume mounts |

#### Error Responses

| Status | Description |
|--------|-------------|
| `401 Unauthorized` | Missing or invalid authentication token |
| `404 Not Found` | Sandbox not found or inaccessible |
| `503 Service Unavailable` | Sandbox services are unavailable because server startup is incomplete |

---

### `DELETE /api/v1/sandboxes/{sandbox_id}`

Stop and permanently delete a Daytona sandbox. Returns `204 No Content` on success.

#### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `sandbox_id` | string | Unique sandbox identifier |

#### Response

`204 No Content` — No response body on success.

#### Error Responses

| Status | Description |
|--------|-------------|
| `401 Unauthorized` | Missing or invalid authentication token |
| `404 Not Found` | Sandbox not found or inaccessible |
| `503 Service Unavailable` | Sandbox services are unavailable because server startup is incomplete |

---

### `POST /api/v1/sandboxes/{sandbox_id}/archive`

Archive a Daytona sandbox to cold storage for later recovery.

#### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `sandbox_id` | string | Unique sandbox identifier |

#### Response

```json
{
  "ok": true
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `ok` | boolean | Whether the sandbox was archived successfully |

#### Error Responses

| Status | Description |
|--------|-------------|
| `401 Unauthorized` | Missing or invalid authentication token |
| `404 Not Found` | Sandbox not found or inaccessible |
| `503 Service Unavailable` | Sandbox services are unavailable because server startup is incomplete |

---

## Schemas

### `SandboxListItem`

Single sandbox entry returned by the list endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Sandbox identifier |
| `name` | string | Sandbox name |
| `state` | string | Sandbox state (e.g. `started`, `stopped`, `archived`) |
| `created_at` | string \| null | ISO-8601 creation timestamp |
| `volume_name` | string \| null | Name of the persistent volume attached to the sandbox |
| `labels` | object | Custom labels attached to the sandbox |
| `cpu` | integer \| null | Allocated CPU cores |
| `memory` | integer \| null | Allocated memory in GiB |
| `disk` | integer \| null | Allocated disk in GiB |

### `SandboxDetailResponse`

Extends `SandboxListItem` with full configuration details.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Sandbox identifier |
| `name` | string | Sandbox name |
| `state` | string | Sandbox state |
| `created_at` | string \| null | ISO-8601 creation timestamp |
| `volume_name` | string \| null | Name of the persistent volume |
| `labels` | object | Custom labels |
| `cpu` | integer \| null | Allocated CPU cores |
| `memory` | integer \| null | Allocated memory in GiB |
| `disk` | integer \| null | Allocated disk in GiB |
| `env_vars` | object | Environment variables |
| `image` | string \| null | Base image |
| `snapshot` | string \| null | Snapshot name |
| `language` | string \| null | Programming language |
| `auto_stop_interval` | integer \| null | Auto-stop timer in minutes |
| `auto_archive_interval` | integer \| null | Auto-archive timer in minutes |
| `auto_delete_interval` | integer \| null | Auto-delete timer in minutes |
| `ephemeral` | boolean \| null | Whether the sandbox is ephemeral |
| `network_block_all` | boolean \| null | Whether all outbound network is blocked |
| `network_allow_list` | string \| null | Comma-separated allowed domains |
| `volumes` | array | Detailed volume mounts |

---

## Error Codes

All sandbox endpoints share a common error response shape:

```json
{
  "detail": "Sandbox not found: <reason>"
}
```

| Status | Cause | Typical Detail |
|--------|-------|----------------|
| `401` | Missing or invalid auth token | Authentication is required |
| `404` | Sandbox does not exist or is inaccessible | `Sandbox not found: ...` |
| `503` | Daytona connection/auth/timeout error | `Sandbox service unavailable: ...` |

---

## Daytona Lifecycle Timers

The following idle lifecycle timers apply to Daytona sandboxes:

| Timer | Default | Description |
|-------|---------|-------------|
| `auto_stop_interval` | 30 minutes | Inactivity before auto-stopping |
| `auto_archive_interval` | 60 minutes | Time after stop before archiving |
| `auto_delete_interval` | null (disabled) | Time after archive before deletion |

See [Daytona Runtime Architecture](daytona-runtime-architecture.md) for provider-level details.
