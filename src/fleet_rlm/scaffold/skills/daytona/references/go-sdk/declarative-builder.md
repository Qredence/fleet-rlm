## Contents

- Build declarative images
- Create pre-built Snapshots
- Image configuration
- See Also




Declarative Builder provides a powerful, code-first approach to defining dependencies for Daytona Sandboxes. Instead of importing images from a container registry, you can programmatically define them using the Daytona SDK.

The declarative builder system supports two primary workflows:

- [**Declarative images**](#build-declarative-images): build images on demand when creating sandboxes
- [**Pre-built snapshots**](#create-pre-built-snapshots): create and register ready-to-use [snapshots](./snapshots.md)

## Build declarative images

Daytona provides an option to create declarative images on-the-fly when creating sandboxes. This is ideal for iterating quickly without creating separate snapshots.

Declarative images are cached for 24 hours, and are automatically reused when running the same script. Thus, subsequent runs on the same runner will be almost instantaneous.

```go
// Define a declarative image with python packages
version := "3.12"
declarativeImage := daytona.DebianSlim(&version).
  PipInstall([]string{"requests", "pytest"}).
  Workdir("/home/daytona")

// Create a new sandbox with the declarative image and stream the build logs
logChan := make(chan string)
go func() {
  for log := range logChan {
    fmt.Print(log)
  }
}()

sandbox, err := client.Create(ctx, types.ImageParams{
  Image: declarativeImage,
}, options.WithTimeout(0), options.WithLogChannel(logChan))
if err != nil {
  // handle error
}
```
> **Note:**
> Use the following best practices when working with the declarative builder:
>
> - **Layer Optimization**: Group related operations to minimize Docker layers
>
> - **Cache Utilization**: Identical build commands and context will be cached and subsequent builds will be almost instant
>
> - **Security**: Create non-root users for application workloads
>
> - **Resource Efficiency**: Use slim base images when appropriate
>
> - **Context Minimization**: Only include necessary files in the build context

## Create pre-built Snapshots

Daytona provides an option to [create pre-built snapshots](./snapshots.md#create-snapshots) that can be reused across multiple sandboxes.

The snapshot remains visible in the [Daytona Dashboard ↗](https://app.daytona.io/dashboard/snapshots) and is permanently cached, ensuring instant availability without rebuilding.

```go
// Create a python data science image
snapshotName := "data-science-snapshot"

version := "3.12"
image := daytona.DebianSlim(&version).
  PipInstall([]string{"pandas", "numpy"}).
  Workdir("/home/daytona")

// Create the snapshot and stream the build logs
_, logChan, err := client.Snapshot.Create(ctx, &types.CreateSnapshotParams{
  Name:  snapshotName,
  Image: image,
})
if err != nil {
  // handle error
}
for log := range logChan {
  fmt.Print(log)
}

// Create a new sandbox using the pre-built snapshot
sandbox, err := client.Create(ctx, types.SnapshotParams{
  Snapshot: snapshotName,
})
if err != nil {
  // handle error
}
```

## Image configuration

Daytona provides an option to define images programmatically using the Daytona SDK. You can specify base images, install packages, add files, set environment variables, and more.

For a complete API reference and method signatures, see the [Python](../python-sdk/image.md), [TypeScript](../typescript-sdk/image.md), [Ruby](../ruby-sdk/image.md), [Go](./daytona.md#type-DockerImage), and [Java](https://www.daytona.io/docs/en/java-sdk/image) SDK references.

### Base image selection

Daytona provides an option to select base images. The following snippets demonstrate how to select and configure base images:

```go
// Create an image from a base
image := daytona.Base("python:3.12-slim-bookworm")

// Use a Debian slim image with Python 3.12
version := "3.12"
image := daytona.DebianSlim(&version)
```

### Package management

Daytona provides an option to install packages and dependencies to your image.
The following snippets demonstrate how to install packages and dependencies to your image:

```go
// Add pip packages
version := "3.12"
image := daytona.DebianSlim(&version).PipInstall([]string{"requests", "pandas"})

// Install from requirements.txt
image := daytona.DebianSlim(&version).
  AddLocalFile("requirements.txt", "/tmp/requirements.txt").
  Run("pip install -r /tmp/requirements.txt")

// Install from pyproject.toml (with optional dependencies)
image := daytona.DebianSlim(&version).
  AddLocalFile("pyproject.toml", "/tmp/pyproject.toml").
  Run("pip install /tmp[dev]")
```

### File system operations

Daytona provides an option to add files and directories to your image.
The following snippets demonstrate how to add files and directories to your image:

```go
// Add a local file
version := "3.12"
image := daytona.DebianSlim(&version).AddLocalFile("package.json", "/home/daytona/package.json")

// Add a local directory
image := daytona.DebianSlim(&version).AddLocalDir("src", "/home/daytona/src")
```

### Environment configuration

Daytona provides an option to configure environment variables and working directories.
The following snippets demonstrate how to configure environment variables and working directories:

```go
// Set environment variables
version := "3.12"
image := daytona.DebianSlim(&version).Env("PROJECT_ROOT", "/home/daytona")

// Set working directory
image := daytona.DebianSlim(&version).Workdir("/home/daytona")
```

### Commands and entrypoints

Daytona provides an option to execute commands during build and configure container startup behavior.
The following snippets demonstrate how to execute commands during build and configure container startup behavior:

```go
// Run shell commands during build
version := "3.12"
image := daytona.DebianSlim(&version).
  Run("apt-get update && apt-get install -y git").
  Run("groupadd -r daytona && useradd -r -g daytona -m daytona").
  Run("mkdir -p /home/daytona/workspace")

// Set entrypoint
image := daytona.DebianSlim(&version).Entrypoint([]string{"/bin/bash"})

// Set default command
image := daytona.DebianSlim(&version).Cmd([]string{"/bin/bash"})
```

### Dockerfile integration

Daytona provides an option to integrate existing Dockerfiles or add custom Dockerfile commands.
The following snippets demonstrate how to integrate existing Dockerfiles or add custom Dockerfile commands:

```go
// Note: In Go, FromDockerfile takes the Dockerfile content as a string
content, err := os.ReadFile("Dockerfile")
if err != nil {
  // handle error
}
image := daytona.FromDockerfile(string(content))

// Extend an existing Dockerfile with additional commands
content, err = os.ReadFile("app/Dockerfile")
if err != nil {
  // handle error
}
image := daytona.FromDockerfile(string(content)).
  PipInstall([]string{"numpy"})
```

### System package installation

Daytona provides an option to install OS-level packages during the image build. Use this pattern when your sandbox needs CLI tools or system libraries that are not available through `pip`.

Each string passed to `run_commands` becomes a separate Dockerfile `RUN` instruction, and every `RUN` produces an immutable layer. To keep the image small, chain the package install and the apt cache cleanup together with `&&` inside a single string so the cache is never persisted in any layer.

```go
version := "3.12"
image := daytona.DebianSlim(&version).
  AptGet([]string{"git", "curl", "ffmpeg", "jq"})
```

### Non-root user setup

Daytona provides an option to define a non-root user for application workloads. Run all installation steps as `root` first, then create the user, fix ownership of the working directory, and switch to the new user with the `USER` directive. Subsequent commands and the sandbox runtime then operate without root privileges.

Place all installation steps before the `USER` directive. After switching to the non-root user, commands that write to system locations (such as `apt-get install` or `pip install` without `--user`) will fail with permission errors.

```go
version := "3.12"
image := daytona.DebianSlim(&version).
  PipInstall([]string{"fastapi", "uvicorn"}).
  Run("groupadd -r daytona && useradd -r -g daytona -m -d /home/daytona daytona").
  Run("chown -R daytona:daytona /home/daytona").
  Workdir("/home/daytona").
  User("daytona")
```

### Multi-language runtimes

Daytona provides an option to combine multiple language runtimes in a single image. The following pattern adds Node.js 20 to a Python base image by installing it from the NodeSource repository. The same approach works for adding Go, Ruby, Java, or any other runtime that distributes a Linux installer.

Chain the apt operations, the NodeSource installer, and the cache cleanup into a single `RUN` instruction. If the cache cleanup runs in a separate `RUN`, the apt cache is already persisted in the earlier layers and the final image keeps those bytes.

```go
version := "3.12"
image := daytona.DebianSlim(&version).
  Run("apt-get update " +
    "&& apt-get install -y --no-install-recommends curl ca-certificates " +
    "&& curl -fsSL https://deb.nodesource.com/setup_20.x | bash - " +
    "&& apt-get install -y nodejs " +
    "&& rm -rf /var/lib/apt/lists/*").
  PipInstall([]string{"fastapi", "uvicorn"})
```

## See Also
- [Python SDK - declarative-builder](../python-sdk/declarative-builder.md)
- [TypeScript SDK - declarative-builder](../typescript-sdk/declarative-builder.md)
