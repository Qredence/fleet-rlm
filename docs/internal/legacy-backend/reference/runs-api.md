# Runs API Reference

This reference documents the execution run steps HTTP API exposed under `/api/v1/runs`.

All endpoints require authentication. See [Auth Modes](auth.md) for details.

---

## Overview

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/runs/{run_id}/steps` | List execution trace steps for a run |

---

## Authentication

All runs endpoints require a valid authentication token. Calls without a token return `401 Unauthorized`.

```bash
curl -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/v1/runs/123e4567-e89b-12d3-a456-426614174000/steps
```

---

## Endpoints

### `GET /api/v1/runs/{run_id}/steps`

Paginated execution trace steps for a run with step_type, tool_name, tokens, and latency.

#### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `run_id` | string | Identifier of the run whose steps to list |

#### Query Parameters

| Parameter | Type | Required | Default | Constraints | Description |
|-----------|------|----------|---------|-------------|-------------|
| `limit` | integer | no | `50` | 1-200 | Page size |
| `offset` | integer | no | `0` | >= 0 | Pagination offset |

#### Response

```json
{
  "items": [
    {
      "id": "step-uuid",
      "step_index": 0,
      "step_type": "tool_call",
      "tool_name": "search",
      "tokens_in": 150,
      "tokens_out": 50,
      "latency_ms": 1200,
      "created_at": "2026-03-09T12:00:00Z"
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 50,
  "has_more": false
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `items` | array | List of [`RunStepItem`](#runstepitem) objects |
| `total` | integer | Total number of steps in the run |
| `offset` | integer | Current pagination offset |
| `limit` | integer | Current page size |
| `has_more` | boolean | Whether more steps exist beyond this page |

#### Error Responses

| Status | Description |
|--------|-------------|
| `401 Unauthorized` | Missing or invalid authentication token |
| `404 Not Found` | Run not found or inaccessible |
| `503 Service Unavailable` | Run services are unavailable because server startup is incomplete |

---

## Schemas

### `RunStepItem`

Single execution step for a run.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Durable step identifier |
| `step_index` | integer | Step position within the run |
| `step_type` | string | Step type (e.g. `tool_call`, `reasoning`) |
| `tool_name` | string \| null | Tool name when applicable |
| `tokens_in` | integer \| null | Input token count |
| `tokens_out` | integer \| null | Output token count |
| `latency_ms` | integer \| null | Step latency in milliseconds |
| `created_at` | string | ISO-8601 creation timestamp |

### `RunStepListResponse`

Paginated execution step list for a run.

| Field | Type | Description |
|-------|------|-------------|
| `items` | array | List of `RunStepItem` objects |
| `total` | integer | Total steps in run |
| `offset` | integer | Current pagination offset |
| `limit` | integer | Current page size |
| `has_more` | boolean | Whether more steps exist beyond this page |

---

## Error Codes

All runs endpoints share a common error response shape:

```json
{
  "detail": "Run not found"
}
```

| Status | Cause | Typical Detail |
|--------|-------|----------------|
| `401` | Missing or invalid auth token | Authentication is required |
| `404` | Run does not exist or is inaccessible | `Run not found` |
| `503` | Database persistence unavailable | `Database persistence is unavailable.` |
