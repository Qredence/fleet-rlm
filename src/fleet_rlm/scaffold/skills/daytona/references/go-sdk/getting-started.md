## Contents

- Dashboard
- SDKs
- CLI
- API
- MCP server
- Multiple runtime support
- Guides
- Examples
- See Also




This section introduces core concepts, common workflows, and next steps for using Daytona.

## Dashboard

[Daytona Dashboard ↗](https://app.daytona.io/) is a visual user interface where you can manage sandboxes, access API keys, view usage, and more.
It serves as the primary point of control for managing your Daytona resources.

## SDKs

Daytona provides [Python](../python-sdk/README.md), [TypeScript](../typescript-sdk/README.md), [Ruby](../ruby-sdk/README.md), [Go](./README.md), and [Java](https://www.daytona.io/docs/en/java-sdk) SDKs to programmatically interact with sandboxes. They support sandbox lifecycle management, code execution, resource access, and more.

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
The examples are based on the Daytona [Python](../python-sdk/README.md), [TypeScript](../typescript-sdk/README.md), [Go](./README.md), [Ruby](../ruby-sdk/README.md), [Java](https://www.daytona.io/docs/en/java-sdk) **SDKs**, [CLI](../cli.md), and [API](../api/README.md) references. More examples are available in our [GitHub repository](https://github.com/daytonaio/daytona/tree/main/examples).

### Create a sandbox

Create a [sandbox](./sandboxes.md) with default settings.

```go
package main

import (
    "context"
    "fmt"
    "log"

    "github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
)

func main() {
    client, err := daytona.NewClient()
    if err != nil {
        log.Fatal(err)
    }

    sandbox, err := client.Create(context.Background(), nil)
    if err != nil {
        log.Fatal(err)
    }
    fmt.Printf("Sandbox ID: %s\n", sandbox.ID)
}
```

### Create and run code in a sandbox

Create a [sandbox](./sandboxes.md) and run code securely in it.

```go
package main

import (
    "context"
    "fmt"
    "log"

    "github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
)

func main() {
    client, err := daytona.NewClient()
    if err != nil {
        log.Fatal(err)
    }

    sandbox, err := client.Create(context.Background(), nil)
    if err != nil {
        log.Fatal(err)
    }

    response, err := sandbox.Process.ExecuteCommand(context.Background(), "echo 'Hello, World!'")
    if err != nil {
        log.Fatal(err)
    }
    fmt.Println(response.Result)
    sandbox.Delete(context.Background())
}
```

### Create a sandbox with custom resources

Create a sandbox with [custom resources](./sandboxes.md#resources) (CPU, memory, disk).

```go
package main

import (
    "context"
    "log"

    "github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
    "github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
)

func main() {
    client, err := daytona.NewClient()
    if err != nil {
        log.Fatal(err)
    }

    sandbox, err := client.Create(context.Background(), types.ImageParams{
        Image: daytona.DebianSlim(nil),
        Resources: &types.Resources{
            CPU:    2,
            Memory: 4,
            Disk:   8,
        },
    })
    if err != nil {
        log.Fatal(err)
    }
}
```

### Create an ephemeral sandbox

Create an [ephemeral sandbox](./sandboxes.md#ephemeral-sandboxes) that is automatically deleted when stopped.

```go
package main

import (
    "context"
    "log"

    "github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
    "github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
)

func main() {
    client, err := daytona.NewClient()
    if err != nil {
        log.Fatal(err)
    }

    autoStop := 5
    sandbox, err := client.Create(context.Background(), types.SnapshotParams{
        SandboxBaseParams: types.SandboxBaseParams{
            Ephemeral:        true,
            AutoStopInterval: &autoStop,
        },
    })
    if err != nil {
        log.Fatal(err)
    }
}
```

### Create a sandbox from a snapshot

Create a sandbox from a pre-built [snapshot](./snapshots.md) for faster sandbox creation with pre-installed dependencies.

```go
package main

import (
    "context"
    "log"

    "github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
    "github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
)

func main() {
    client, err := daytona.NewClient()
    if err != nil {
        log.Fatal(err)
    }

    sandbox, err := client.Create(context.Background(), types.SnapshotParams{
        Snapshot: "my-snapshot-name",
        SandboxBaseParams: types.SandboxBaseParams{
            Language: types.CodeLanguagePython,
        },
    })
    if err != nil {
        log.Fatal(err)
    }
}
```

### Create a sandbox with a declarative image

Create a sandbox with a [declarative image](./declarative-builder.md) that defines dependencies programmatically.

```go
package main

import (
    "context"
    "log"

    "github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
    "github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
)

func main() {
    client, err := daytona.NewClient()
    if err != nil {
        log.Fatal(err)
    }

    image := daytona.DebianSlim(nil).
        PipInstall([]string{"requests", "pandas", "numpy"}).
        Workdir("/home/daytona")
    sandbox, err := client.Create(context.Background(), types.ImageParams{
        Image: image,
    })
    if err != nil {
        log.Fatal(err)
    }
}
```

### Create a sandbox with volumes

Create a sandbox with a [volume](./volumes.md) mounted to share data across sandboxes.

```go
package main

import (
    "context"
    "log"

    "github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
    "github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
)

func main() {
    client, err := daytona.NewClient()
    if err != nil {
        log.Fatal(err)
    }

    volume, err := client.Volume.Get(context.Background(), "my-volume")
    if err != nil {
        volume, err = client.Volume.Create(context.Background(), "my-volume")
        if err != nil {
            log.Fatal(err)
        }
    }

    sandbox, err := client.Create(context.Background(), types.SnapshotParams{
        SandboxBaseParams: types.SandboxBaseParams{
            Volumes: []types.VolumeMount{{
                VolumeID:  volume.ID,
                MountPath: "/home/daytona/data",
            }},
        },
    })
    if err != nil {
        log.Fatal(err)
    }
}
```

### Create a sandbox with a Git repository cloned

Create a sandbox with a [Git repository](../typescript-sdk/git.md) cloned to manage version control.

```go
package main

import (
    "context"
    "fmt"
    "log"

    "github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
)

func main() {
    client, err := daytona.NewClient()
    if err != nil {
        log.Fatal(err)
    }

    sandbox, err := client.Create(context.Background(), nil)
    if err != nil {
        log.Fatal(err)
    }

    sandbox.Git.Clone(context.Background(), "https://github.com/daytonaio/daytona.git", "/home/daytona/daytona")
    status, err := sandbox.Git.Status(context.Background(), "/home/daytona/daytona")
    if err != nil {
        log.Fatal(err)
    }
    fmt.Printf("Branch: %s\n", status.CurrentBranch)
}
```

## See Also
- [Python SDK - getting-started](../python-sdk/getting-started.md)
- [TypeScript SDK - getting-started](../typescript-sdk/getting-started.md)
