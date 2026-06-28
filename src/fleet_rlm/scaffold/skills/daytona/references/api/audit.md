# Audit API

## GET `/audit/organizations/{organizationId}` {#daytona/tag/audit/GET/audit/organizations/{organizationId}}

**Get audit logs for organization**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |
| `page` | query | number | No | Page number of the results |
| `limit` | query | number | No | Number of results per page |
| `from` | query | string (date-time) | No | From date (ISO 8601 format) |
| `to` | query | string (date-time) | No | To date (ISO 8601 format) |
| `nextToken` | query | string | No | Token for cursor-based pagination. When provided, takes precedence over page parameter. |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Paginated list of organization audit logs | PaginatedAuditLogs |

---
