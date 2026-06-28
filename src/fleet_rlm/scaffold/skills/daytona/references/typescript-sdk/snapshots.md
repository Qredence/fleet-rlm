## Contents

- Create Snapshots
- Get a Snapshot by name
- List Snapshots
- Activate Snapshots
- Deactivate Snapshots
- Delete Snapshots
- Snapshot lifecycle
- Run Docker in a Sandbox
- Run Kubernetes in a Sandbox
- Default Snapshots
- See Also




Snapshots are reusable sandbox templates built from [Docker](https://www.docker.com/) or [OCI](https://opencontainers.org/) compatible images. Sandboxes can use snapshots to provide a consistent and reproducible environment for your dependencies, settings, and resources.

A snapshot defines the base operating system, language runtimes, system packages, and project-level setup that should exist when a sandbox starts. Instead of repeating bootstrap steps on every sandbox creation, you capture that setup once as a snapshot and reuse it.

You start with default snapshots for common stacks, or create custom snapshots for your own toolchain and constraints. Custom snapshots are useful when your workflow depends on specific package versions, private dependencies, startup scripts, or filesystem layout.

- **Snapshot SDKs**: [TypeScript](./snapshot.md), [Python](../python-sdk/sync/snapshot.md), [Ruby](../ruby-sdk/snapshot.md), [Go](../go-sdk/daytona.md#type-snapshotservice), [Java](https://www.daytona.io/docs/en/java-sdk/snapshot)
- **Snapshot API**: [RESTful API](../api/README.md#daytona/tag/snapshots) ([OpenAPI spec](https://www.daytona.io/docs/en/openapi.json)), [Toolbox API](../api/README.md#daytona-toolbox) ([OpenAPI spec](https://www.daytona.io/docs/en/toolbox-openapi.json))
- **Snapshot CLI**: [Mac/Linux/Windows](../cli.md)

## Create Snapshots

Daytona provides methods to create snapshots. You can create a snapshot from:

- [GPU snapshots](#gpu-snapshots) (for [GPU sandboxes](./sandboxes.md#gpu-sandboxes))
- [public images](#public-images)
- [local images](#local-images)
- [images from private registries](#images-from-private-registries)
- [declarative builder](#declarative-builder)

1. Navigate to [Daytona Snapshots ↗](https://app.daytona.io/dashboard/snapshots)
2. Click **Create Snapshot**
3. Enter the snapshot **`name`** and **`image`**

- **Snapshot name**: identifier used to reference the snapshot
- **Snapshot image**: base image for the snapshot, must include either a tag or a digest (e.g., **`ubuntu:22.04`**); the `latest`/`lts`/`stable` tags are not supported

4. Click **Create** to create a snapshot

**Python:**

```python
from daytona import Daytona, CreateSnapshotParams

daytona = Daytona()
snapshot = daytona.snapshot.create(
    CreateSnapshotParams(name="my-awesome-snapshot", image="python:3.12"),
)
```

**TypeScript:**

```typescript
import { Daytona } from "@daytona/sdk";

const daytona = new Daytona();
const snapshot = await daytona.snapshot.create({
  name: "my-awesome-snapshot",
  image: "python:3.12",
});
```

**Ruby:**

```ruby
require 'daytona'

daytona = Daytona::Daytona.new
snapshot = daytona.snapshot.create(
  Daytona::CreateSnapshotParams.new(name: 'my-awesome-snapshot', image: 'python:3.12')
)
```

**Go:**

```go
package main

import (
	"context"

	"github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
	"github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
)

func main() {
	client, _ := daytona.NewClient()
	ctx := context.Background()
	snapshot, logCh, _ := client.Snapshot.Create(ctx, &types.CreateSnapshotParams{
		Name:  "my-awesome-snapshot",
		Image: "python:3.12",
	})
	for range logCh {
	}
	_ = snapshot
}
```

**Java:**

```java
import io.daytona.sdk.Daytona;
import io.daytona.sdk.model.Snapshot;

final class CreateSnapshot {
    public static void main(String[] args) {
        try (Daytona daytona = new Daytona()) {
            Snapshot snapshot = daytona.snapshot().create("my-awesome-snapshot", "python:3.12");
        }
    }
}
```

**CLI:**

```bash
daytona snapshot create my-awesome-snapshot --image python:3.11-slim --cpu 2 --memory 4
```

**API:**

```bash
curl https://app.daytona.io/api/snapshots \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_SECRET_TOKEN' \
  --data '{
    "name": "my-awesome-snapshot",
    "imageName": "python:3.11-slim",
    "cpu": 2,
    "memory": 4
  }'
```

### GPU Snapshots

Daytona provides methods to create GPU snapshots.

GPU snapshots are used to create [GPU sandboxes](./sandboxes.md#gpu-sandboxes). Daytona provides a pre-built `daytona-gpu` snapshot for creating GPU sandboxes.

1. Navigate to [Daytona Snapshots ↗](https://app.daytona.io/dashboard/snapshots)
2. Click **Create Snapshot**
3. Enter the snapshot **`name`** and **`image`**
4. Select **`us-east-1`** region
5. Select the **`Allocate GPU`** checkbox
6. Specify the **`GPU type`**: **`NVIDIA H100`** **`NVIDIA RTX PRO 6000`**
7. Click **Create** to create a GPU snapshot

```typescript
import { Daytona } from "@daytona/sdk";

const daytona = new Daytona({ target: "us-east-1" });
const snapshot = await daytona.snapshot.create({
  name: "my-gpu-snapshot",
  image: "python:3.12",
  resources: { cpu: 1, memory: 1, disk: 1, gpu: 1 },
});
```

<a id="using-public-images"></a>
### Public images

Daytona supports creating snapshots from any publicly accessible image or container registry.

1. Navigate to [Daytona Snapshots ↗](https://app.daytona.io/dashboard/snapshots)
2. Click the **Create Snapshot** button
3. Enter the snapshot **`name`** and **`image`** of any publicly accessible image or container registry

**Python:**

```python
from daytona import Daytona, CreateSnapshotParams

daytona = Daytona()
daytona.snapshot.create(
    CreateSnapshotParams(name="my-awesome-snapshot", image="python:3.11-slim"),
    on_logs=lambda chunk: print(chunk, end=""),
)
```

**TypeScript:**

```typescript
import { Daytona } from "@daytona/sdk";

const daytona = new Daytona();
await daytona.snapshot.create(
  { name: "my-awesome-snapshot", image: "python:3.11-slim" },
  { onLogs: console.log },
);
```

**Ruby:**

```ruby
require 'daytona'

daytona = Daytona::Daytona.new
params = Daytona::CreateSnapshotParams.new(
  name: 'my-awesome-snapshot',
  image: 'python:3.11-slim'
)
snapshot = daytona.snapshot.create(params) do |chunk|
  print chunk
end
```

**Go:**

```go
package main

import (
	"context"
	"github.com/daytonaio/daytona/libs/sdk-go/pkg/daytona"
	"github.com/daytonaio/daytona/libs/sdk-go/pkg/types"
)

func main() {
	client, _ := daytona.NewClient()
	ctx := context.Background()
	snapshot, logChan, _ := client.Snapshot.Create(ctx, &types.CreateSnapshotParams{
		Name:  "my-awesome-snapshot",
		Image: "python:3.11-slim",
	})
	_ = snapshot
	for range logChan {
	}
}
```

**Java:**

```java
import io.daytona.sdk.Daytona;
import io.daytona.sdk.model.Snapshot;

public class App {
    public static void main(String[] args) {
        try (Daytona daytona = new Daytona()) {
            Snapshot snapshot = daytona.snapshot().create("my-awesome-snapshot", "python:3.11-slim");
        }
    }
}
```

**CLI:**

```bash
daytona snapshot create my-awesome-snapshot --image python:3.11-slim
```

**API:**

```bash
curl https://app.daytona.io/api/snapshots \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer YOUR_SECRET_TOKEN' \
  --data '{
    "name": "my-awesome-snapshot",
    "imageName": "python:3.11-slim"
  }'
```

<a id="using-local-images"></a>
### Local images

Daytona supports creating snapshots from local images or from local Dockerfiles.

To create a snapshot from a local image or from a local Dockerfile, use the [Daytona CLI](../cli.md#daytona-snapshot).

Daytona expects the local image to be built for AMD64 architecture. Therefore, the `--platform=linux/amd64` flag is required when building the Docker image if your machine is running on a different architecture.

1. Ensure the image and tag you want to use is available

```bash
docker images
```

2. Create a snapshot and push it to Daytona:

```bash
daytona snapshot push custom-alpine:3.21 --name alpine-minimal
```

Alternatively, use the `--dockerfile` flag under `create` to pass the path to the Dockerfile you want to use and Daytona will build the snapshot for you. The `COPY`/`ADD` commands will be automatically parsed and added to the context. To manually add files to the context, use the `--context` flag.

```bash
daytona snapshot create my-awesome-snapshot --dockerfile ./Dockerfile
```

<a id="using-images-from-private-registries"></a>
### Images from private registries

Daytona supports creating snapshots from images from [Docker Hub](#docker-hub), [Google Artifact Registry](#google-artifact-registry), [GitHub Container Registry](#github-container-registry-ghcr), [Amazon ECR](#amazon-elastic-container-registry-ecr) or other private container registries.

1. Navigate to [Daytona Registries ↗](https://app.daytona.io/dashboard/registries)
2. Click **Add Registry** and select your provider
3. Fill in the visible fields
4. Navigate to [Daytona Snapshots ↗](https://app.daytona.io/dashboard/snapshots)
5. Click **Create Snapshot**
6. Enter the snapshot **`name`** and the full **`image`** reference, including the registry host and repository (e.g. **`my-registry.com/<repo>/custom-alpine:3.21`**)

#### Docker Hub

Daytona supports creating snapshots from Docker Hub images.

1. Navigate to [Daytona Registries ↗](https://app.daytona.io/dashboard/registries),
2. Click **Add Registry** and select the **Docker Hub** tab
3. Input the following fields:
   - **Username**: your Docker Hub username (the account with access to the image)
   - **Personal Access Token**: a [Docker Hub PAT](https://docs.docker.com/security/access-tokens/) — not your account password
   - **Registry URL**: auto-filled with **`docker.io`** and not shown in the form
4. Create the snapshot using the full image reference, e.g. **`docker.io/<username>/<image>:<tag>`**

#### Google Artifact Registry

Daytona supports creating snapshots from images from Google Artifact Registry, authenticated with a [service account key](https://cloud.google.com/iam/docs/keys-create-delete) in JSON format.

1. Navigate to [Daytona Registries ↗](https://app.daytona.io/dashboard/registries),
2. Click **Add Registry** and select the **Google** tab
2. Input the following fields:
   - **Registry URL**: the base URL for your region (e.g. **`https://us-central1-docker.pkg.dev`**)
   - **Service Account JSON Key**: the contents of your service account key JSON file
   - **Google Cloud Project ID**: your GCP project ID
   - **Username**: auto-filled with **`_json_key`** (required by Google for service-account auth)
3. Create the snapshot using the full image reference, e.g.

    **`us-central1-docker.pkg.dev/<project>/<repo>/<image>:<tag>`**

<a id="github-container-registry-ghcr"></a>
#### GitHub Container Registry

Daytona supports creating snapshots from images from GitHub Container Registry.

1. Navigate to [Daytona Registries ↗](https://app.daytona.io/dashboard/registries),
2. Click **Add Registry** and select the **GitHub** tab
2. Input the following fields:
   - **GitHub Username**: the account with access to the image
   - **Personal Access Token**: a [GitHub PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) with **`read:packages`** scope (and **`write:packages`** / **`delete:packages`** if you'll push or delete)
   - **Registry URL**: auto-filled with **`ghcr.io`** and not shown in the form
3. Create the snapshot using the full image reference, e.g.

    **`ghcr.io/<owner>/<image>:<tag>`**

<a id="amazon-elastic-container-registry-ecr"></a>
#### Amazon Elastic Container Registry

Daytona pulls private ECR images via cross-account IAM role assumption — you create a role in your AWS account that trusts Daytona's broker principal, and Daytona assumes it on every pull to fetch a short-lived ECR token. No long-lived AWS credentials are shared, and no manual token rotation is needed.

You'll need two values:

- **Daytona Broker ARN**: `arn:aws:iam::967657494466:role/DaytonaEcrCredentialBroker` — the IAM principal Daytona uses to assume into your role. Self-hosted: substitute the IAM role your API pods assume (e.g. via IRSA).
- **External ID**: your Daytona organization ID, visible in the dashboard URL (`/dashboard/<orgId>/...`) and on your organization settings page.

##### 1. Create an IAM role in your AWS account

Create an IAM role with the trust and permissions policies below. Replace `<YOUR_EXTERNAL_ID>` with your organization ID.

Trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::967657494466:role/DaytonaEcrCredentialBroker" },
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {
        "sts:ExternalId": "<YOUR_EXTERNAL_ID>"
      }
    }
  }]
}
```

Permissions policy (read-only on ECR):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage"
    ],
    "Resource": "*"
  }]
}
```

Copy the ARN of the role you just created (e.g. `arn:aws:iam::123456789012:role/daytona-ecr-puller`).

##### 2. Register the registry in Daytona

1. On [Daytona Registries ↗](https://app.daytona.io/dashboard/registries), click **Add Registry** and select the **Amazon ECR** tab.
2. Fill in:
   - **Registry URL**: `<account_id>.dkr.ecr.<region>.amazonaws.com`
   - **Role ARN**: the role you created in step 1 — Daytona assumes it on every pull

   Password is not used for ECR — Daytona resolves credentials server-side by assuming the role you created in step 1, using your organization ID as the AssumeRole `ExternalId`.

##### 3. Create the snapshot

1. Navigate to [Daytona Snapshots ↗](https://app.daytona.io/dashboard/snapshots).
2. Click **Create Snapshot**.
3. Enter the snapshot name and the full image reference (e.g. `123456789012.dkr.ecr.us-east-1.amazonaws.com/my-repo/custom-alpine:3.21`).

##### Optional: harden the trust policy

Daytona sends a `daytona-<orgId>-pull` session name on every AssumeRole call. You can require it in your trust policy for CloudTrail audit visibility — add inside `Condition`:

```json
"StringLike": {
  "sts:RoleSessionName": "daytona-<YOUR_EXTERNAL_ID>-*"
}
```

<a id="using-the-declarative-builder"></a>
### Declarative builder

[Declarative Builder](./declarative-builder.md) provides a powerful, code-first approach to defining dependencies for Daytona Sandboxes. Instead of importing images from a container registry, you can programmatically define them using the Daytona [SDKs](./getting-started.md#sdks).

### Resources

Snapshots can be customized with specific [sandbox resources](./sandboxes.md#resources). By default, Daytona sandboxes use **1 vCPU**, **1GB RAM**, and **3GiB disk**. To view your available resources and limits, see [limits](../platform/limits.md) or navigate to [Daytona Limits ↗](https://app.daytona.io/dashboard/limits).

To set custom snapshot resources, use the `Resources` class.

```typescript
import { Daytona } from "@daytona/sdk";

const daytona = new Daytona();
const snapshot = await daytona.snapshot.create({
  name: "my-awesome-snapshot1123",
  image: "python:3.12",
  resources: { cpu: 2, memory: 4, disk: 8 },
});
```

### Regions

When creating a snapshot, you can specify the [region](./regions.md) in which it will be available. If not specified, the snapshot will be created in your organization's default region. When you later create a sandbox from this snapshot, you can use the snapshot's region as the target region for the sandbox.

```typescript
import { Daytona } from "@daytona/sdk";

const daytona = new Daytona();
const snapshot = await daytona.snapshot.create({
  name: "my-awesome-snapshotus",
  image: "python:3.12",
  regionId: "us",
});
```

## Get a Snapshot by name

Daytona provides an option to get a snapshot by name.

The following snippet returns the snapshot with the specified name:

```typescript
await daytona.snapshot.get('my-awesome-snapshot')
```

## List Snapshots

Daytona provides options to list snapshots and view their details.

The following snippet lists all snapshots on the first page with a limit of 10 snapshots per page.

```typescript
await daytona.snapshot.list(2, 10)
```

## Activate Snapshots

Snapshots automatically become inactive after 2 weeks of not being used. To activate an inactive snapshot:

1. Navigate to [Daytona Snapshots ↗](https://app.daytona.io/dashboard/snapshots)
2. Click the three dots at the end of the row for the snapshot you want to activate
3. Click the **Activate** button

```typescript
await daytona.snapshot.activate("my-awesome-snapshot")
```

## Deactivate Snapshots

Daytona provides an option to deactivate snapshots. Deactivated snapshots are not available for new sandboxes.

1. Navigate to [Daytona Snapshots ↗](https://app.daytona.io/dashboard/snapshots)
2. Click the three dots at the end of the row for the snapshot you want to deactivate
3. Click the **Deactivate** button

## Delete Snapshots

Daytona provides options to delete snapshots. Deleted snapshots cannot be recovered.

1. Navigate to [Daytona Snapshots ↗](https://app.daytona.io/dashboard/snapshots)
2. Click the three dots at the end of the row for the snapshot you want to delete
3. Click the **Delete** button

```typescript
await daytona.snapshot.delete(await daytona.snapshot.get("my-awesome-snapshot"))
```

## Snapshot lifecycle

A snapshot can have several different states. Each state reflects the snapshot's current status.

- **Pending**: the snapshot creation has been requested
- **Building**: the snapshot is being built
- **Pulling**: the snapshot image is being pulled from a registry
- **Active**: the snapshot is ready to use for creating sandboxes
- **Inactive**: the snapshot is deactivated
- **Error**: the snapshot creation failed
- **Build Failed**: the snapshot build process failed
- **Removing**: the snapshot is being deleted
> **Note:**
> Inactive snapshots cannot be used to create sandboxes. They must be explicitly [re-activated](#activate-snapshots) before use. When activated, the snapshot returns to `pending` state and is re-processed before becoming `active` again.

## Run Docker in a Sandbox

Daytona Sandboxes can run Docker containers inside them (**Docker-in-Docker**), enabling you to build, test, and deploy containerized applications. This is particularly useful when your projects have dependencies on external services like databases, message queues, or other microservices.

Agents can seamlessly interact with these services since they run within the same sandbox environment, providing better isolation and security compared to external service dependencies. The following use cases are supported:

- Run databases (PostgreSQL, Redis, MySQL) and other services
- Build and test containerized applications
- Deploy microservices and their dependencies
- Create isolated development environments with full container orchestration
> **Note:**
> Docker-in-Docker Sandboxes require additional resources due to the Docker daemon overhead. Consider allocating at least 2 vCPU and 4GiB of memory for optimal performance.

#### Create a Docker-in-Docker Snapshot

Daytona provides an option to create a snapshot with Docker support using pre-built Docker-in-Docker images as a base or by manually installing Docker in a custom image.

##### Using pre-built images

The following base images are widely used for creating Docker-in-Docker snapshots or can be used as a base for a custom Dockerfile:

- `docker:28.3.3-dind`: official Docker-in-Docker image (Alpine-based, lightweight)
- `docker:28.3.3-dind-rootless`: rootless Docker-in-Docker for enhanced security
- `docker:28.3.2-dind-alpine3.22`: Docker-in-Docker image with Alpine 3.22

##### Using manual installation

Alternatively, install Docker manually in a custom Dockerfile:

```dockerfile
FROM ubuntu:22.04
# Install Docker using the official install script
RUN curl -fsSL https://get.docker.com | VERSION=28.3.3 sh -
```

#### Run Docker Compose in a Sandbox

Docker Compose allows you to define and run multi-container applications. With Docker-in-Docker enabled in a Daytona Sandbox, you can use Docker Compose to orchestrate services like databases, caches, and application containers.

First, create a Docker-in-Docker snapshot using the [Daytona Dashboard ↗](https://app.daytona.io/dashboard/snapshots) or [CLI](../cli.md#daytona-snapshot-create) with one of the [pre-built images](#using-pre-built-images) (e.g., `docker:28.3.3-dind`). Then use the following snippet to run Docker Compose services inside a sandbox:

```typescript
import { Daytona } from '@daytona/sdk'

// Initialize the Daytona client
const daytona = new Daytona()

// Create a sandbox from a Docker-in-Docker snapshot
const sandbox = await daytona.create({ snapshot: 'docker-dind' })

// Create a docker-compose.yml file
const composeContent = `
services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
`
await sandbox.fs.uploadFile(Buffer.from(composeContent), 'docker-compose.yml')

// Start Docker Compose services
let result = await sandbox.process.executeCommand('docker compose -p demo up -d')
console.log(result.result)

// Check running services
result = await sandbox.process.executeCommand('docker compose -p demo ps')
console.log(result.result)

// Clean up
await sandbox.process.executeCommand('docker compose -p demo down')
```

## Run Kubernetes in a Sandbox

Daytona Sandboxes can run a Kubernetes cluster inside the sandbox. Kubernetes runs entirely inside the sandbox and is removed when the sandbox is deleted, keeping environments secure and reproducible.

##### Run k3s in a Sandbox

The following snippet installs and starts a k3s cluster inside a sandbox and lists all running pods.

```typescript
import { Daytona } from '@daytona/sdk'
import { setTimeout } from 'timers/promises'

// Initialize the Daytona client
const daytona = new Daytona()

// Create the sandbox instance
const sandbox = await daytona.create()

// Run the k3s installation script
const response = await sandbox.process.executeCommand(
  'curl -sfL https://get.k3s.io | sh -'
)

// Run k3s
const sessionName = 'k3s-server'
await sandbox.process.createSession(sessionName)
const k3s = await sandbox.process.executeSessionCommand(sessionName, {
  command: 'sudo /usr/local/bin/k3s server',
  async: true,
})

// Give time to k3s to fully start
await setTimeout(30000)

// Get all pods
const pods = await sandbox.process.executeCommand(
  'sudo /usr/local/bin/kubectl get pod -A'
)
console.log(pods.result)
```

## Default Snapshots

Daytona provides pre-built snapshots with fixed resource sizes for creating sandboxes.

| **Snapshot**         | **vCPU** | **Memory** | **Storage** | **GPU** |
| -------------------- | -------- | ---------- | ----------- | ------- |
| **`daytona-small`**  | 1        | 1GiB       | 3GiB        |         |
| **`daytona-medium`** | 2        | 4GiB       | 8GiB        |         |
| **`daytona-large`**  | 4        | 8GiB       | 10GiB       |         |
| **`daytona-gpu`**    | 1        | 1GiB       | 1GiB        | 1       |
| **`windows`**        | 2        | 8GiB       | 30GiB       |         |

Snapshots are based on the `daytonaio/sandbox:<version>` image.

### Python packages (pip)

- `anthropic` (v0.76.0)
- `beautifulsoup4` (v4.14.3)
- `claude-agent-sdk` (v0.1.22)
- `openai-agents` (v0.15.1)
- `daytona` (v0.134.0)
- `django` (v6.0.1)
- `flask` (v3.1.2)
- `huggingface-hub` (v0.36.0)
- `instructor` (v1.14.4)
- `keras` (v3.13.0)
- `langchain` (v1.2.7)
- `llama-index` (v0.14.13)
- `matplotlib` (v3.10.8)
- `numpy` (v2.4.1)
- `ollama` (v0.6.1)
- `openai` (v2.33.0)
- `opencv-python` (v4.13.0.90)
- `pandas` (v2.3.3)
- `pillow` (v12.1.0)
- `pipx` (v1.8.0)
- `pydantic-ai` (v1.47.0)
- `python-lsp-server` (v1.14.0)
- `requests` (v2.32.5)
- `scikit-learn` (v1.8.0)
- `scipy` (v1.17.0)
- `seaborn` (v0.13.2)
- `sqlalchemy` (v2.0.46)
- `torch` (v2.10.0)
- `transformers` (v4.57.6)
- `uv` (v0.9.26)

### Node.js packages (npm)

- `@anthropic-ai/claude-code` (v2.1.19)
- `@openai/codex` (0.128.0)
- `bun` (v1.3.6)
- `openclaw` (v2026.2.1)
- `opencode-ai` (v1.1.35)
- `ts-node` (v10.9.2)
- `typescript` (v5.9.3)
- `typescript-language-server` (v5.1.3)

## See Also
- [Python SDK - snapshots](../python-sdk/snapshots.md)
