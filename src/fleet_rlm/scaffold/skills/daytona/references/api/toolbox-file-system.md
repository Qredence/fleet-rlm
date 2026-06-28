# File System API


## Contents

- GET `/files`
- DELETE `/files`
- POST `/files/bulk-download`
- POST `/files/bulk-upload`
- GET `/files/download`
- GET `/files/find`
- POST `/files/folder`
- GET `/files/info`
- POST `/files/move`
- POST `/files/permissions`
- POST `/files/replace`
- GET `/files/search`
- POST `/files/upload`

## GET `/files` {#daytona-toolbox/tag/file-system/GET/files}

**List files and directories**

List files and directories in the specified path

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | No | Directory path to list (defaults to working directory) |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | array of FileInfo |

---

## DELETE `/files` {#daytona-toolbox/tag/file-system/DELETE/files}

**Delete a file or directory**

Delete a file or directory at the specified path

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | File or directory path to delete |
| `recursive` | query | boolean | No | Enable recursive deletion for directories |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 204 | No Content |  |

---

## POST `/files/bulk-download` {#daytona-toolbox/tag/file-system/POST/files/bulk-download}

**Download multiple files**

Download multiple files by providing their paths. Successful files are returned as multipart parts named `file`. Per-file failures are returned as multipart parts named `error` with JSON payloads shaped like ErrorResponse.

### Request Body

Paths of files to download

Schema: **FilesDownloadRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `paths` | array of string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Multipart response with file parts and JSON error parts | gin.H |

---

## POST `/files/bulk-upload` {#daytona-toolbox/tag/file-system/POST/files/bulk-upload}

**Upload multiple files**

Upload multiple files with their destination paths

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## GET `/files/download` {#daytona-toolbox/tag/file-system/GET/files/download}

**Download a file**

Download a file by providing its path

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | File path to download |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | string |

---

## GET `/files/find` {#daytona-toolbox/tag/file-system/GET/files/find}

**Find text in files**

Search for text pattern within files in a directory

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | Directory path to search in |
| `pattern` | query | string | Yes | Text pattern to search for |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | array of Match |

---

## POST `/files/folder` {#daytona-toolbox/tag/file-system/POST/files/folder}

**Create a folder**

Create a folder with the specified path and optional permissions

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | Folder path to create |
| `mode` | query | string | Yes | Octal permission mode (default: 0755) |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 201 | Created |  |

---

## GET `/files/info` {#daytona-toolbox/tag/file-system/GET/files/info}

**Get file information**

Get detailed information about a file or directory

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | File or directory path |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | FileInfo |

---

## POST `/files/move` {#daytona-toolbox/tag/file-system/POST/files/move}

**Move or rename file/directory**

Move or rename a file or directory from source to destination

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `source` | query | string | Yes | Source file or directory path |
| `destination` | query | string | Yes | Destination file or directory path |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## POST `/files/permissions` {#daytona-toolbox/tag/file-system/POST/files/permissions}

**Set file permissions**

Set file permissions, ownership, and group for a file or directory

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | File or directory path |
| `owner` | query | string | No | Owner (username or UID) |
| `group` | query | string | No | Group (group name or GID) |
| `mode` | query | string | No | File mode in octal format (e.g., 0755) |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK |  |

---

## POST `/files/replace` {#daytona-toolbox/tag/file-system/POST/files/replace}

**Replace text in files**

Replace text pattern with new value in multiple files

### Request Body

Replace request

Schema: **ReplaceRequest**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `files` | array of string | Yes |  |
| `newValue` | string | Yes |  |
| `pattern` | string | Yes |  |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | array of ReplaceResult |

---

## GET `/files/search` {#daytona-toolbox/tag/file-system/GET/files/search}

**Search files by pattern**

Search for files matching a specific pattern in a directory

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | Directory path to search in |
| `pattern` | query | string | Yes | File pattern to match (e.g., *.txt, *.go) |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | SearchFilesResponse |

---

## POST `/files/upload` {#daytona-toolbox/tag/file-system/POST/files/upload}

**Upload a file**

Upload a file to the specified path

### Parameters

| Name | In | Type | Required | Description |
|------|-----|------|----------|-------------|
| `path` | query | string | Yes | Destination path for the uploaded file |

### Request Body

Schema: **UploadFile_request**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | string (binary) | Yes | File to upload |

### Responses

| Status | Description | Schema |
|--------|-------------|--------|
| 200 | OK | gin.H |

---
