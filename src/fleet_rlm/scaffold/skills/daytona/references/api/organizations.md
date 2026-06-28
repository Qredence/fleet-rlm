# Organizations API


## Contents

- GET `/organizations/invitations`
- GET `/organizations/invitations/count`
- POST `/organizations/invitations/{invitationId}/accept`/accept}
- POST `/organizations/invitations/{invitationId}/decline`/decline}
- GET `/organizations`
- POST `/organizations`
- PATCH `/organizations/{organizationId}/default-region`/default-region}
- GET `/organizations/{organizationId}`}
- DELETE `/organizations/{organizationId}`}
- GET `/organizations/{organizationId}/usage`/usage}
- PATCH `/organizations/{organizationId}/quota`/quota}
- PATCH `/organizations/{organizationId}/quota/{regionId}`/quota/{regionId}}
- POST `/organizations/{organizationId}/leave`/leave}
- POST `/organizations/{organizationId}/suspend`/suspend}
- POST `/organizations/{organizationId}/unsuspend`/unsuspend}
- GET `/organizations/otel-config/by-sandbox-auth-token/{authToken}`}
- GET `/organizations/{organizationId}/otel-config`/otel-config}
- PUT `/organizations/{organizationId}/otel-config`/otel-config}
- DELETE `/organizations/{organizationId}/otel-config`/otel-config}
- POST `/organizations/{organizationId}/sandbox-default-limited-network-egress`/sandbox-default-limited-network-egress}
- PUT `/organizations/{organizationId}/experimental-config`/experimental-config}
- GET `/organizations/{organizationId}/roles`/roles}
- POST `/organizations/{organizationId}/roles`/roles}
- PUT `/organizations/{organizationId}/roles/{roleId}`/roles/{roleId}}
- DELETE `/organizations/{organizationId}/roles/{roleId}`/roles/{roleId}}
- GET `/organizations/{organizationId}/users`/users}
- POST `/organizations/{organizationId}/users/{userId}/access`/users/{userId}/access}
- DELETE `/organizations/{organizationId}/users/{userId}`/users/{userId}}
- GET `/organizations/{organizationId}/invitations`/invitations}
- POST `/organizations/{organizationId}/invitations`/invitations}
- PUT `/organizations/{organizationId}/invitations/{invitationId}`/invitations/{invitationId}}
- POST `/organizations/{organizationId}/invitations/{invitationId}/cancel`/invitations/{invitationId}/cancel}
- GET `/regions`
- POST `/regions`
- GET `/regions/{id}`}
- PATCH `/regions/{id}`}
- DELETE `/regions/{id}`}
- POST `/regions/{id}/regenerate-proxy-api-key`/regenerate-proxy-api-key}
- POST `/regions/{id}/regenerate-ssh-gateway-api-key`/regenerate-ssh-gateway-api-key}
- POST `/regions/{id}/regenerate-snapshot-manager-credentials`/regenerate-snapshot-manager-credentials}

## GET `/organizations/invitations` {#daytona/tag/organizations/GET/organizations/invitations}

**List organization invitations for authenticated user**

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | List of organization invitations | array of OrganizationInvitation |

---

## GET `/organizations/invitations/count` {#daytona/tag/organizations/GET/organizations/invitations/count}

**Get count of organization invitations for authenticated user**

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Count of organization invitations | number |

---

## POST `/organizations/invitations/{invitationId}/accept` {#daytona/tag/organizations/POST/organizations/invitations/{invitationId}/accept}

**Accept organization invitation**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `invitationId` | path | string | Yes | Invitation ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Organization invitation accepted successfully | OrganizationInvitation |

---

## POST `/organizations/invitations/{invitationId}/decline` {#daytona/tag/organizations/POST/organizations/invitations/{invitationId}/decline}

**Decline organization invitation**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `invitationId` | path | string | Yes | Invitation ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Organization invitation declined successfully |  |

---

## GET `/organizations` {#daytona/tag/organizations/GET/organizations}

**List organizations**

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | List of organizations | array of Organization |

---

## POST `/organizations` {#daytona/tag/organizations/POST/organizations}

**Create organization**

### Request Body

Schema: **CreateOrganization**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | The name of organization |
| `defaultRegionId` | string | Yes | The ID of the default region for the organization |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 201 | Organization created successfully | Organization |

---

## PATCH `/organizations/{organizationId}/default-region` {#daytona/tag/organizations/PATCH/organizations/{organizationId}/default-region}

**Set default region for organization**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Request Body

Schema: **UpdateOrganizationDefaultRegion**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `defaultRegionId` | string | Yes | The ID of the default region for the organization |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Default region set successfully |  |

---

## GET `/organizations/{organizationId}` {#daytona/tag/organizations/GET/organizations/{organizationId}}

**Get organization by ID**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Organization details | Organization |

---

## DELETE `/organizations/{organizationId}` {#daytona/tag/organizations/DELETE/organizations/{organizationId}}

**Delete organization**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Organization deleted successfully |  |

---

## GET `/organizations/{organizationId}/usage` {#daytona/tag/organizations/GET/organizations/{organizationId}/usage}

**Get organization current usage overview**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Current usage overview | OrganizationUsageOverview |

---

## PATCH `/organizations/{organizationId}/quota` {#daytona/tag/organizations/PATCH/organizations/{organizationId}/quota}

**Update organization quota**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Request Body

Schema: **UpdateOrganizationQuota**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `maxCpuPerSandbox` | number | Yes |  |
| `maxMemoryPerSandbox` | number | Yes |  |
| `maxDiskPerSandbox` | number | Yes |  |
| `snapshotQuota` | number | Yes |  |
| `maxSnapshotSize` | number | Yes |  |
| `volumeQuota` | number | Yes |  |
| `authenticatedRateLimit` | number | Yes |  |
| `sandboxCreateRateLimit` | number | Yes |  |
| `sandboxLifecycleRateLimit` | number | Yes |  |
| `authenticatedRateLimitTtlSeconds` | number | Yes |  |
| `sandboxCreateRateLimitTtlSeconds` | number | Yes |  |
| `sandboxLifecycleRateLimitTtlSeconds` | number | Yes |  |
| `snapshotDeactivationTimeoutMinutes` | number | Yes | Time in minutes before an unused snapshot is deactivated |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Organization quota updated successfully |  |

---

## PATCH `/organizations/{organizationId}/quota/{regionId}` {#daytona/tag/organizations/PATCH/organizations/{organizationId}/quota/{regionId}}

**Update organization region quota**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |
| `regionId` | path | string | Yes | ID of the region where the updated quota will be applied |

### Request Body

Schema: **UpdateOrganizationRegionQuota**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sandboxClass` | object | No |  |
| `totalCpuQuota` | number | Yes |  |
| `totalMemoryQuota` | number | Yes |  |
| `totalDiskQuota` | number | Yes |  |
| `totalGpuQuota` | number | Yes |  |
| `allowedGpuTypes` | array of [GpuType](#schema-gputype) | No |  |
| `maxCpuPerSandbox` | number | No |  |
| `maxMemoryPerSandbox` | number | No |  |
| `maxDiskPerSandbox` | number | No |  |
| `maxDiskPerNonEphemeralSandbox` | number | No |  |
| `maxCpuPerGpuSandbox` | number | No |  |
| `maxMemoryPerGpuSandbox` | number | No |  |
| `maxDiskPerGpuSandbox` | number | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Region quota updated successfully |  |

---

## POST `/organizations/{organizationId}/leave` {#daytona/tag/organizations/POST/organizations/{organizationId}/leave}

**Leave organization**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Organization left successfully |  |

---

## POST `/organizations/{organizationId}/suspend` {#daytona/tag/organizations/POST/organizations/{organizationId}/suspend}

**Suspend organization**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Request Body

Schema: **OrganizationSuspension**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `reason` | string | Yes | Suspension reason |
| `until` | string (date-time) | Yes | Suspension until |
| `suspensionCleanupGracePeriodHours` | number | No | Suspension cleanup grace period hours |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Organization suspended successfully |  |

---

## POST `/organizations/{organizationId}/unsuspend` {#daytona/tag/organizations/POST/organizations/{organizationId}/unsuspend}

**Unsuspend organization**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Organization unsuspended successfully |  |

---

## GET `/organizations/otel-config/by-sandbox-auth-token/{authToken}` {#daytona/tag/organizations/GET/organizations/otel-config/by-sandbox-auth-token/{authToken}}

**Get organization OTEL config by sandbox auth token**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `authToken` | path | string | Yes | Sandbox Auth Token |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OTEL Config | OtelConfig |

---

## GET `/organizations/{organizationId}/otel-config` {#daytona/tag/organizations/GET/organizations/{organizationId}/otel-config}

**Get organization OTEL config by organization ID**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OTEL Config | OtelConfig |

---

## PUT `/organizations/{organizationId}/otel-config` {#daytona/tag/organizations/PUT/organizations/{organizationId}/otel-config}

**Update organization OpenTelemetry configuration**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Request Body

Schema: **OtelConfig**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `endpoint` | string | Yes | Endpoint |
| `headers` | object | No | Headers |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | OpenTelemetry configuration updated successfully |  |

---

## DELETE `/organizations/{organizationId}/otel-config` {#daytona/tag/organizations/DELETE/organizations/{organizationId}/otel-config}

**Delete organization OpenTelemetry configuration**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | OpenTelemetry configuration deleted successfully |  |

---

## POST `/organizations/{organizationId}/sandbox-default-limited-network-egress` {#daytona/tag/organizations/POST/organizations/{organizationId}/sandbox-default-limited-network-egress}

**Update sandbox default limited network egress**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Request Body

Schema: **OrganizationSandboxDefaultLimitedNetworkEgress**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sandboxDefaultLimitedNetworkEgress` | boolean | Yes | Sandbox default limited network egress |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Sandbox default limited network egress updated successfully |  |

---

## PUT `/organizations/{organizationId}/experimental-config` {#daytona/tag/organizations/PUT/organizations/{organizationId}/experimental-config}

**Update experimental configuration**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Request Body

Experimental configuration as a JSON object. Set to null to clear the configuration.


### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 |  |  |

---

## GET `/organizations/{organizationId}/roles` {#daytona/tag/organizations/GET/organizations/{organizationId}/roles}

**List organization roles**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | List of organization roles | array of OrganizationRole |

---

## POST `/organizations/{organizationId}/roles` {#daytona/tag/organizations/POST/organizations/{organizationId}/roles}

**Create organization role**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Request Body

Schema: **CreateOrganizationRole**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | The name of the role |
| `description` | string | Yes | The description of the role |
| `permissions` | array of string | Yes | The list of permissions assigned to the role |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 201 | Organization role created successfully | OrganizationRole |

---

## PUT `/organizations/{organizationId}/roles/{roleId}` {#daytona/tag/organizations/PUT/organizations/{organizationId}/roles/{roleId}}

**Update organization role**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |
| `roleId` | path | string | Yes | Role ID |

### Request Body

Schema: **UpdateOrganizationRole**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | The name of the role |
| `description` | string | Yes | The description of the role |
| `permissions` | array of string | Yes | The list of permissions assigned to the role |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Role updated successfully | OrganizationRole |

---

## DELETE `/organizations/{organizationId}/roles/{roleId}` {#daytona/tag/organizations/DELETE/organizations/{organizationId}/roles/{roleId}}

**Delete organization role**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |
| `roleId` | path | string | Yes | Role ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Organization role deleted successfully |  |

---

## GET `/organizations/{organizationId}/users` {#daytona/tag/organizations/GET/organizations/{organizationId}/users}

**List organization members**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | List of organization members | array of OrganizationUser |

---

## POST `/organizations/{organizationId}/users/{userId}/access` {#daytona/tag/organizations/POST/organizations/{organizationId}/users/{userId}/access}

**Update access for organization member**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |
| `userId` | path | string | Yes | User ID |

### Request Body

Schema: **UpdateOrganizationMemberAccess**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | Organization member role |
| `assignedRoleIds` | array of string | Yes | Array of assigned role IDs |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Access updated successfully | OrganizationUser |

---

## DELETE `/organizations/{organizationId}/users/{userId}` {#daytona/tag/organizations/DELETE/organizations/{organizationId}/users/{userId}}

**Delete organization member**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |
| `userId` | path | string | Yes | User ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | User removed from organization successfully |  |

---

## GET `/organizations/{organizationId}/invitations` {#daytona/tag/organizations/GET/organizations/{organizationId}/invitations}

**List pending organization invitations**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | List of pending organization invitations | array of OrganizationInvitation |

---

## POST `/organizations/{organizationId}/invitations` {#daytona/tag/organizations/POST/organizations/{organizationId}/invitations}

**Create organization invitation**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |

### Request Body

Schema: **CreateOrganizationInvitation**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | Email address of the invitee |
| `role` | string | Yes | Organization member role for the invitee |
| `assignedRoleIds` | array of string | Yes | Array of assigned role IDs for the invitee |
| `expiresAt` | string (date-time) | No | Expiration date of the invitation |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 201 | Organization invitation created successfully | OrganizationInvitation |

---

## PUT `/organizations/{organizationId}/invitations/{invitationId}` {#daytona/tag/organizations/PUT/organizations/{organizationId}/invitations/{invitationId}}

**Update organization invitation**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |
| `invitationId` | path | string | Yes | Invitation ID |

### Request Body

Schema: **UpdateOrganizationInvitation**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | Organization member role |
| `assignedRoleIds` | array of string | Yes | Array of role IDs |
| `expiresAt` | string (date-time) | No | Expiration date of the invitation |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Organization invitation updated successfully | OrganizationInvitation |

---

## POST `/organizations/{organizationId}/invitations/{invitationId}/cancel` {#daytona/tag/organizations/POST/organizations/{organizationId}/invitations/{invitationId}/cancel}

**Cancel organization invitation**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `organizationId` | path | string | Yes | Organization ID |
| `invitationId` | path | string | Yes | Invitation ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Organization invitation cancelled successfully |  |

---

## GET `/regions` {#daytona/tag/organizations/GET/regions}

**List all available regions for the organization**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | List of all available regions | array of Region |

---

## POST `/regions` {#daytona/tag/organizations/POST/regions}

**Create a new region**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |

### Request Body

Schema: **CreateRegion**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Region name |
| `proxyUrl` | string | No | Proxy URL for the region |
| `sshGatewayUrl` | string | No | SSH Gateway URL for the region |
| `snapshotManagerUrl` | string | No | Snapshot Manager URL for the region |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 201 | The region has been successfully created. | CreateRegionResponse |

---

## GET `/regions/{id}` {#daytona/tag/organizations/GET/regions/{id}}

**Get region by ID**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `id` | path | string | Yes | Region ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 |  | Region |

---

## PATCH `/regions/{id}` {#daytona/tag/organizations/PATCH/regions/{id}}

**Update region configuration**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `id` | path | string | Yes | Region ID |

### Request Body

Schema: **UpdateRegion**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `proxyUrl` | string | No | Proxy URL for the region |
| `sshGatewayUrl` | string | No | SSH Gateway URL for the region |
| `snapshotManagerUrl` | string | No | Snapshot Manager URL for the region |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 |  |  |

---

## DELETE `/regions/{id}` {#daytona/tag/organizations/DELETE/regions/{id}}

**Delete a region**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `id` | path | string | Yes | Region ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | The region has been successfully deleted. |  |

---

## POST `/regions/{id}/regenerate-proxy-api-key` {#daytona/tag/organizations/POST/regions/{id}/regenerate-proxy-api-key}

**Regenerate proxy API key for a region**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `id` | path | string | Yes | Region ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | The proxy API key has been successfully regenerated. | RegenerateApiKeyResponse |

---

## POST `/regions/{id}/regenerate-ssh-gateway-api-key` {#daytona/tag/organizations/POST/regions/{id}/regenerate-ssh-gateway-api-key}

**Regenerate SSH gateway API key for a region**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `id` | path | string | Yes | Region ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | The SSH gateway API key has been successfully regenerated. | RegenerateApiKeyResponse |

---

## POST `/regions/{id}/regenerate-snapshot-manager-credentials` {#daytona/tag/organizations/POST/regions/{id}/regenerate-snapshot-manager-credentials}

**Regenerate snapshot manager credentials for a region**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `id` | path | string | Yes | Region ID |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | The snapshot manager credentials have been successfully regenerated. | SnapshotManagerCredentials |

---
