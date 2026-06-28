## Contents

- Dashboard
- SDKs
- CLI
- API
- MCP server
- Multiple runtime support
- Guides
- Examples




This section introduces core concepts, common workflows, and next steps for using Daytona.

## Dashboard

[Daytona Dashboard ↗](https://app.daytona.io/) is a visual user interface where you can manage sandboxes, access API keys, view usage, and more.
It serves as the primary point of control for managing your Daytona resources.

## SDKs

Daytona provides [Python](./README.md), [TypeScript](../typescript-sdk/README.md), [Ruby](../ruby-sdk/README.md), [Go](../go-sdk/README.md), and [Java](https://www.daytona.io/docs/en/java-sdk) SDKs to programmatically interact with sandboxes. They support sandbox lifecycle management, code execution, resource access, and more.

## CLI

Daytona provides command-line access to core features for interacting with Daytona Sandboxes, including managing their lifecycle, snapshots, and more. To interact with Daytona Sandboxes from the command line, install the Daytona CLI:

**Mac:**

```bash
brew install daytonaio/cli/daytona
```

Trust the tap once so routine `brew upgrade` keeps the Daytona CLI up to date. Recent Homebrew versions require third-party taps to be explicitly trusted; without it, a bare `brew upgrade` skips the Daytona tap and the CLI goes stale:

```bash
brew trust daytonaio/cli
```

To upgrade the Daytona CLI to the latest version:

```bash
brew upgrade daytonaio/cli/daytona
```

Alternatively, install directly without Homebrew:

For Apple Silicon (`arm64`):

  ```bash
  sudo curl -fL https://github.com/daytonaio/daytona/releases/latest/download/daytona-darwin-arm64 -o /usr/local/bin/daytona && sudo chmod +x /usr/local/bin/daytona
  ```

For Intel (`amd64`):

  ```bash
  sudo curl -fL https://github.com/daytonaio/daytona/releases/latest/download/daytona-darwin-amd64 -o /usr/local/bin/daytona && sudo chmod +x /usr/local/bin/daytona
  ```

**Linux:**

Choose the command for your Linux architecture. Both commands download the latest binary from GitHub releases and install it to `/usr/local/bin`, overwriting any existing version.

For `amd64` (`x86_64`):

  ```bash
  sudo curl -fL https://github.com/daytonaio/daytona/releases/latest/download/daytona-linux-amd64 -o /usr/local/bin/daytona && sudo chmod +x /usr/local/bin/daytona
  ```

For `arm64` (`aarch64`):

  ```bash
  sudo curl -fL https://github.com/daytonaio/daytona/releases/latest/download/daytona-linux-arm64 -o /usr/local/bin/daytona && sudo chmod +x /usr/local/bin/daytona
  ```

**Windows:**

```bash
powershell -Command "irm https://get.daytona.io/windows | iex"
```

After installing the Daytona CLI, use the `daytona` command to interact with Daytona sandboxes from the command line.

To view all available commands and flags, see the [CLI reference](../cli.md).

## API

Daytona provides a RESTful API for interacting with Daytona Sandboxes, including managing their lifecycle, snapshots, and more.
It serves as a flexible and powerful way to interact with Daytona from your own applications.

To interact with Daytona Sandboxes from the API, see the [API reference](../api/README.md).

## MCP server

Daytona provides a Model Context Protocol (MCP) server that enables AI agents to interact with Daytona Sandboxes programmatically. The MCP server integrates with popular AI agents including Claude, Cursor, and Windsurf.

To set up the MCP server with your AI agent:

```bash
daytona mcp init [claude/cursor/windsurf]
```

For more information, see the [MCP server documentation](../platform/mcp.md).

## Multiple runtime support

The [TypeScript SDK](../typescript-sdk/README.md) ships as a dual ESM/CJS package and works out of the box in **Node.js**, **Bun**, **Next.js**, **Nuxt.js**, **Remix**, **Vite SSR**, **AWS Lambda**, and **Azure Functions** without any extra configuration.

For **Cloudflare Workers**, set the Node.js compatibility flag in your `wrangler.toml`:

```toml
compatibility_flags = ["nodejs_compat"]
```

For **Deno**, install with `deno add npm:@daytona/sdk` or import directly with the `npm:` specifier:

```typescript
import { Daytona, Image } from 'npm:@daytona/sdk'
```

For **browser apps with Vite** (or any browser bundler), install [`vite-plugin-node-polyfills`](https://www.npmjs.com/package/vite-plugin-node-polyfills) and add it to your `vite.config.ts`:

```typescript
import { defineConfig } from 'vite'
import { nodePolyfills } from 'vite-plugin-node-polyfills'

export default defineConfig({
  plugins: [nodePolyfills({ globals: { Buffer: true, process: true, global: true } })],
})
```

The SDK uses Node's `Buffer` for binary data (downloaded files, multipart bodies). Browsers don't ship `Buffer`, so the polyfill provides it. Without it, basic operations like `Image.base()` and `daytona.list()` still work, but methods that handle binary payloads (`fs.downloadFile`, `fs.downloadFiles`) will throw.

Some runtimes don't expose the full set of Node.js APIs (browsers and edge runtimes have no filesystem, no `crypto`, etc.). Methods that depend on those APIs throw a clear runtime error instead of silently producing wrong results.

## Guides

Daytona provides a comprehensive set of [guides](https://www.daytona.io/docs/en/guides) to help you get started. The guides cover a wide range of topics, from basic usage to advanced topics, and showcase various types of integrations between Daytona and other tools.

## Examples

Daytona provides quick examples for common sandbox operations and best practices. <br />
The examples are based on the Daytona [Python](./README.md), [TypeScript](../typescript-sdk/README.md), [Go](../go-sdk/README.md), [Ruby](../ruby-sdk/README.md), [Java](https://www.daytona.io/docs/en/java-sdk) **SDKs**, [CLI](../cli.md), and [API](../api/README.md) references. More examples are available in our [GitHub repository](https://github.com/daytonaio/daytona/tree/main/examples).

### Create a sandbox

Create a [sandbox](./sandboxes.md) with default settings.

```python
from daytona import Daytona

daytona = Daytona()
sandbox = daytona.create()
print(f"Sandbox ID: {sandbox.id}")
```

### Create and run code in a sandbox

Create a [sandbox](./sandboxes.md) and run code securely in it.

```python
from daytona import Daytona

daytona = Daytona()
sandbox = daytona.create()
response = sandbox.process.exec("echo 'Hello, World!'")
print(response.result)
sandbox.delete()
```

### Create a sandbox with custom resources

Create a sandbox with [custom resources](./sandboxes.md#resources) (CPU, memory, disk).

```python
from daytona import Daytona, CreateSandboxFromImageParams, Image, Resources

daytona = Daytona()
sandbox = daytona.create(
    CreateSandboxFromImageParams(
        image=Image.debian_slim("3.12"),
        resources=Resources(cpu=2, memory=4, disk=8)
    )
)
```

### Create an ephemeral sandbox

Create an [ephemeral sandbox](./sandboxes.md#ephemeral-sandboxes) that is automatically deleted when stopped.

```python
from daytona import Daytona, CreateSandboxFromSnapshotParams

daytona = Daytona()
sandbox = daytona.create(
    CreateSandboxFromSnapshotParams(ephemeral=True, auto_stop_interval=5)
)
```

### Create a sandbox from a snapshot

Create a sandbox from a pre-built [snapshot](./snapshots.md) for faster sandbox creation with pre-installed dependencies.

```python
from daytona import Daytona, CreateSandboxFromSnapshotParams

daytona = Daytona()
sandbox = daytona.create(
    CreateSandboxFromSnapshotParams(
        snapshot="my-snapshot-name",
        language="python"
    )
)
```

### Create a sandbox with a declarative image

Create a sandbox with a [declarative image](./declarative-builder.md) that defines dependencies programmatically.

```python
from daytona import Daytona, CreateSandboxFromImageParams, Image

daytona = Daytona()
image = (
    Image.debian_slim("3.12")
    .pip_install(["requests", "pandas", "numpy"])
    .workdir("/home/daytona")
)
sandbox = daytona.create(
    CreateSandboxFromImageParams(image=image),
    on_snapshot_create_logs=print
)
```

### Create a sandbox with volumes

Create a sandbox with a [volume](./volumes.md) mounted to share data across sandboxes.

```python
from daytona import Daytona, CreateSandboxFromSnapshotParams, VolumeMount

daytona = Daytona()
volume = daytona.volume.get("my-volume", create=True)
sandbox = daytona.create(
    CreateSandboxFromSnapshotParams(
        volumes=[VolumeMount(volume_id=volume.id, mount_path="/home/daytona/data")]
    )
)
```

### Create a sandbox with a Git repository cloned

Create a sandbox with a [Git repository](../typescript-sdk/git.md) cloned to manage version control.

```python
from daytona import Daytona

daytona = Daytona()
sandbox = daytona.create()

sandbox.git.clone("https://github.com/daytonaio/daytona.git", "/home/daytona/daytona")
status = sandbox.git.status("/home/daytona/daytona")
print(f"Branch: {status.current_branch}")
```
