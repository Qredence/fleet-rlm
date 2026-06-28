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

```ruby
# List directory contents
files = sandbox.fs.list_files("workspace/data")

# Print files and their sizes
files.each do |file|
  puts "#{file.name}: #{file.size} bytes" unless file.is_dir
end

# List only directories
dirs = files.select(&:is_dir)
puts "Subdirectories: #{dirs.map(&:name).join(', ')}"
```

### Get directory or file information

Daytona provides methods to get directory or file information such as group, directory, modified time, mode, name, owner, permissions, and size by providing the path to the directory or file.

```ruby
# Get file metadata
info = sandbox.fs.get_file_info("workspace/data/file.txt")
puts "Size: #{info.size} bytes"
puts "Modified: #{info.mod_time}"
puts "Mode: #{info.mode}"

# Check if path is a directory
info = sandbox.fs.get_file_info("workspace/data")
puts "Path is a directory" if info.is_dir
```

### Create directories

Daytona provides methods to create directories by providing the path to the directory and the permissions to set on the directory.

```ruby
# Create a directory with standard permissions
sandbox.fs.create_folder("workspace/data", "755")

# Create a private directory
sandbox.fs.create_folder("workspace/secrets", "700")
```

### Upload files

Daytona provides methods to upload a single or multiple files in sandboxes.

#### Upload a single file

Daytona provides methods to upload a single file in sandboxes by providing the content to upload and the path to the file to upload it to.

```ruby
# Upload a text file from string content
content = "Hello, World!"
sandbox.fs.upload_file(content, "tmp/hello.txt")

# Upload a local file
sandbox.fs.upload_file("local_file.txt", "tmp/file.txt")

# Upload binary data
data = { key: "value" }.to_json
sandbox.fs.upload_file(data, "tmp/config.json")
```

#### Upload multiple files

Daytona provides methods to upload multiple files in sandboxes by providing the content to upload and their destination paths.

```ruby
# Upload multiple files
files = [
  FileUpload.new("Content of file 1", "/tmp/file1.txt"),
  FileUpload.new("workspace/data/file2.txt", "/tmp/file2.txt"),
  FileUpload.new('{"key": "value"}', "/tmp/config.json")
]

sandbox.fs.upload_files(files)
```

### Download files

Daytona provides methods to download files from sandboxes.

#### Download a single file

Daytona provides methods to download a single file from sandboxes by providing the path to the file to download.

```ruby
# Download and get file content
content = sandbox.fs.download_file("workspace/data/file.txt")
puts content

# Download and save a file locally
sandbox.fs.download_file("workspace/data/file.txt", "local_copy.txt")
size_mb = File.size("local_copy.txt") / 1024.0 / 1024.0
puts "Size of the downloaded file: #{size_mb} MB"
```

In the Python and TypeScript SDKs, `download_file` and `downloadFile` raise typed Daytona exceptions when the daemon returns structured per-file error metadata. Missing files map to not-found errors, invalid paths such as directories map to validation errors, and permission failures map to authorization errors.

#### Download multiple files

Daytona provides methods to download multiple files from sandboxes by providing the paths to the files to download.

```ruby
# Download multiple files by calling download_file for each
files_to_download = [
  { remote: "data/file1.txt", local: nil },              # Download to memory
  { remote: "data/file2.txt", local: "local_file2.txt" } # Download to local file
]

files_to_download.each do |f|
  if f[:local]
    sandbox.fs.download_file(f[:remote], f[:local])
    puts "Downloaded #{f[:remote]} to #{f[:local]}"
  else
    content = sandbox.fs.download_file(f[:remote])
    puts "Downloaded #{f[:remote]} to memory (#{content.size} bytes)"
  end
end
```

Bulk downloads keep the existing `error` string for compatibility and now also include structured metadata on each failed item:

- Python: `result.error_details.message`, `result.error_details.status_code`, `result.error_details.error_code`
- TypeScript: `result.errorDetails.message`, `result.errorDetails.statusCode`, `result.errorDetails.errorCode`

The toolbox bulk-download API returns successful files as multipart `file` parts and per-file failures as multipart `error` parts with JSON payloads containing `message`, `statusCode`, and `code`.

### Delete files

Daytona provides methods to delete files or directories from sandboxes by providing the path to the file or directory to delete.

```ruby
# Delete a file
sandbox.fs.delete_file("workspace/data/old_file.txt")

# Delete a directory recursively
sandbox.fs.delete_file("workspace/old_dir", recursive: true)
```

## Advanced operations

Daytona provides advanced file system operations such as file permissions, search and replace, and move files.

### File permissions

Daytona provides methods to set file permissions, ownership, and group for a file or directory by providing the path to the file or directory and the permissions to set.

```ruby
# Make a file executable
sandbox.fs.set_file_permissions(
  path: "workspace/scripts/run.sh",
  mode: "755"  # rwxr-xr-x
)

# Change file owner
sandbox.fs.set_file_permissions(
  path: "workspace/data/file.txt",
  owner: "daytona",
  group: "daytona"
)
```

### Find and replace text in files

Daytona provides methods to find and replace text in files by providing the path to the directory to search in and the pattern to search for.

```ruby
# Search for TODOs in Ruby files
matches = sandbox.fs.find_files("workspace/src", "TODO:")
matches.each do |match|
  puts "#{match.file}:#{match.line}: #{match.content.strip}"
end

# Replace in specific files
results = sandbox.fs.replace_in_files(
  files: ["workspace/src/file1.rb", "workspace/src/file2.rb"],
  pattern: "old_function",
  new_value: "new_function"
)

# Print results
results.each do |result|
  if result.success
    puts "#{result.file}: #{result.success}"
  else
    puts "#{result.file}: #{result.error}"
  end
end
```

### Move or rename directory or file

Daytona provides methods to move or rename a directory or file in sandboxes by providing the path to the file or directory (source) and the new path to the file or directory (destination).

```ruby
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

## See Also
- [Python SDK - file-system-operations](../python-sdk/file-system-operations.md)
- [TypeScript SDK - file-system-operations](../typescript-sdk/file-system-operations.md)
- [Go SDK - file-system-operations](../go-sdk/file-system-operations.md)
