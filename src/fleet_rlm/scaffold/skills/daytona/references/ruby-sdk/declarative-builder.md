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

```ruby
# Define a simple declarative image with Python packages
declarative_image = Daytona::Image
  .debian_slim('3.12')
  .pip_install(['requests', 'pytest'])
  .workdir('/home/daytona')

# Create a new Sandbox with the declarative image and stream the build logs
sandbox = daytona.create(
  Daytona::CreateSandboxFromImageParams.new(image: declarative_image),
  on_snapshot_create_logs: proc { |chunk| puts chunk }
)
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

```ruby
# Create a simple Python data science image
snapshot_name = 'data-science-snapshot'

image = Daytona::Image
  .debian_slim('3.12')
  .pip_install(['pandas', 'numpy'])
  .workdir('/home/daytona')

# Create the Snapshot and stream the build logs
daytona.snapshot.create(
  Daytona::CreateSnapshotParams.new(
    name: snapshot_name,
    image: image
  ),
  on_logs: proc { |chunk| puts chunk }
)

# Create a new Sandbox using the pre-built Snapshot
sandbox = daytona.create(
  Daytona::CreateSandboxFromSnapshotParams.new(snapshot: snapshot_name)
)
```

## Image configuration

Daytona provides an option to define images programmatically using the Daytona SDK. You can specify base images, install packages, add files, set environment variables, and more.

For a complete API reference and method signatures, see the [Python](../python-sdk/image.md), [TypeScript](../typescript-sdk/image.md), [Ruby](./image.md), [Go](../go-sdk/daytona.md#type-DockerImage), and [Java](https://www.daytona.io/docs/en/java-sdk/image) SDK references.

### Base image selection

Daytona provides an option to select base images. The following snippets demonstrate how to select and configure base images:

```ruby
# Create an image from a base
image = Daytona::Image.base('python:3.12-slim-bookworm')

# Use a Debian slim image with Python 3.12
image = Daytona::Image.debian_slim('3.12')
```

### Package management

Daytona provides an option to install packages and dependencies to your image.
The following snippets demonstrate how to install packages and dependencies to your image:

```ruby
# Add pip packages
image = Daytona::Image.debian_slim('3.12').pip_install(['requests', 'pandas'])

# Install from requirements.txt
image = Daytona::Image.debian_slim('3.12').pip_install_from_requirements('requirements.txt')

# Install from pyproject.toml (with optional dependencies)
image = Daytona::Image.debian_slim('3.12').pip_install_from_pyproject('pyproject.toml',
  optional_dependencies: ['dev']
)
```

### File system operations

Daytona provides an option to add files and directories to your image.
The following snippets demonstrate how to add files and directories to your image:

```ruby
# Add a local file
image = Daytona::Image.debian_slim('3.12').add_local_file('package.json', '/home/daytona/package.json')

# Add a local directory
image = Daytona::Image.debian_slim('3.12').add_local_dir('src', '/home/daytona/src')
```

### Environment configuration

Daytona provides an option to configure environment variables and working directories.
The following snippets demonstrate how to configure environment variables and working directories:

```ruby
# Set environment variables
image = Daytona::Image.debian_slim('3.12').env({ 'PROJECT_ROOT' => '/home/daytona' })

# Set working directory
image = Daytona::Image.debian_slim('3.12').workdir('/home/daytona')
```

### Commands and entrypoints

Daytona provides an option to execute commands during build and configure container startup behavior.
The following snippets demonstrate how to execute commands during build and configure container startup behavior:

```ruby
# Run shell commands during build
image = Daytona::Image.debian_slim('3.12').run_commands(
  'apt-get update && apt-get install -y git',
  'groupadd -r daytona && useradd -r -g daytona -m daytona',
  'mkdir -p /home/daytona/workspace'
)

# Set entrypoint
image = Daytona::Image.debian_slim('3.12').entrypoint(['/bin/bash'])

# Set default command
image = Daytona::Image.debian_slim('3.12').cmd(['/bin/bash'])
```

### Dockerfile integration

Daytona provides an option to integrate existing Dockerfiles or add custom Dockerfile commands.
The following snippets demonstrate how to integrate existing Dockerfiles or add custom Dockerfile commands:

```ruby
# Add custom Dockerfile commands
image = Daytona::Image.debian_slim('3.12').dockerfile_commands(['RUN echo "Hello, world!"'])

# Use an existing Dockerfile
image = Daytona::Image.from_dockerfile('Dockerfile')

# Extend an existing Dockerfile
image = Daytona::Image.from_dockerfile('app/Dockerfile').pip_install(['numpy'])
```

### System package installation

Daytona provides an option to install OS-level packages during the image build. Use this pattern when your sandbox needs CLI tools or system libraries that are not available through `pip`.

Each string passed to `run_commands` becomes a separate Dockerfile `RUN` instruction, and every `RUN` produces an immutable layer. To keep the image small, chain the package install and the apt cache cleanup together with `&&` inside a single string so the cache is never persisted in any layer.

```ruby
image = Daytona::Image
  .debian_slim('3.12')
  .run_commands(
    'apt-get update ' \
    '&& apt-get install -y --no-install-recommends git curl ffmpeg jq ' \
    '&& rm -rf /var/lib/apt/lists/*'
  )
```

### Non-root user setup

Daytona provides an option to define a non-root user for application workloads. Run all installation steps as `root` first, then create the user, fix ownership of the working directory, and switch to the new user with the `USER` directive. Subsequent commands and the sandbox runtime then operate without root privileges.

Place all installation steps before the `USER` directive. After switching to the non-root user, commands that write to system locations (such as `apt-get install` or `pip install` without `--user`) will fail with permission errors.

```ruby
image = Daytona::Image
  .debian_slim('3.12')
  .pip_install(['fastapi', 'uvicorn'])
  .run_commands(
    'groupadd -r daytona && useradd -r -g daytona -m -d /home/daytona daytona',
    'chown -R daytona:daytona /home/daytona'
  )
  .workdir('/home/daytona')
  .dockerfile_commands(['USER daytona'])
```

### Multi-language runtimes

Daytona provides an option to combine multiple language runtimes in a single image. The following pattern adds Node.js 20 to a Python base image by installing it from the NodeSource repository. The same approach works for adding Go, Ruby, Java, or any other runtime that distributes a Linux installer.

Chain the apt operations, the NodeSource installer, and the cache cleanup into a single `RUN` instruction. If the cache cleanup runs in a separate `RUN`, the apt cache is already persisted in the earlier layers and the final image keeps those bytes.

```ruby
image = Daytona::Image
  .debian_slim('3.12')
  .run_commands(
    'apt-get update ' \
    '&& apt-get install -y --no-install-recommends curl ca-certificates ' \
    '&& curl -fsSL https://deb.nodesource.com/setup_20.x | bash - ' \
    '&& apt-get install -y nodejs ' \
    '&& rm -rf /var/lib/apt/lists/*'
  )
  .pip_install(['fastapi', 'uvicorn'])
```

## See Also
- [Python SDK - declarative-builder](../python-sdk/declarative-builder.md)
- [TypeScript SDK - declarative-builder](../typescript-sdk/declarative-builder.md)
- [Go SDK - declarative-builder](../go-sdk/declarative-builder.md)
