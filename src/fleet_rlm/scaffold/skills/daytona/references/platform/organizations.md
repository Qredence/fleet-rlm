## Contents

- Personal vs Collaborative
- Create organization
- List organizations
- Leave organization
- Delete organization
- Roles
- Members
- Invitations
- Regions
- Organization settings
- Advanced operations
- See Also




Daytona provides organizations as a way to group resources and enable collaboration. Users can work individually in their personal organization or together in a collaborative organization.

Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard) to manage your organizations, or use the [API](../api/README.md#daytona/tag/organizations) to manage them programmatically.

## Personal vs Collaborative

Every Daytona user starts with a personal organization, ideal for solo use and experimentation. Collaborative organizations are created manually and designed for company-wide collaboration with shared access and controls.

| **Feature**        | **Personal organization**        | **Collaborative organization**                 |
| ------------------ | -------------------------------- | ---------------------------------------------- |
| **Creation**       | Automatic on signup              | Manually by a user                             |
| **Members**        | Single user only                 | Multiple users (invite-based)                  |
| **Access Control** | No roles or permissions          | Roles with granular resource-based assignments |
| **Billing**        | Tied to individual user          | Shared across team members                     |
| **Use Case**       | Personal testing, small projects | Company/team development and production        |
| **Quota Scope**    | Per user                         | Shared across all members                      |
| **Deletable**      | No                               | Yes (by Owner)                                 |

Users can switch between their personal and collaborative organizations by using the dropdown in the [Daytona Dashboard ↗](https://app.daytona.io/dashboard) sidebar. Each organization has its own sandboxes, API keys, and resource quotas.

## Create organization

Daytona provides options to create organizations in [Daytona Dashboard ↗](https://app.daytona.io/dashboard/) or programmatically using the [API](../api/README.md#daytona/tag/organizations).

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/)
2. Expand the dropdown at the top-left corner of the sidebar to view your organizations
3. Click the **Create Organization** button
4. Enter the organization name
5. Select a [region](../python-sdk/regions.md)
6. Click **Create** to create the organization

**API:**

```bash
curl 'https://app.daytona.io/api/organizations' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "name": "My Organization",
  "defaultRegionId": "us"
}'
```

## List organizations

Daytona provides methods to list all organizations the authenticated user belongs to.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations' \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### Get by ID

Daytona provides a method to get an organization by ID.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID' \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

## Leave organization

Daytona provides options to leave an organization in [Daytona Dashboard ↗](https://app.daytona.io/dashboard/) or programmatically using the [API](../api/README.md#daytona/tag/organizations).

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/)
2. Expand the dropdown at the top-left corner of the sidebar to view your organizations
3. Select the organization you want to leave
4. Click **Settings** in the sidebar
5. Click **Leave Organization**
6. Confirm by clicking the **Leave** button

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/leave' \
  --request POST \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

## Delete organization

Daytona provides options to delete an organization in [Daytona Dashboard ↗](https://app.daytona.io/dashboard/) or programmatically using the [API](../api/README.md#daytona/tag/organizations).

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/)
2. Expand the dropdown at the top-left corner of the sidebar to view your organizations
3. Select the organization you want to delete
4. Click **Settings** in the sidebar
5. Click **Delete Organization**
6. Confirm the deletion by typing the organization name and clicking the **Delete** button

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID' \
  --request DELETE \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

## Roles

Users within an organization can have one of two different roles:

1. **Owners** have full administrative access to the organization and its resources. Organization owners can perform administrative actions.
2. **Members** have no administrative access to the organization, while their access to organization resources is based on [**Assignments**](#role-assignments).

### Role assignments

The list of available role assignments includes:

| Assignment                    | Description                                                         |
| ----------------------------- | ------------------------------------------------------------------- |
| **`Viewer (required)`**       | Grants read access to all resources in the organization             |
| **`Developer`**               | Grants the ability to create sandboxes and keys in the organization |
| **`Sandboxes Admin`**         | Grants admin access to sandboxes in the organization                |
| **`Snapshots Admin`**         | Grants admin access to snapshots in the organization                |
| **`Registries Admin`**        | Grants admin access to registries in the organization               |
| **`Volumes Admin`**           | Grants admin access to volumes in the organization                  |
| **`Super Admin`**             | Grants full access to all resources in the organization             |
| **`Auditor`**                 | Grants access to audit logs in the organization                     |
| **`Infrastructure Admin`**    | Grants admin access to infrastructure in the organization           |

### Create role

Daytona provides a method to create a new role in an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/roles' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "name": "Maintainer",
  "description": "Can manage all resources",
  "permissions": ["write:sandboxes", "delete:sandboxes"]
}'
```

### List roles

Daytona provides a method to list all roles in an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/roles' \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### Update role

Daytona provides a method to update a role in an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/roles/ROLE_ID' \
  --request PUT \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "name": "Maintainer",
  "description": "Can manage all resources",
  "permissions": ["write:sandboxes", "delete:sandboxes"]
}'
```

### Delete role

Daytona provides a method to delete a role in an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/roles/ROLE_ID' \
  --request DELETE \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

## Members

Daytona provides methods to manage members in an organization.

### List members

Daytona provides a method to list all members in an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/users' \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### Invite members

Daytona provides a method to invite a new user to an organization.

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/members)
2. Click the **Invite Member** button
3. Enter the email address of the user you want to invite
4. [Select a role](#roles) for the new user. If you select the **`Member`** role, define their [assignments](#role-assignments)

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/invitations' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "email": "mail@example.com",
  "role": "member",
  "assignedRoleIds": ["00000000-0000-0000-0000-000000000001"]
}'
```

### Remove members

Daytona provides a method to remove a user from an organization.

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/members)
2. Click the **Remove** button next to the user you want to remove
3. Confirm the removal by clicking the **Remove** button

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/users/USER_ID' \
  --request DELETE \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### Update access

Daytona provides options to update the access of a member in an organization in [Daytona Dashboard ↗](https://app.daytona.io/dashboard/members) or programmatically using the [API](../api/README.md#daytona/tag/organizations).

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/members)
2. Click the three-dot menu on the member row
3. Click **Change Role** or **Manage Assignments**
4. Update the role or assignments and click **Save**

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/users/USER_ID/access' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "role": "member",
  "assignedRoleIds": ["00000000-0000-0000-0000-000000000001"]
}'
```

## Invitations

Daytona provides methods to manage invitations in an organization.

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/user/invitations)
2. Expand the dropdown at the bottom of the sidebar to view pending invitations to join other organizations.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/invitations' \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### Get invitations count

Daytona provides a method to get the number of invitations in an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/invitations/count' \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### Accept invitation

Daytona provides options to accept a pending organization invitation in [Daytona Dashboard ↗](https://app.daytona.io/dashboard/user/invitations) or programmatically using the [API](../api/README.md#daytona/tag/organizations).

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/)
2. Click your profile at the bottom-left of the sidebar
3. Click **Invitations**
4. Click the checkmark button on the invitation row

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/invitations/INVITATION_ID/accept' \
  --request POST \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

Once a user accepts an invitation to join an organization, they get access to resource quotas assigned to that organization and they may proceed by issuing a new [API key](../../SKILL.md#authentication) and creating sandboxes.

### Decline invitation

Daytona provides options to decline a pending organization invitation in [Daytona Dashboard ↗](https://app.daytona.io/dashboard/user/invitations) or programmatically using the [API](../api/README.md#daytona/tag/organizations).

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/)
2. Click your profile at the bottom-left of the sidebar
3. Click **Invitations**
4. Click the X button on the invitation row
5. Confirm by clicking the **Decline** button

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/invitations/INVITATION_ID/decline' \
  --request POST \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### List pending

Daytona provides a method to list pending invitations for an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/invitations' \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### Update invitation

Daytona provides options to update an invitation for an organization in [Daytona Dashboard ↗](https://app.daytona.io/dashboard/members) or programmatically using the [API](../api/README.md#daytona/tag/organizations).

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/members)
2. Scroll to the **Invitations** table
3. Click the three-dot menu on the invitation row
4. Click **Edit**
5. Update the role or assignments and click **Update**

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/invitations/INVITATION_ID' \
  --request PUT \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "role": "member",
  "assignedRoleIds": ["00000000-0000-0000-0000-000000000001"],
  "expiresAt": "2030-01-01T00:00:00.000Z"
}'
```

### Cancel invitation

Daytona provides options to cancel an invitation for an organization in [Daytona Dashboard ↗](https://app.daytona.io/dashboard/members) or programmatically using the [API](../api/README.md#daytona/tag/organizations).

1. Navigate to [Daytona Dashboard ↗](https://app.daytona.io/dashboard/members)
2. Scroll to the **Invitations** table
3. Click the three-dot menu on the invitation row
4. Click **Cancel**
5. Confirm by clicking the **Confirm** button

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/invitations/INVITATION_ID/cancel' \
  --request POST \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

## Regions

Each organization has a default [region](../python-sdk/regions.md) that determines where sandboxes are created when no specific target is provided. Regions represent geographic or logical groupings of compute infrastructure. Organizations can update their default region, manage per-region resource quotas, and query region quota information for individual sandboxes.

For more information on available region types, see the [Regions](../python-sdk/regions.md) guide.

### Set default region

Daytona provides options to set the default region programmatically using the [API](../api/README.md#daytona/tag/organizations).

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/default-region' \
  --request PATCH \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "defaultRegionId": "us"
}'
```

## Organization settings

The settings page in the [Daytona Dashboard ↗](https://app.daytona.io/dashboard/settings) allows you to view the organization ID and name, and optionally delete the organization if you don't need it anymore. This action is irreversible, so please proceed with caution. Personal organizations are there by default and cannot be deleted.

## Advanced operations

Daytona provides methods to perform advanced operations on an organization.

### Usage overview

Daytona provides a method to get the usage overview for an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/usage' \
  --header 'Authorization: Bearer YOUR_API_KEY'
```

### Update sandbox default limited network egress

Daytona provides a method to update the sandbox default limited network egress for an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/sandbox-default-limited-network-egress' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "sandboxDefaultLimitedNetworkEgress": true
}'
```

### Update experimental configuration

Daytona provides a method to update the experimental configuration for an organization.

**API:**

```bash
curl 'https://app.daytona.io/api/organizations/ORGANIZATION_ID/experimental-config' \
  --request PUT \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --data '{
  "otel": {
    "endpoint": "http://otel-collector:4317",
    "headers": {
      "api-key": "XXX"
    }
  }
}'
```

## See Also

- [Python SDK](../python-sdk/README.md)
- [TypeScript SDK](../typescript-sdk/README.md)
- [Go SDK](../go-sdk/README.md)
- [Ruby SDK](../ruby-sdk/README.md)
