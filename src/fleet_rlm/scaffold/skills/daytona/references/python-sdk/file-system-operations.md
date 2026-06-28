

Daytona provides comprehensive file system operations through the `fs` module in sandboxes.

## Basic operations

Daytona provides methods to interact with the file system in sandboxes. You can perform various operations like listing files, creating directories, reading and writing files, and more.

File operations assume you are operating in the sandbox user's home directory (e.g. `workspace` implies `/home/[username]/workspace`). Use a leading `/` when providing absolute paths.

### List files and directories

Daytona provides methods to list files and directories in a sandbox by providing the path to the directory. If the path is not provided, the method will list the files and directories in the sandbox working directory.

```python
# List files in a directory
files = sandbox.fs.list_files("workspace")

for file in files:
    print(f"Name: {file.name}")
    print(f"Is directory: {file.is_dir}")
    print(f"Size: {file.size}")
    print(f"Modified: {file.mod_time}")
```

### Get directory or file information

Daytona provides methods to get directory or file information such as group, directory, modified time, mode, name, owner, permissions, and size by providing the path to the directory or file.

```python
# Get file metadata
info = sandbox.fs.get_file_info("workspace/data/file.txt")
print(f"Size: {info.size} bytes")
print(f"Modified: {info.mod_time}")
print(f"Mode: {info.mode}")

# Check if path is a directory
info = sandbox.fs.get_file_info("workspace/data")
if info.is_dir:
    print("Path is a directory")
```

### Create directories

Daytona provides methods to create directories by providing the path to the directory and the permissions to set on the directory.

```python
# Create with specific permissions
sandbox.fs.create_folder("workspace/new-dir", "755")
```

### Upload files

Daytona provides methods to upload a single or multiple files in sandboxes.

#### Upload a single file

Daytona provides methods to upload a single file in sandboxes by providing the content to upload and the path to the file to upload it to.

```python
# Upload a single file
with open("local_file.txt", "rb") as f:
    content = f.read()
sandbox.fs.upload_file(content, "remote_file.txt")
```

#### Upload multiple files

Daytona provides methods to upload multiple files in sandboxes by providing the content to upload and their destination paths.

```python
# Upload multiple files at once
files_to_upload = []

with open("file1.txt", "rb") as f1:
    files_to_upload.append(FileUpload(
        source=f1.read(),
        destination="data/file1.txt",
    ))

with open("file2.txt", "rb") as f2:
    files_to_upload.append(FileUpload(
        source=f2.read(),
        destination="data/file2.txt",
    ))

with open("settings.json", "rb") as f3:
    files_to_upload.append(FileUpload(
        source=f3.read(),
        destination="config/settings.json",
    ))

sandbox.fs.upload_files(files_to_upload)
```

### Download files

Daytona provides methods to download files from sandboxes.

#### Download a single file

Daytona provides methods to download a single file from sandboxes by providing the path to the file to download.

```python
from daytona import DaytonaNotFoundError

try:
    content = sandbox.fs.download_file("file1.txt")
except DaytonaNotFoundError as error:
    print(f"Missing file: {error}")
else:
    with open("local_file.txt", "wb") as f:
        f.write(content)

    print(content.decode("utf-8"))
```

In the Python and TypeScript SDKs, `download_file` and `downloadFile` raise typed Daytona exceptions when the daemon returns structured per-file error metadata. Missing files map to not-found errors, invalid paths such as directories map to validation errors, and permission failures map to authorization errors.

#### Download multiple files

Daytona provides methods to download multiple files from sandboxes by providing the paths to the files to download.

```python
# Download multiple files at once
files_to_download = [
    FileDownloadRequest(source="data/file1.txt"), # No destination - download to memory
    FileDownloadRequest(source="data/file2.txt", destination="local_file2.txt"), # Download to local file
]

results = sandbox.fs.download_files(files_to_download)

for result in results:
    if result.error:
        print(f"Error downloading {result.source}: {result.error}")
        if result.error_details:
            print(
                f"  status={result.error_details.status_code} "
                f"code={result.error_details.error_code}"
            )
    elif result.result:
        print(f"Downloaded {result.source} to {result.result}")
```

Bulk downloads keep the existing `error` string for compatibility and now also include structured metadata on each failed item:

- Python: `result.error_details.message`, `result.error_details.status_code`, `result.error_details.error_code`
- TypeScript: `result.errorDetails.message`, `result.errorDetails.statusCode`, `result.errorDetails.errorCode`

The toolbox bulk-download API returns successful files as multipart `file` parts and per-file failures as multipart `error` parts with JSON payloads containing `message`, `statusCode`, and `code`.

### Delete files

Daytona provides methods to delete files or directories from sandboxes by providing the path to the file or directory to delete.

```python
sandbox.fs.delete_file("workspace/file.txt")
```

## Advanced operations

Daytona provides advanced file system operations such as file permissions, search and replace, and move files.

### File permissions

Daytona provides methods to set file permissions, ownership, and group for a file or directory by providing the path to the file or directory and the permissions to set.

```python
# Set file permissions
sandbox.fs.set_file_permissions("workspace/file.txt", "644")

# Get file permissions
file_info = sandbox.fs.get_file_info("workspace/file.txt")
print(f"Permissions: {file_info.permissions}")
```

### Find and replace text in files

Daytona provides methods to find and replace text in files by providing the path to the directory to search in and the pattern to search for.

```python
# Search for text in files by providing the path to the directory to search in and the pattern to search for
results = sandbox.fs.find_files(
    path="workspace/src",
    pattern="text-of-interest"
)
for match in results:
    print(f"Absolute file path: {match.file}")
    print(f"Line number: {match.line}")
    print(f"Line content: {match.content}")
    print("\n")

# Replace text in files
sandbox.fs.replace_in_files(
    files=["workspace/file1.txt", "workspace/file2.txt"],
    pattern="old_text",
    new_value="new_text"
)
```

### Move or rename directory or file

Daytona provides methods to move or rename a directory or file in sandboxes by providing the path to the file or directory (source) and the new path to the file or directory (destination).

```python
# Rename a file
sandbox.fs.move_files(
    "workspace/data/old_name.txt",
    "workspace/data/new_name.txt"
)

# Move a file to a different directory
sandbox.fs.move_files(
    "workspace/data/file.txt",
    "workspace/archive/file.txt"
)

# Move a directory
sandbox.fs.move_files(
    "workspace/old_dir",
    "workspace/new_dir"
)
```
