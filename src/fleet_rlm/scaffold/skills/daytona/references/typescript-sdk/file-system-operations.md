## Contents

- Basic operations
- Advanced operations
- See Also




Daytona provides comprehensive file system operations through the `fs` module in sandboxes.

## Basic operations

Daytona provides methods to interact with the file system in sandboxes. You can perform various operations like listing files, creating directories, reading and writing files, and more.

File operations assume you are operating in the sandbox user's home directory (e.g. `workspace` implies `/home/[username]/workspace`). Use a leading `/` when providing absolute paths.

### List files and directories

Daytona provides methods to list files and directories in a sandbox by providing the path to the directory. If the path is not provided, the method will list the files and directories in the sandbox working directory.

```typescript
// List files in a directory
const files = await sandbox.fs.listFiles('workspace')

files.forEach(file => {
  console.log(`Name: ${file.name}`)
  console.log(`Is directory: ${file.isDir}`)
  console.log(`Size: ${file.size}`)
  console.log(`Modified: ${file.modTime}`)
})
```

### Get directory or file information

Daytona provides methods to get directory or file information such as group, directory, modified time, mode, name, owner, permissions, and size by providing the path to the directory or file.

```typescript
// Get file details
const info = await fs.getFileDetails('app/config.json')
console.log(`Size: ${info.size}, Modified: ${info.modTime}`)
```

### Create directories

Daytona provides methods to create directories by providing the path to the directory and the permissions to set on the directory.

```typescript
// Create with specific permissions
await sandbox.fs.createFolder('workspace/new-dir', '755')
```

### Upload files

Daytona provides methods to upload a single or multiple files in sandboxes.

#### Upload a single file

Daytona provides methods to upload a single file in sandboxes by providing the content to upload and the path to the file to upload it to.

```typescript
// Upload a single file
const fileContent = Buffer.from('Hello, World!')
await sandbox.fs.uploadFile(fileContent, 'data.txt')
```

#### Upload multiple files

Daytona provides methods to upload multiple files in sandboxes by providing the content to upload and their destination paths.

```typescript
// Upload multiple files at once
const files = [
  {
    source: Buffer.from('Content of file 1'),
    destination: 'data/file1.txt',
  },
  {
    source: Buffer.from('Content of file 2'),
    destination: 'data/file2.txt',
  },
  {
    source: Buffer.from('{"key": "value"}'),
    destination: 'config/settings.json',
  },
]

await sandbox.fs.uploadFiles(files)
```

### Download files

Daytona provides methods to download files from sandboxes.

#### Download a single file

Daytona provides methods to download a single file from sandboxes by providing the path to the file to download.

```typescript
import { DaytonaNotFoundError } from '@daytona/sdk'

try {
  const downloadedFile = await sandbox.fs.downloadFile('file1.txt')
  console.log('File content:', downloadedFile.toString())
} catch (error) {
  if (error instanceof DaytonaNotFoundError) {
    console.error(`Missing file: ${error.message}`)
  } else {
    throw error
  }
}
```

In the Python and TypeScript SDKs, `download_file` and `downloadFile` raise typed Daytona exceptions when the daemon returns structured per-file error metadata. Missing files map to not-found errors, invalid paths such as directories map to validation errors, and permission failures map to authorization errors.

#### Download multiple files

Daytona provides methods to download multiple files from sandboxes by providing the paths to the files to download.

```typescript
// Download multiple files at once
const files = [
  { source: 'data/file1.txt' }, // No destination - download to memory
  { source: 'data/file2.txt', destination: 'local_file2.txt' }, // Download to local file
]

const results = await sandbox.fs.downloadFiles(files)

results.forEach(result => {
  if (result.error) {
    console.error(`Error downloading ${result.source}: ${result.error}`)
    if (result.errorDetails) {
      console.error(
        `  status=${result.errorDetails.statusCode} code=${result.errorDetails.errorCode}`
      )
    }
  } else if (result.result) {
    console.log(`Downloaded ${result.source} to ${result.result}`)
  }
})
```

Bulk downloads keep the existing `error` string for compatibility and now also include structured metadata on each failed item:

- Python: `result.error_details.message`, `result.error_details.status_code`, `result.error_details.error_code`
- TypeScript: `result.errorDetails.message`, `result.errorDetails.statusCode`, `result.errorDetails.errorCode`

The toolbox bulk-download API returns successful files as multipart `file` parts and per-file failures as multipart `error` parts with JSON payloads containing `message`, `statusCode`, and `code`.

### Delete files

Daytona provides methods to delete files or directories from sandboxes by providing the path to the file or directory to delete.

```typescript
await sandbox.fs.deleteFile('workspace/file.txt')
```

## Advanced operations

Daytona provides advanced file system operations such as file permissions, search and replace, and move files.

### File permissions

Daytona provides methods to set file permissions, ownership, and group for a file or directory by providing the path to the file or directory and the permissions to set.

```typescript
// Set file permissions
await sandbox.fs.setFilePermissions('workspace/file.txt', { mode: '644' })

// Get file permissions
const fileInfo = await sandbox.fs.getFileDetails('workspace/file.txt')
console.log(`Permissions: ${fileInfo.permissions}`)
```

### Find and replace text in files

Daytona provides methods to find and replace text in files by providing the path to the directory to search in and the pattern to search for.

```typescript
// Search for text in files; if a folder is specified, the search is recursive
const results = await sandbox.fs.findFiles({
    path="workspace/src",
    pattern: "text-of-interest"
})
results.forEach(match => {
    console.log('Absolute file path:', match.file)
    console.log('Line number:', match.line)
    console.log('Line content:', match.content)
})

// Replace text in files
await sandbox.fs.replaceInFiles(
    ["workspace/file1.txt", "workspace/file2.txt"],
    "old_text",
    "new_text"
)
```

### Move or rename directory or file

Daytona provides methods to move or rename a directory or file in sandboxes by providing the path to the file or directory (source) and the new path to the file or directory (destination).

```typescript
// Move a file to a new location
await fs.moveFiles('app/temp/data.json', 'app/data/data.json')
```

## See Also
- [Python SDK - file-system-operations](../python-sdk/file-system-operations.md)
