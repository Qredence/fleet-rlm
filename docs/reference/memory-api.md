# Memory API Reference

This reference documents the memory browsing HTTP API exposed under `/api/v1/memory`.

All endpoints require authentication. See [Auth Modes](auth.md) for details.

---

## Overview

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/memory` | Browse memory items with optional scope filters |

---

## Authentication

All memory endpoints require a valid authentication token. Calls without a token return `401 Unauthorized`.

```bash
curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/v1/memory?scope=session\&scope_id=session-123
```

---

## Endpoints

### `GET /api/v1/memory`

Return memory items filtered by scope and scope_id. Without filters, returns all memory for the authenticated user.

#### Query Parameters

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `scope` | string | no | `null` | `user`, `tenant`, `workspace`, `run`, `session` | Filter by memory scope |
| `scope_id` | string | no | `null` | — | Filter by scope identifier |
| `limit` | integer | no | `100` | 1-200 | Page size |
| `offset` | integer | no | `0` | >= 0 | Pagination offset |

#### Response

```json
{
  "items": [
    {
      "id": "memory-uuid",
      "scope": "session",
      "scope_id": "session-123",
      "kind": "fact",
      "source": "agent",
      "status": "active",
      "content_text": "User prefers concise answers.",
      "importance": 80,
      "tags": ["preference"],
      "created_at": "2026-03-09T12:00:00Z"
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 100
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `items` | array | List of [`MemoryItemResponse`](#memoryitemresponse) objects |
| `total` | integer | Total number of matching memory items |
| `offset` | integer | Current pagination offset |
| `limit` | integer | Current page size |

#### Error Responses

| Status | Description |
|--------|-------------|
| `400 Bad Request` | Invalid scope filter value |
| `401 Unauthorized` | Missing or invalid authentication token |
| `503 Service Unavailable` | Memory services are unavailable because server startup is incomplete |

---

## Schemas

### `MemoryItemResponse`

Single memory item returned by the memory browse endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Durable memory item identifier |
| `scope` | string | Memory scope (e.g. `user`, `tenant`, `workspace`, `run`, `session`) |
| `scope_id` | string | Identifier within the scope |
| `kind` | string | Memory kind (e.g. `fact`, `observation`, `preference`) |
| `source` | string | Memory source (e.g. `user`, `agent`, `system`) |
| `status` | string | Memory status (e.g. `active`, `archived`) |
| `content_text` | string \| null | Textual content when available |
| `importance` | integer | Importance score (0-100) |
| `tags` | array | Associated tags |
| `created_at` | string | ISO-8601 creation timestamp |

### `MemoryListResponse`

Paginated memory item list response.

| Field | Type | Description |
|-------|------|-------------|
| `items` | array | List of `MemoryItemResponse` objects |
| `total` | integer | Total matching memory items |
| `offset` | integer | Current pagination offset |
| `limit` | integer | Current page size |

---

## Error Codes

All memory endpoints share a common error response shape:

```json
{
  "detail": "Invalid scope: <value>"
}
```

| Status | Cause | Typical Detail |
|--------|-------|----------------|
| `400` | Invalid scope filter value | `Invalid scope: ...` |
| `401` | Missing or invalid auth token | Authentication is required |
| `503` | Database persistence unavailable | `Database persistence is unavailable.` |
