# Toolbox API


## Contents

- GET `/toolbox/{sandboxId}/toolbox/project-dir`/toolbox/project-dir}
- GET `/toolbox/{sandboxId}/toolbox/user-home-dir`/toolbox/user-home-dir}
- GET `/toolbox/{sandboxId}/toolbox/work-dir`/toolbox/work-dir}
- GET `/toolbox/{sandboxId}/toolbox/files`/toolbox/files}
- DELETE `/toolbox/{sandboxId}/toolbox/files`/toolbox/files}
- GET `/toolbox/{sandboxId}/toolbox/files/download`/toolbox/files/download}
- POST `/toolbox/{sandboxId}/toolbox/files/bulk-download`/toolbox/files/bulk-download}
- GET `/toolbox/{sandboxId}/toolbox/files/find`/toolbox/files/find}
- POST `/toolbox/{sandboxId}/toolbox/files/folder`/toolbox/files/folder}
- GET `/toolbox/{sandboxId}/toolbox/files/info`/toolbox/files/info}
- POST `/toolbox/{sandboxId}/toolbox/files/move`/toolbox/files/move}
- POST `/toolbox/{sandboxId}/toolbox/files/permissions`/toolbox/files/permissions}
- POST `/toolbox/{sandboxId}/toolbox/files/replace`/toolbox/files/replace}
- GET `/toolbox/{sandboxId}/toolbox/files/search`/toolbox/files/search}
- POST `/toolbox/{sandboxId}/toolbox/files/upload`/toolbox/files/upload}
- POST `/toolbox/{sandboxId}/toolbox/files/bulk-upload`/toolbox/files/bulk-upload}
- POST `/toolbox/{sandboxId}/toolbox/git/add`/toolbox/git/add}
- GET `/toolbox/{sandboxId}/toolbox/git/branches`/toolbox/git/branches}
- POST `/toolbox/{sandboxId}/toolbox/git/branches`/toolbox/git/branches}
- DELETE `/toolbox/{sandboxId}/toolbox/git/branches`/toolbox/git/branches}
- POST `/toolbox/{sandboxId}/toolbox/git/clone`/toolbox/git/clone}
- POST `/toolbox/{sandboxId}/toolbox/git/commit`/toolbox/git/commit}
- GET `/toolbox/{sandboxId}/toolbox/git/history`/toolbox/git/history}
- POST `/toolbox/{sandboxId}/toolbox/git/pull`/toolbox/git/pull}
- POST `/toolbox/{sandboxId}/toolbox/git/push`/toolbox/git/push}
- POST `/toolbox/{sandboxId}/toolbox/git/checkout`/toolbox/git/checkout}
- GET `/toolbox/{sandboxId}/toolbox/git/status`/toolbox/git/status}
- POST `/toolbox/{sandboxId}/toolbox/process/execute`/toolbox/process/execute}
- GET `/toolbox/{sandboxId}/toolbox/process/session`/toolbox/process/session}
- POST `/toolbox/{sandboxId}/toolbox/process/session`/toolbox/process/session}
- GET `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}`/toolbox/process/session/{sessionId}}
- DELETE `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}`/toolbox/process/session/{sessionId}}
- POST `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/exec`/toolbox/process/session/{sessionId}/exec}
- GET `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/command/{commandId}`/toolbox/process/session/{sessionId}/command/{commandId}}
- GET `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/command/{commandId}/logs`/toolbox/process/session/{sessionId}/command/{commandId}/logs}
- GET `/toolbox/{sandboxId}/toolbox/process/pty`/toolbox/process/pty}
- POST `/toolbox/{sandboxId}/toolbox/process/pty`/toolbox/process/pty}
- GET `/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}`/toolbox/process/pty/{sessionId}}
- DELETE `/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}`/toolbox/process/pty/{sessionId}}
- POST `/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}/resize`/toolbox/process/pty/{sessionId}/resize}
- POST `/toolbox/{sandboxId}/toolbox/lsp/completions`/toolbox/lsp/completions}
- POST `/toolbox/{sandboxId}/toolbox/lsp/did-close`/toolbox/lsp/did-close}
- POST `/toolbox/{sandboxId}/toolbox/lsp/did-open`/toolbox/lsp/did-open}
- GET `/toolbox/{sandboxId}/toolbox/lsp/document-symbols`/toolbox/lsp/document-symbols}
- POST `/toolbox/{sandboxId}/toolbox/lsp/start`/toolbox/lsp/start}
- POST `/toolbox/{sandboxId}/toolbox/lsp/stop`/toolbox/lsp/stop}
- GET `/toolbox/{sandboxId}/toolbox/lsp/workspace-symbols`/toolbox/lsp/workspace-symbols}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/start`/toolbox/computeruse/start}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/stop`/toolbox/computeruse/stop}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/status`/toolbox/computeruse/status}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/status`/toolbox/computeruse/process/{processName}/status}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/restart`/toolbox/computeruse/process/{processName}/restart}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/logs`/toolbox/computeruse/process/{processName}/logs}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/errors`/toolbox/computeruse/process/{processName}/errors}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/mouse/position`/toolbox/computeruse/mouse/position}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/mouse/move`/toolbox/computeruse/mouse/move}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/mouse/click`/toolbox/computeruse/mouse/click}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/mouse/drag`/toolbox/computeruse/mouse/drag}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/mouse/scroll`/toolbox/computeruse/mouse/scroll}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/keyboard/type`/toolbox/computeruse/keyboard/type}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/keyboard/key`/toolbox/computeruse/keyboard/key}
- POST `/toolbox/{sandboxId}/toolbox/computeruse/keyboard/hotkey`/toolbox/computeruse/keyboard/hotkey}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/screenshot`/toolbox/computeruse/screenshot}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/screenshot/region`/toolbox/computeruse/screenshot/region}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/screenshot/compressed`/toolbox/computeruse/screenshot/compressed}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/screenshot/region/compressed`/toolbox/computeruse/screenshot/region/compressed}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/display/info`/toolbox/computeruse/display/info}
- GET `/toolbox/{sandboxId}/toolbox/computeruse/display/windows`/toolbox/computeruse/display/windows}

## GET `/toolbox/{sandboxId}/toolbox/project-dir` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/project-dir}

**[DEPRECATED] Get sandbox project dir**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Project directory retrieved successfully | ProjectDirResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/user-home-dir` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/user-home-dir}

**[DEPRECATED] Get sandbox user home dir**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | User home directory retrieved successfully | UserHomeDirResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/work-dir` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/work-dir}

**[DEPRECATED] Get sandbox work-dir**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Work-dir retrieved successfully | WorkDirResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/files` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/files}

**[DEPRECATED] List files**

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Files listed successfully | array of FileInfo |

---

## DELETE `/toolbox/{sandboxId}/toolbox/files` {#daytona/tag/toolbox/DELETE/toolbox/{sandboxId}/toolbox/files}

**[DEPRECATED] Delete file**

Delete file inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `recursive` | query | boolean | No |  |
| `path` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | File deleted successfully |  |

---

## GET `/toolbox/{sandboxId}/toolbox/files/download` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/files/download}

**[DEPRECATED] Download file**

Download file from sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | File downloaded successfully | string |

---

## POST `/toolbox/{sandboxId}/toolbox/files/bulk-download` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/files/bulk-download}

**[DEPRECATED] Download multiple files**

Streams back a multipart/form-data bundle of the requested paths

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **DownloadFiles**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `paths` | array of string | Yes | List of remote file paths to download |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | A multipart/form-data response with each file as a part | string |

---

## GET `/toolbox/{sandboxId}/toolbox/files/find` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/files/find}

**[DEPRECATED] Search for text/pattern in files**

Search for text/pattern inside sandbox files

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |
| `pattern` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Search completed successfully | array of Match |

---

## POST `/toolbox/{sandboxId}/toolbox/files/folder` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/files/folder}

**[DEPRECATED] Create folder**

Create folder inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |
| `mode` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Folder created successfully |  |

---

## GET `/toolbox/{sandboxId}/toolbox/files/info` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/files/info}

**[DEPRECATED] Get file info**

Get file info inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | File info retrieved successfully | FileInfo |

---

## POST `/toolbox/{sandboxId}/toolbox/files/move` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/files/move}

**[DEPRECATED] Move file**

Move file inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `source` | query | string | Yes |  |
| `destination` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | File moved successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/files/permissions` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/files/permissions}

**[DEPRECATED] Set file permissions**

Set file owner/group/permissions inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |
| `owner` | query | string | No |  |
| `group` | query | string | No |  |
| `mode` | query | string | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | File permissions updated successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/files/replace` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/files/replace}

**[DEPRECATED] Replace in files**

Replace text/pattern in multiple files inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **ReplaceRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `files` | array of string | Yes |  |
| `pattern` | string | Yes |  |
| `newValue` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Text replaced successfully | array of ReplaceResult |

---

## GET `/toolbox/{sandboxId}/toolbox/files/search` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/files/search}

**[DEPRECATED] Search files**

Search for files inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |
| `pattern` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Search completed successfully | SearchFilesResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/files/upload` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/files/upload}

**[DEPRECATED] Upload file**

Upload file inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |

### Request Body

Schema: **uploadFile_deprecated_request**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | string (binary) | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | File uploaded successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/files/bulk-upload` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/files/bulk-upload}

**[DEPRECATED] Upload multiple files**

Upload multiple files inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: array of **UploadFile**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | string (binary) | Yes |  |
| `path` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Files uploaded successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/git/add` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/git/add}

**[DEPRECATED] Add files**

Add files to git commit

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **GitAddRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes |  |
| `files` | array of string | Yes | files to add (use . for all files) |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Files added to git successfully |  |

---

## GET `/toolbox/{sandboxId}/toolbox/git/branches` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/git/branches}

**[DEPRECATED] Get branch list**

Get branch list from git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Branch list retrieved successfully | ListBranchResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/git/branches` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/git/branches}

**[DEPRECATED] Create branch**

Create branch on git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **GitBranchRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes |  |
| `name` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Branch created successfully |  |

---

## DELETE `/toolbox/{sandboxId}/toolbox/git/branches` {#daytona/tag/toolbox/DELETE/toolbox/{sandboxId}/toolbox/git/branches}

**[DEPRECATED] Delete branch**

Delete branch on git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **GitDeleteBranchRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes |  |
| `name` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Branch deleted successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/git/clone` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/git/clone}

**[DEPRECATED] Clone repository**

Clone git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **GitCloneRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes |  |
| `path` | string | Yes |  |
| `username` | string | No |  |
| `password` | string | No |  |
| `branch` | string | No |  |
| `commit_id` | string | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Repository cloned successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/git/commit` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/git/commit}

**[DEPRECATED] Commit changes**

Commit changes to git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **GitCommitRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes |  |
| `message` | string | Yes |  |
| `author` | string | Yes |  |
| `email` | string | Yes |  |
| `allow_empty` | boolean | No | Allow creating an empty commit when no changes are staged |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Changes committed successfully | GitCommitResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/git/history` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/git/history}

**[DEPRECATED] Get commit history**

Get commit history from git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Commit history retrieved successfully | array of GitCommitInfo |

---

## POST `/toolbox/{sandboxId}/toolbox/git/pull` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/git/pull}

**[DEPRECATED] Pull changes**

Pull changes from remote

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **GitRepoRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes |  |
| `username` | string | No |  |
| `password` | string | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Changes pulled successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/git/push` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/git/push}

**[DEPRECATED] Push changes**

Push changes to remote

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **GitRepoRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes |  |
| `username` | string | No |  |
| `password` | string | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Changes pushed successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/git/checkout` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/git/checkout}

**[DEPRECATED] Checkout branch**

Checkout branch or commit in git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **GitCheckoutRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes |  |
| `branch` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Branch checked out successfully |  |

---

## GET `/toolbox/{sandboxId}/toolbox/git/status` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/git/status}

**[DEPRECATED] Get git status**

Get status from git repository

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `path` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Git status retrieved successfully | GitStatus |

---

## POST `/toolbox/{sandboxId}/toolbox/process/execute` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/process/execute}

**[DEPRECATED] Execute command**

Execute command synchronously inside sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **ExecuteRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command` | string | Yes |  |
| `cwd` | string | No | Current working directory |
| `timeout` | number | No | Timeout in seconds, defaults to 10 seconds |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Command executed successfully | ExecuteResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/process/session` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/process/session}

**[DEPRECATED] List sessions**

List all active sessions in the sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Sessions retrieved successfully | array of Session |

---

## POST `/toolbox/{sandboxId}/toolbox/process/session` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/process/session}

**[DEPRECATED] Create session**

Create a new session in the sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **CreateSessionRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sessionId` | string | Yes | The ID of the session |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 |  |  |

---

## GET `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/process/session/{sessionId}}

**[DEPRECATED] Get session**

Get session by ID

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `sessionId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Session retrieved successfully | Session |

---

## DELETE `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}` {#daytona/tag/toolbox/DELETE/toolbox/{sandboxId}/toolbox/process/session/{sessionId}}

**[DEPRECATED] Delete session**

Delete a specific session

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `sessionId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Session deleted successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/exec` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/exec}

**[DEPRECATED] Execute command in session**

Execute a command in a specific session

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `sessionId` | path | string | Yes |  |

### Request Body

Schema: **SessionExecuteRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command` | string | Yes | The command to execute |
| `runAsync` | boolean | No | Whether to execute the command asynchronously |
| `async` | boolean | No | Deprecated: Use runAsync instead. Whether to execute the command asynchronously |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Command executed successfully | SessionExecuteResponse |
| 202 | Command accepted and is being processed | SessionExecuteResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/command/{commandId}` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/command/{commandId}}

**[DEPRECATED] Get session command**

Get session command by ID

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `sessionId` | path | string | Yes |  |
| `commandId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Session command retrieved successfully | Command |

---

## GET `/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/command/{commandId}/logs` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/process/session/{sessionId}/command/{commandId}/logs}

**[DEPRECATED] Get command logs**

Get logs for a specific command in a session

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `sessionId` | path | string | Yes |  |
| `commandId` | path | string | Yes |  |
| `follow` | query | boolean | No | Whether to stream the logs |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Command log stream marked with stdout and stderr prefixes | string |

---

## GET `/toolbox/{sandboxId}/toolbox/process/pty` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/process/pty}

**[DEPRECATED] List PTY sessions**

List all active PTY sessions in the sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | PTY sessions retrieved successfully | PtyListResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/process/pty` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/process/pty}

**[DEPRECATED] Create PTY session**

Create a new PTY session in the sandbox

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **PtyCreateRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | The unique identifier for the PTY session |
| `cwd` | string | No | Starting directory for the PTY session, defaults to the sandbox's working directory |
| `envs` | object | No | Environment variables for the PTY session |
| `cols` | number | No | Number of terminal columns |
| `rows` | number | No | Number of terminal rows |
| `lazyStart` | boolean | No | Whether to start the PTY session lazily (only start when first client connects) |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 201 | PTY session created successfully | PtyCreateResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}}

**[DEPRECATED] Get PTY session**

Get PTY session information by ID

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `sessionId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | PTY session retrieved successfully | PtySessionInfo |

---

## DELETE `/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}` {#daytona/tag/toolbox/DELETE/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}}

**[DEPRECATED] Delete PTY session**

Delete a PTY session and terminate the associated process

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `sessionId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | PTY session deleted successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}/resize` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/process/pty/{sessionId}/resize}

**[DEPRECATED] Resize PTY session**

Resize a PTY session

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `sessionId` | path | string | Yes |  |

### Request Body

Schema: **PtyResizeRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cols` | number | Yes | Number of terminal columns |
| `rows` | number | Yes | Number of terminal rows |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | PTY session resized successfully | PtySessionInfo |

---

## POST `/toolbox/{sandboxId}/toolbox/lsp/completions` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/lsp/completions}

**[DEPRECATED] Get Lsp Completions**

The Completion request is sent from the client to the server to compute completion items at a given cursor position.

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **LspCompletionParams**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `languageId` | string | Yes | Language identifier |
| `pathToProject` | string | Yes | Path to the project |
| `uri` | string | Yes | Document URI |
| `position` | [Position](#schema-position) | Yes |  |
| `context` | [CompletionContext](#schema-completioncontext) | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | CompletionList |

---

## POST `/toolbox/{sandboxId}/toolbox/lsp/did-close` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/lsp/did-close}

**[DEPRECATED] Call Lsp DidClose**

The document close notification is sent from the client to the server when the document got closed in the client.

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **LspDocumentRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `languageId` | string | Yes | Language identifier |
| `pathToProject` | string | Yes | Path to the project |
| `uri` | string | Yes | Document URI |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## POST `/toolbox/{sandboxId}/toolbox/lsp/did-open` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/lsp/did-open}

**[DEPRECATED] Call Lsp DidOpen**

The document open notification is sent from the client to the server to signal newly opened text documents.

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **LspDocumentRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `languageId` | string | Yes | Language identifier |
| `pathToProject` | string | Yes | Path to the project |
| `uri` | string | Yes | Document URI |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## GET `/toolbox/{sandboxId}/toolbox/lsp/document-symbols` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/lsp/document-symbols}

**[DEPRECATED] Call Lsp DocumentSymbols**

The document symbol request is sent from the client to the server.

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `languageId` | query | string | Yes |  |
| `pathToProject` | query | string | Yes |  |
| `uri` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | array of LspSymbol |

---

## POST `/toolbox/{sandboxId}/toolbox/lsp/start` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/lsp/start}

**[DEPRECATED] Start Lsp server**

Start Lsp server process inside sandbox project

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **LspServerRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `languageId` | string | Yes | Language identifier |
| `pathToProject` | string | Yes | Path to the project |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## POST `/toolbox/{sandboxId}/toolbox/lsp/stop` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/lsp/stop}

**[DEPRECATED] Stop Lsp server**

Stop Lsp server process inside sandbox project

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **LspServerRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `languageId` | string | Yes | Language identifier |
| `pathToProject` | string | Yes | Path to the project |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## GET `/toolbox/{sandboxId}/toolbox/lsp/workspace-symbols` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/lsp/workspace-symbols}

**[DEPRECATED] Call Lsp WorkspaceSymbols**

The workspace symbol request is sent from the client to the server to list project-wide symbols matching the query string.

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `languageId` | query | string | Yes |  |
| `pathToProject` | query | string | Yes |  |
| `query` | query | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | array of LspSymbol |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/start` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/start}

**[DEPRECATED] Start computer use processes**

Start all VNC desktop processes (Xvfb, xfce4, x11vnc, novnc)

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Computer use processes started successfully | ComputerUseStartResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/stop` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/stop}

**[DEPRECATED] Stop computer use processes**

Stop all VNC desktop processes (Xvfb, xfce4, x11vnc, novnc)

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Computer use processes stopped successfully | ComputerUseStopResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/status` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/status}

**[DEPRECATED] Get computer use status**

Get status of all VNC desktop processes

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Computer use status retrieved successfully | ComputerUseStatusResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/status` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/status}

**[DEPRECATED] Get process status**

Get status of a specific VNC process

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `processName` | path | string | Yes |  |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Process status retrieved successfully | ProcessStatusResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/restart` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/restart}

**[DEPRECATED] Restart process**

Restart a specific VNC process

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `processName` | path | string | Yes |  |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Process restarted successfully | ProcessRestartResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/logs` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/logs}

**[DEPRECATED] Get process logs**

Get logs for a specific VNC process

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `processName` | path | string | Yes |  |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Process logs retrieved successfully | ProcessLogsResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/errors` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/process/{processName}/errors}

**[DEPRECATED] Get process errors**

Get error logs for a specific VNC process

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `processName` | path | string | Yes |  |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Process errors retrieved successfully | ProcessErrorsResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/mouse/position` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/mouse/position}

**[DEPRECATED] Get mouse position**

Get current mouse cursor position

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Mouse position retrieved successfully | MousePosition |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/mouse/move` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/mouse/move}

**[DEPRECATED] Move mouse**

Move mouse cursor to specified coordinates

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **MouseMoveRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `x` | number | Yes | The target X coordinate to move the mouse cursor to |
| `y` | number | Yes | The target Y coordinate to move the mouse cursor to |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Mouse moved successfully | MouseMoveResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/mouse/click` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/mouse/click}

**[DEPRECATED] Click mouse**

Click mouse at specified coordinates

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **MouseClickRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `x` | number | Yes | The X coordinate where to perform the mouse click |
| `y` | number | Yes | The Y coordinate where to perform the mouse click |
| `button` | string | No | The mouse button to click (left, right, middle). Defaults to left |
| `double` | boolean | No | Whether to perform a double-click instead of a single click |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Mouse clicked successfully | MouseClickResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/mouse/drag` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/mouse/drag}

**[DEPRECATED] Drag mouse**

Drag mouse from start to end coordinates

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **MouseDragRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `startX` | number | Yes | The starting X coordinate for the drag operation |
| `startY` | number | Yes | The starting Y coordinate for the drag operation |
| `endX` | number | Yes | The ending X coordinate for the drag operation |
| `endY` | number | Yes | The ending Y coordinate for the drag operation |
| `button` | string | No | The mouse button to use for dragging (left, right, middle). Defaults to left |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Mouse dragged successfully | MouseDragResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/mouse/scroll` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/mouse/scroll}

**[DEPRECATED] Scroll mouse**

Scroll mouse at specified coordinates

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **MouseScrollRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `x` | number | Yes | The X coordinate where to perform the scroll operation |
| `y` | number | Yes | The Y coordinate where to perform the scroll operation |
| `direction` | string | Yes | The scroll direction (up, down) |
| `amount` | number | No | The number of scroll units to scroll. Defaults to 1 |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Mouse scrolled successfully | MouseScrollResponse |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/keyboard/type` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/keyboard/type}

**[DEPRECATED] Type text**

Type text using keyboard

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **KeyboardTypeRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | Yes | The text to type using the keyboard |
| `delay` | number | No | Delay in milliseconds between keystrokes. Defaults to 0 |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Text typed successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/keyboard/key` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/keyboard/key}

**[DEPRECATED] Press key**

Press a key with optional modifiers

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **KeyboardPressRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | string | Yes | The key to press (e.g., a, b, c, enter, space, etc.) |
| `modifiers` | array of string | No | Array of modifier keys to press along with the main key (ctrl, alt, shift, cmd) |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Key pressed successfully |  |

---

## POST `/toolbox/{sandboxId}/toolbox/computeruse/keyboard/hotkey` {#daytona/tag/toolbox/POST/toolbox/{sandboxId}/toolbox/computeruse/keyboard/hotkey}

**[DEPRECATED] Press hotkey**

Press a hotkey combination

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Request Body

Schema: **KeyboardHotkeyRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `keys` | string | Yes | The hotkey combination to press (e.g., "ctrl+c", "cmd+v", "alt+tab") |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Hotkey pressed successfully |  |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/screenshot` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/screenshot}

**[DEPRECATED] Take screenshot**

Take a screenshot of the entire screen

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `show_cursor` | query | boolean | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Screenshot taken successfully | ScreenshotResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/screenshot/region` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/screenshot/region}

**[DEPRECATED] Take region screenshot**

Take a screenshot of a specific region

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `show_cursor` | query | boolean | No |  |
| `height` | query | number | Yes |  |
| `width` | query | number | Yes |  |
| `y` | query | number | Yes |  |
| `x` | query | number | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Region screenshot taken successfully | RegionScreenshotResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/screenshot/compressed` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/screenshot/compressed}

**[DEPRECATED] Take compressed screenshot**

Take a compressed screenshot with format, quality, and scale options

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `scale` | query | number | No |  |
| `quality` | query | number | No |  |
| `format` | query | string | No |  |
| `show_cursor` | query | boolean | No |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Compressed screenshot taken successfully | CompressedScreenshotResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/screenshot/region/compressed` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/screenshot/region/compressed}

**[DEPRECATED] Take compressed region screenshot**

Take a compressed screenshot of a specific region

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |
| `scale` | query | number | No |  |
| `quality` | query | number | No |  |
| `format` | query | string | No |  |
| `show_cursor` | query | boolean | No |  |
| `height` | query | number | Yes |  |
| `width` | query | number | Yes |  |
| `y` | query | number | Yes |  |
| `x` | query | number | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Compressed region screenshot taken successfully | CompressedScreenshotResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/display/info` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/display/info}

**[DEPRECATED] Get display info**

Get information about displays

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Display info retrieved successfully | DisplayInfoResponse |

---

## GET `/toolbox/{sandboxId}/toolbox/computeruse/display/windows` {#daytona/tag/toolbox/GET/toolbox/{sandboxId}/toolbox/computeruse/display/windows}

**[DEPRECATED] Get windows**

Get list of open windows

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `X-Daytona-Organization-ID` | header | string | No | Use with JWT to specify the organization ID |
| `sandboxId` | path | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Windows list retrieved successfully | WindowsResponse |

---
