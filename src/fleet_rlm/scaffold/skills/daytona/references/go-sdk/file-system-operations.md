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

```go
// List files in a directory
files, err := sandbox.FileSystem.ListFiles(ctx, "workspace")
if err != nil {
	log.Fatal(err)
}

for _, file := range files {
	fmt.Printf("Name: %s\n", file.Name)
	fmt.Printf("Is directory: %t\n", file.IsDirectory)
	fmt.Printf("Size: %d\n", file.Size)
	fmt.Printf("Modified: %s\n", file.ModifiedTime)
}
```

### Get directory or file information

Daytona provides methods to get directory or file information such as group, directory, modified time, mode, name, owner, permissions, and size by providing the path to the directory or file.

```go
// Get file metadata
info, err := sandbox.FileSystem.GetFileInfo(ctx, "workspace/data/file.txt")
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Size: %d bytes\n", info.Size)
fmt.Printf("Modified: %s\n", info.ModifiedTime)
fmt.Printf("Mode: %s\n", info.Mode)

// Check if path is a directory
info, err = sandbox.FileSystem.GetFileInfo(ctx, "workspace/data")
if err != nil {
	log.Fatal(err)
}
if info.IsDirectory {
	fmt.Println("Path is a directory")
}
```

### Create directories

Daytona provides methods to create directories by providing the path to the directory and the permissions to set on the directory.

```go
// Create with specific permissions
err := sandbox.FileSystem.CreateFolder(ctx, "workspace/new-dir",
	options.WithMode("755"),
)
if err != nil {
	log.Fatal(err)
}
```

### Upload files

Daytona provides methods to upload a single or multiple files in sandboxes.

#### Upload a single file

Daytona provides methods to upload a single file in sandboxes by providing the content to upload and the path to the file to upload it to.

```go
// Upload from a local file path
err := sandbox.FileSystem.UploadFile(ctx, "local_file.txt", "remote_file.txt")
if err != nil {
	log.Fatal(err)
}

// Or upload from byte content
content := []byte("Hello, World!")
err = sandbox.FileSystem.UploadFile(ctx, content, "hello.txt")
if err != nil {
	log.Fatal(err)
}
```

#### Upload multiple files

Daytona provides methods to upload multiple files in sandboxes by providing the content to upload and their destination paths.

```go
// Upload multiple files by calling UploadFile for each
filesToUpload := []struct {
	source      string
	destination string
}{
	{"file1.txt", "data/file1.txt"},
	{"file2.txt", "data/file2.txt"},
	{"settings.json", "config/settings.json"},
}

for _, f := range filesToUpload {
	err := sandbox.FileSystem.UploadFile(ctx, f.source, f.destination)
	if err != nil {
		log.Fatal(err)
	}
}
```

### Download files

Daytona provides methods to download files from sandboxes.

#### Download a single file

Daytona provides methods to download a single file from sandboxes by providing the path to the file to download.

```go
// Download and get contents in memory
content, err := sandbox.FileSystem.DownloadFile(ctx, "file1.txt", nil)
if err != nil {
	log.Fatal(err)
}
fmt.Println(string(content))

// Download and save to a local file
localPath := "local_file.txt"
content, err = sandbox.FileSystem.DownloadFile(ctx, "file1.txt", &localPath)
if err != nil {
	log.Fatal(err)
}
```

In the Python and TypeScript SDKs, `download_file` and `downloadFile` raise typed Daytona exceptions when the daemon returns structured per-file error metadata. Missing files map to not-found errors, invalid paths such as directories map to validation errors, and permission failures map to authorization errors.

#### Download multiple files

Daytona provides methods to download multiple files from sandboxes by providing the paths to the files to download.

```go
// Download multiple files by calling DownloadFile for each
filesToDownload := []struct {
	remotePath string
	localPath  *string
}{
	{"data/file1.txt", nil},                           // Download to memory
	{"data/file2.txt", ptrString("local_file2.txt")},  // Download to local file
}

for _, f := range filesToDownload {
	content, err := sandbox.FileSystem.DownloadFile(ctx, f.remotePath, f.localPath)
	if err != nil {
		fmt.Printf("Error downloading %s: %v\n", f.remotePath, err)
		continue
	}
	if f.localPath == nil {
		fmt.Printf("Downloaded %s to memory (%d bytes)\n", f.remotePath, len(content))
	} else {
		fmt.Printf("Downloaded %s to %s\n", f.remotePath, *f.localPath)
	}
}
```

Bulk downloads keep the existing `error` string for compatibility and now also include structured metadata on each failed item:

- Python: `result.error_details.message`, `result.error_details.status_code`, `result.error_details.error_code`
- TypeScript: `result.errorDetails.message`, `result.errorDetails.statusCode`, `result.errorDetails.errorCode`

The toolbox bulk-download API returns successful files as multipart `file` parts and per-file failures as multipart `error` parts with JSON payloads containing `message`, `statusCode`, and `code`.

### Delete files

Daytona provides methods to delete files or directories from sandboxes by providing the path to the file or directory to delete.

```go
// Delete a file
err := sandbox.FileSystem.DeleteFile(ctx, "workspace/file.txt", false)
if err != nil {
	log.Fatal(err)
}

// Delete a directory recursively
err = sandbox.FileSystem.DeleteFile(ctx, "workspace/old_dir", true)
if err != nil {
	log.Fatal(err)
}
```

## Advanced operations

Daytona provides advanced file system operations such as file permissions, search and replace, and move files.

### File permissions

Daytona provides methods to set file permissions, ownership, and group for a file or directory by providing the path to the file or directory and the permissions to set.

```go
// Set file permissions
err := sandbox.FileSystem.SetFilePermissions(ctx, "workspace/file.txt",
	options.WithPermissionMode("644"),
)
if err != nil {
	log.Fatal(err)
}

// Set owner and group
err = sandbox.FileSystem.SetFilePermissions(ctx, "workspace/file.txt",
	options.WithOwner("daytona"),
	options.WithGroup("daytona"),
)
if err != nil {
	log.Fatal(err)
}

// Get file info to check permissions
fileInfo, err := sandbox.FileSystem.GetFileInfo(ctx, "workspace/file.txt")
if err != nil {
	log.Fatal(err)
}
fmt.Printf("Mode: %s\n", fileInfo.Mode)
```

### Find and replace text in files

Daytona provides methods to find and replace text in files by providing the path to the directory to search in and the pattern to search for.

```go
// Search for text in files
result, err := sandbox.FileSystem.FindFiles(ctx, "workspace/src", "text-of-interest")
if err != nil {
	log.Fatal(err)
}
matches := result.([]map[string]any)
for _, match := range matches {
	fmt.Printf("Absolute file path: %s\n", match["file"])
	fmt.Printf("Line number: %v\n", match["line"])
	fmt.Printf("Line content: %s\n\n", match["content"])
}

// Replace text in files
_, err = sandbox.FileSystem.ReplaceInFiles(ctx,
	[]string{"workspace/file1.txt", "workspace/file2.txt"},
	"old_text",
	"new_text",
)
if err != nil {
	log.Fatal(err)
}
```

### Move or rename directory or file

Daytona provides methods to move or rename a directory or file in sandboxes by providing the path to the file or directory (source) and the new path to the file or directory (destination).

```go
// Rename a file
err := sandbox.FileSystem.MoveFiles(ctx, "workspace/data/old_name.txt", "workspace/data/new_name.txt")
if err != nil {
	log.Fatal(err)
}

// Move a file to a different directory
err = sandbox.FileSystem.MoveFiles(ctx, "workspace/data/file.txt", "workspace/archive/file.txt")
if err != nil {
	log.Fatal(err)
}

// Move a directory
err = sandbox.FileSystem.MoveFiles(ctx, "workspace/old_dir", "workspace/new_dir")
if err != nil {
	log.Fatal(err)
}
```

## See Also
- [Python SDK - file-system-operations](../python-sdk/file-system-operations.md)
- [TypeScript SDK - file-system-operations](../typescript-sdk/file-system-operations.md)
