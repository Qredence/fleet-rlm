# Git API


## Contents

- POST `/git/add`
- GET `/git/branches`
- POST `/git/branches`
- DELETE `/git/branches`
- POST `/git/checkout`
- POST `/git/clone`
- POST `/git/commit`
- GET `/git/history`
- POST `/git/pull`
- POST `/git/push`
- GET `/git/status`

## POST `/git/add` {#daytona-toolbox/tag/git/POST/git/add}

**Add files to Git staging**

Add files to the Git staging area

### Request Body

Add files request

Schema: **GitAddRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `files` | array of string | Yes | files to add (use . for all files) |
| `path` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## GET `/git/branches` {#daytona-toolbox/tag/git/GET/git/branches}

**List branches**

Get a list of all branches in the Git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | Repository path |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | ListBranchResponse |

---

## POST `/git/branches` {#daytona-toolbox/tag/git/POST/git/branches}

**Create a new branch**

Create a new branch in the Git repository

### Request Body

Create branch request

Schema: **GitBranchRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes |  |
| `path` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 201 | Created |  |

---

## DELETE `/git/branches` {#daytona-toolbox/tag/git/DELETE/git/branches}

**Delete a branch**

Delete a branch from the Git repository

### Request Body

Delete branch request

Schema: **GitDeleteBranchRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes |  |
| `path` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | No Content |  |

---

## POST `/git/checkout` {#daytona-toolbox/tag/git/POST/git/checkout}

**Checkout branch or commit**

Switch to a different branch or commit in the Git repository

### Request Body

Checkout request

Schema: **GitCheckoutRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `branch` | string | Yes |  |
| `path` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## POST `/git/clone` {#daytona-toolbox/tag/git/POST/git/clone}

**Clone a Git repository**

Clone a Git repository to the specified path. Defaults to strict TLS verification; set insecure_skip_tls=true to skip verification for self-signed or private-CA Git servers.

### Request Body

Clone repository request

Schema: **GitCloneRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `branch` | string | No |  |
| `commit_id` | string | No |  |
| `insecure_skip_tls` | boolean | No | Skip TLS certificate verification for this clone. Defaults to false (verify). Set to true ONLY for trusted internal Git servers with self-signed or private-CA certs; credentials, if supplied, will be transmitted over an unverified TLS connection and are exposed to any MITM on the route. |
| `password` | string | No |  |
| `path` | string | Yes |  |
| `url` | string | Yes |  |
| `username` | string | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## POST `/git/commit` {#daytona-toolbox/tag/git/POST/git/commit}

**Commit changes**

Commit staged changes to the Git repository

### Request Body

Commit request

Schema: **GitCommitRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `allow_empty` | boolean | No |  |
| `author` | string | Yes |  |
| `email` | string | Yes |  |
| `message` | string | Yes |  |
| `path` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | GitCommitResponse |

---

## GET `/git/history` {#daytona-toolbox/tag/git/GET/git/history}

**Get commit history**

Get the commit history of the Git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | Repository path |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | array of GitCommitInfo |

---

## POST `/git/pull` {#daytona-toolbox/tag/git/POST/git/pull}

**Pull changes from remote**

Pull changes from the remote Git repository

### Request Body

Pull request

Schema: **GitRepoRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `password` | string | No |  |
| `path` | string | Yes |  |
| `username` | string | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## POST `/git/push` {#daytona-toolbox/tag/git/POST/git/push}

**Push changes to remote**

Push local changes to the remote Git repository

### Request Body

Push request

Schema: **GitRepoRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `password` | string | No |  |
| `path` | string | Yes |  |
| `username` | string | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## GET `/git/status` {#daytona-toolbox/tag/git/GET/git/status}

**Get Git status**

Get the Git status of the repository at the specified path

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | Repository path |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | GitStatus |

---
