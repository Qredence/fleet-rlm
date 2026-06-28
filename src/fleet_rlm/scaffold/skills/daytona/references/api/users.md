# Users API

## GET `/users/me` {#daytona/tag/users/GET/users/me}

**Get authenticated user**

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | User details | User |

---

## GET `/users/account-providers` {#daytona/tag/users/GET/users/account-providers}

**Get available account providers**

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Available account providers | array of AccountProvider |

---

## POST `/users/linked-accounts` {#daytona/tag/users/POST/users/linked-accounts}

**Link account**

### Request Body

Schema: **CreateLinkedAccount**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | Yes | The authentication provider of the secondary account |
| `userId` | string | Yes | The user ID of the secondary account |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Account linked successfully |  |

---

## DELETE `/users/linked-accounts/{provider}/{providerUserId}` {#daytona/tag/users/DELETE/users/linked-accounts/{provider}/{providerUserId}}

**Unlink account**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `provider` | path | string | Yes |  |
| `providerUserId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | Account unlinked successfully |  |

---

## POST `/users/mfa/sms/enroll` {#daytona/tag/users/POST/users/mfa/sms/enroll}

**Enroll in SMS MFA**

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | SMS MFA enrollment URL | string |

---
