# Server API

## POST `/init` {#daytona-toolbox/tag/server/POST/init}

**Initialize toolbox server**

Set the auth token and initialize telemetry for the toolbox server

### Request Body

Initialization request

Schema: **InitializeRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `token` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | object |

---
