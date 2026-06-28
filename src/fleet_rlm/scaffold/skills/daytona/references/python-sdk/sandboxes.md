## Contents

- Create Sandboxes
- Start Sandboxes
- Get Sandbox
- List Sandboxes
- Stop Sandboxes
- Pause Sandboxes
- Archive Sandboxes
- Recover Sandboxes
- Resize Sandboxes
- Fork Sandboxes
- Label Sandboxes
- Create Snapshot from Sandbox
- Delete Sandboxes
- Sandbox lifecycle
- Multiple runtime support
- Automated lifecycle management




Daytona provides **full composable computers** — **sandboxes** — for AI agents. Sandboxes are isolated runtime environments you can manage programmatically to run code. Each sandbox runs in isolation, giving it a dedicated kernel, filesystem, network stack, and allocated vCPU, RAM, and disk. Agents get access to a full composable computer where they can install packages, run servers, compile code, and manage processes.

Sandboxes have **1 vCPU**, **1GB RAM**, and **3GiB disk** by default. Organizations get a maximum sandbox resource limit of **4 vCPUs**, **8GB RAM**, and **10GB disk**.

Sandboxes can use [snapshots](./snapshots.md) to capture a fully configured environment (base operating system, installed packages, dependencies and configuration) to create new sandboxes.

Each sandbox has its own network stack with per-sandbox firewall rules. By default, sandboxes follow standard network policies, but you can restrict egress to a specific set of allowed destinations or block all outbound traffic entirely.

- **Sandbox SDKs**: [TypeScript](../typescript-sdk/sandbox.md), [Python](./sync/sandbox.md), [Ruby](../ruby-sdk/sandbox.md), [Go](../go-sdk/daytona.md#type-sandbox), [Java](https://www.daytona.io/docs/en/java-sdk/sandbox)
- **Sandbox API**: [RESTful API](../api/README.md#daytona/tag/sandbox) ([OpenAPI spec](https://www.daytona.io/docs/en/openapi.json)), [Toolbox API](../api/README.md#daytona-toolbox) ([OpenAPI spec](https://www.daytona.io/docs/en/toolbox-openapi.json))
- **Sandbox CLI**: [Mac/Linux/Windows](../cli.md)

## Create Sandboxes

Daytona provides methods to create sandboxes.

1. Navigate to [Daytona Sandboxes ↗](https://app.daytona.io/dashboard/sandboxes)
2. Click **Create Sandbox**
3. Click **Create** to create a sandbox

```python
from daytona import Daytona

daytona = Daytona()
sandbox = daytona.create()
```

### GPU Sandboxes

Daytona provides methods to create GPU sandboxes.

Daytona supports NVIDIA GPU devices for creating GPU sandboxes. Use GPU sandboxes for workloads such as model inference, fine-tuning, and CUDA-accelerated compute.

- **NVIDIA H100**
- **NVIDIA RTX Pro 6000**

Daytona provides a pre-built `daytona-gpu` snapshot for creating GPU sandboxes. Each GPU sandbox is ephemeral and supports up to **16 vCPUs**, **192GB RAM**, and **512GB disk**.

1. Navigate to [Daytona Sandboxes ↗](https://app.daytona.io/dashboard/sandboxes)
2. Click **Create Sandbox**
3. Select a GPU snapshot (**`daytona-gpu`**)
4. Select **`us-east-1`** region
5. Select **`ephemeral`** or set **`auto-delete interval`** to **`0`**
6. Click **Create** to create a GPU sandbox

```python
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

daytona = Daytona(DaytonaConfig(target="us-east-1"))
sandbox = daytona.create(
        CreateSandboxFromSnapshotParams(
            snapshot="daytona-gpu",
            auto_delete_interval=0
        ),
)
```

To create a GPU sandbox with custom GPU count and types:

1. Create a sandbox from an **`image`**
2. Set the **`auto-delete interval`** to **`0`** (ephemeral)
3. Set the **`GPU`** count to the number of GPUs you want
4. Specify the **`GPU type`**(s): **`H100`** **`RTX-PRO-6000`**

    The GPU type field accepts a single value or an ordered list of preferred types.

```python
from daytona import Daytona, CreateSandboxFromImageParams, Image, Resources, GpuType, DaytonaConfig

daytona = Daytona(DaytonaConfig(target="us-east-1"))
sandbox = daytona.create(
    CreateSandboxFromImageParams(
        image=Image.debian_slim("3.12"),
        auto_delete_interval=0,
        resources=Resources(
            gpu=1,
            gpu_type=[GpuType.H100, GpuType.RTX_PRO_6000],
        ),
    )
)
```

### Windows Sandboxes

Daytona provides methods to create Windows sandboxes.

Windows sandboxes are Windows OS runtime environments used to run Windows applications. Use Windows sandboxes to run Windows-specific tools and workflows on a consistent Windows baseline.

Daytona provides a pre-built `windows` snapshot for creating Windows sandboxes. The snapshot uses **2 vCPU**, **8GiB** memory, and **30GiB** disk.

1. Navigate to [Daytona Sandboxes ↗](https://app.daytona.io/dashboard/sandboxes)
2. Click **Create Sandbox**
3. Select a Windows snapshot (**`windows`**)
4. Select **`us`** region
5. Click **Create** to create a Windows sandbox

```python
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams

daytona = Daytona(DaytonaConfig(target="us"))
sandbox = daytona.create(
    CreateSandboxFromSnapshotParams(
        snapshot="windows",
    )
)
```


### Linked Sandboxes

Daytona provides methods to create linked sandboxes.

Linked sandboxes are attached to an existing parent sandbox at creation time.

Create the parent sandbox first, then create one or more children whose create request references the parent's sandbox ID. This records the relationship on the child sandbox as the linked sandbox ID. Omitting the linked sandbox parameter yields an unlinked sandbox.

- **Lifecycle**

  Linked sandboxes are always ephemeral and cannot be persisted or resumed after stop. The [auto-delete interval](#auto-delete-interval) must be exactly `0` on create; this is enforced, not a default. The [auto-stop interval](#auto-stop-interval) sets the idle period in minutes after which the child sandbox stops. Once stopped, linked children are auto-deleted. Deleting the parent deletes all of its linked children (cascade). One parent may have many linked children (1:N).
- **Networking**

  Linked sandboxes share an internal link network. Connections work in both directions: the parent can reach each child and each child can reach the parent. Every sandbox on the link network is registered under its sandbox name and ID as DNS aliases, so either works as the host. For example: `telnet LINKED_SANDBOX_ID 5555` from the parent reaches port `5555` on the linked child sandbox.

```python
from daytona import CreateSandboxFromSnapshotParams, Daytona

daytona = Daytona()

parent = daytona.create()

child = daytona.create(
    CreateSandboxFromSnapshotParams(
        linked_sandbox=parent.id,
        ephemeral=True,
    )
)

# The link network registers each sandbox under its name as a DNS alias
response = child.process.exec(f"curl http://{parent.name}:3000/")
```

### Ephemeral Sandboxes

Daytona provides methods to create ephemeral sandboxes.

Ephemeral sandboxes are automatically deleted once they are stopped. They are useful for short-lived tasks or testing purposes.

To create an ephemeral sandbox, set the `ephemeral` parameter to `True` when creating a sandbox. Setting [**`autoDeleteInterval: 0`**](#auto-delete-interval) (ephemeral) has the same effect.

```python
from daytona import Daytona, CreateSandboxFromSnapshotParams

daytona = Daytona()
params = CreateSandboxFromSnapshotParams(
    ephemeral=True,
    auto_stop_interval=5,  # delete after 5 minutes of inactivity
)
sandbox = daytona.create(params)
```

### Resources

Sandboxes have **1 vCPU**, **1GB RAM**, and **3GiB disk** by default. Organizations get a maximum sandbox resource limit of **4 vCPUs**, **8GB RAM**, and **10GB disk**.

| **Resource** | **Unit** | **Default** | **Minimum** | **Maximum** |
| ------------ | -------- | ----------- | ----------- | ----------- |
| CPU          | vCPU     | **`1`**     | **`1`**     | **`4`**     |
| Memory       | GiB      | **`1`**     | **`1`**     | **`8`**     |
| Disk         | GiB      | **`3`**     | **`1`**     | **`10`**    |

##### Pre-built snapshots

Daytona provides [pre-built snapshots](./snapshots.md#default-snapshots) with fixed resource sizes for creating sandboxes.

| **Snapshot**         | **vCPU** | **Memory** | **Storage** | **GPU** |
| -------------------- | -------- | ---------- | ----------- | ------- |
| **`daytona-small`**  | 1        | 1GiB       | 3GiB        |         |
| **`daytona-medium`** | 2        | 4GiB       | 8GiB        |         |
| **`daytona-large`**  | 4        | 8GiB       | 10GiB       |         |
| **`daytona-gpu`**    | 1        | 1GiB       | 1GiB        | 1       |
| **`windows`**        | 2        | 8GiB       | 30GiB       |         |

```python
from daytona import Daytona, CreateSandboxFromSnapshotParams

daytona = Daytona()
sandbox = daytona.create(
    CreateSandboxFromSnapshotParams(
        snapshot="daytona-medium",
    )
)
```

##### Custom resources

Daytona provides methods to create sandboxes with custom resources.

Use the `Resources` class to set custom sandbox resources. All resource parameters are optional and must be integers. If not specified, Daytona will use the default values. Maximum values are per-sandbox limits set at the organization level.

```python
from daytona import Daytona, CreateSandboxFromImageParams, Image, Resources

daytona = Daytona()
sandbox = daytona.create(
    CreateSandboxFromImageParams(
        image=Image.debian_slim("3.12"),
        resources=Resources(cpu=2, memory=4, disk=8),
    )
)
```

## Start Sandboxes

Daytona provides methods to start sandboxes.

1. Navigate to [Daytona Sandboxes ↗](https://app.daytona.io/dashboard/sandboxes)
2. Click the start icon (**▶**) next to the sandbox you want to start

```python
sandbox.start()
```

## Get Sandbox

Daytona provides methods to get a sandbox by ID or name.

```python
sandbox = daytona.get("my-sandbox-id-or-name")
```

## List Sandboxes

Daytona provides methods to list sandboxes.

```python
for sandbox in daytona.list():
    print(sandbox.id)
```

## Stop Sandboxes

Daytona provides methods to stop sandboxes.

Stopped sandboxes maintain filesystem persistence while their memory state is cleared. They incur
only disk usage costs and can be started again when needed. Sandboxes in a stopping or stopped state
will no longer accept requests.

The stopped state should be used when a sandbox is expected to be started again. Otherwise, it is recommended to stop and then archive the sandbox to eliminate disk usage costs.

1. Navigate to [Daytona Sandboxes ↗](https://app.daytona.io/dashboard/sandboxes)
2. Click the stop icon (**⏹**) next to the sandbox you want to stop

```python
sandbox.stop()
```

If you need a faster shutdown, use force stop (`force=true` / `--force`) to terminate the sandbox immediately. Force stop is ungraceful and should be used when quick termination is more important than process cleanup. Avoid force stop for normal shutdowns where the process should flush buffers, write final state, or run cleanup hooks.

Common use cases for force stop include:

- you need to reduce stop time and can accept immediate termination
- the entrypoint ignores termination signals or hangs during shutdown

## Pause Sandboxes

Daytona provides methods to pause sandboxes.

Pausing a sandbox keeps both filesystem state and memory persistence, so sandboxes can resume from in-memory runtime state. Compared to regular stop behavior, pause is useful for workloads with active in-memory context and state continuity.

Daytona supports pause functionality through VM-based runners. Pause is handled through the existing stop action. This means stop behaves as pause and preserves memory state, while force stop performs a full shutdown without preserving memory state.

## Archive Sandboxes

Daytona provides methods to archive sandboxes.

A sandbox must be stopped before it can be archived. When a sandbox is archived, the entire filesystem state is moved to a cost-effective object storage, making it available for an extended period.

Starting an archived sandbox takes more time than starting a stopped sandbox, depending on its size. It can be started again in the same way as a stopped sandbox.

```python
sandbox.archive()
```

## Recover Sandboxes

Daytona provides methods to recover sandboxes.

```python
sandbox.recover()
```

When a sandbox enters an error state, it can sometimes be recovered using the `recover` method, depending on the underlying error reason. The `recoverable` flag indicates whether the error state can be resolved through an automated recovery procedure.

Recovery actions are not performed automatically because they address errors that require **further user intervention**, such as freeing up storage space.

```python
# Check if the sandbox is recoverable
if sandbox.recoverable:
    sandbox.recover()
```

## Resize Sandboxes

Daytona provides methods to resize [sandbox resources](#resources) after creation.

On a running sandbox, you can increase CPU and memory without interruption. To decrease CPU or memory, or to increase disk capacity, stop the sandbox first. Disk size can only be increased and cannot be decreased.

Resizing updates the sandbox resource allocation (`cpu`, `memory`, and `disk`) for that sandbox only. CPU and memory control compute capacity for running workloads, while disk controls persistent filesystem capacity. Values must be integers and stay within your organization's per-sandbox resource limits.

```python
# Resize a started sandbox (CPU and memory can be increased)
sandbox.resize(Resources(cpu=2, memory=4))

# Resize a stopped sandbox (CPU and memory can change, disk can only increase)
sandbox.stop()
sandbox.resize(Resources(cpu=4, memory=8, disk=20))
sandbox.start()
```

To verify CPU and memory limits inside the sandbox after resizing, read `cgroup` values directly. Tools such as `nproc`, `free`, `top`, `htop`, `/proc/cpuinfo`, and `/proc/meminfo` read host-level values and do not reflect sandbox resource limits.

```bash
cat /sys/fs/cgroup/cpu.max      # "<quota> <period>" (cores = quota / period)
cat /sys/fs/cgroup/memory.max   # bytes
df -h /                         # disk
```

## Fork Sandboxes

Daytona provides methods to fork sandboxes.

Forking creates a duplicate of your sandbox's filesystem and memory, and copies it into a new sandbox. The new sandbox is fully independent: it can be started, stopped, and deleted without affecting the original. The sandbox must be in started state before forking.

Daytona tracks the parent-child relationship in a fork tree, so you can always trace a fork's lineage back to the sandbox it was created from. You can fork a fork, building out branches as needed. The parent sandbox cannot be deleted while it has active fork children.

1. Navigate to [Daytona Sandboxes ↗](https://app.daytona.io/dashboard/sandboxes)
2. Click the three-dot menu (**⋮**) next to the sandbox you want to fork
3. Select **Fork**

```python
# Fork sandbox through the Sandbox instance
forked = sandbox._experimental_fork(name="my-forked-sandbox")
```

To view the fork tree for a sandbox and all its related sandboxes:

1. Navigate to [Daytona Sandboxes ↗](https://app.daytona.io/dashboard/sandboxes)
2. Click the three-dot menu (**⋮**) next to a sandbox
3. Select **View Fork Tree**

The fork tree displays each sandbox in the hierarchy along with its current state and creation time, allowing you to trace the lineage of any fork back to its origin.

## Label Sandboxes

Daytona provides methods to set sandbox labels.

Setting labels replaces the full label set for the sandbox. Include all labels you want to keep in the request. If you omit an existing label, it will be removed.

```python
sandbox.set_labels({
    "team": "platform",
    "env": "staging",
})
```

## Create Snapshot from Sandbox

Daytona provides methods to create [snapshots](./snapshots.md) from sandboxes.

A snapshot captures an immutable, point-in-time copy of a sandbox's filesystem and memory that you can use as a base to create new sandboxes, effectively templating a known-good environment for reuse. You can think of it as a checkpoint you can restore from whenever you need a clean, identical starting point.

[Windows sandboxes](#windows-sandboxes) use the `includeMemory` parameter to control whether the snapshot also captures the sandbox's memory.

| **Include memory** | **Snapshot contents**     | **Required sandbox state** |
| ------------------- | ------------------------- | -------------------------- |
| **`false`** (default)   | Filesystem only           | Stopped                    |
| **`true`**              | Filesystem and memory     | Started                    |

```python
# Create snapshot from sandbox
sandbox._experimental_create_snapshot("my-sandbox-snapshot")
```

## Delete Sandboxes

Daytona provides methods to delete sandboxes.

1. Navigate to [Daytona Sandboxes ↗](https://app.daytona.io/dashboard/sandboxes)
2. Click the **Delete** button next to the sandbox you want to delete.

```python
sandbox.delete()
```

## Sandbox lifecycle

A sandbox can have several different states. Each state reflects the status of your sandbox.

- [**Creating**](#create-sandboxes): the sandbox is provisioning and will be ready to use
- [**Starting**](#start-sandboxes): the sandbox is starting and will be ready to use
- [**Started**](#start-sandboxes): the sandbox has started and is ready to use
- [**Stopping**](#stop-sandboxes): the sandbox is stopping and will no longer accept requests
- [**Stopped**](#stop-sandboxes): the sandbox has stopped and is no longer running
- [**Deleting**](#delete-sandboxes): the sandbox is deleting and will be removed
- [**Deleted**](#delete-sandboxes): the sandbox has been deleted and no longer exists
- [**Archiving**](#archive-sandboxes): the sandbox is archiving and its state will be preserved
- [**Archived**](#archive-sandboxes): the sandbox has been archived and its state is preserved
- [**Resizing**](#resize-sandboxes): the sandbox is being resized to a new set of resources
- [**Error**](#recover-sandboxes): the sandbox is in an error state and needs to be recovered
- **Restoring**: the sandbox is being restored from archive and will be ready to use shortly
- **Unknown**: the default sandbox state before it is created
- **Pulling Snapshot**: the sandbox is pulling a [snapshot](./snapshots.md) to provide a base environment
- **Building Snapshot**: the sandbox is building a [snapshot](./snapshots.md) to provide a base environment
- **Build Pending**: the sandbox build is pending and will start shortly
- **Build Failed**: the sandbox build failed and needs to be retried

The diagram demonstrates the states and possible transitions between them.


## Multiple runtime support

Daytona sandboxes support Python, TypeScript, and JavaScript programming language runtimes for direct code execution inside the sandbox. The `language` parameter controls which programming language runtime is used for the sandbox:

- **`python`**
- **`typescript`**
- **`javascript`**

If omitted, the Daytona SDK will default to `python`. To override this, explicitly set the `language` value when creating the sandbox.

## Automated lifecycle management

Sandboxes can be automatically stopped, archived, and deleted based on user-defined intervals. The intervals act as a TTL (time-to-live) mechanism for the sandbox. You can also refresh the last activity timestamp to explicitly signal activity when lifecycle behavior depends on inactivity intervals.

### Update sandbox last activity

Daytona provides methods to update a sandbox's last activity timestamp.

This updates the sandbox's recorded activity time without changing its runtime state. It is useful when your workflow is driven by external systems or background orchestration that may not reset inactivity tracking.

For example, if you run long-lived automation around a sandbox and want to avoid unintended auto-stop behavior, call this operation periodically to indicate that the sandbox is still actively used.

```python
sandbox.refresh_activity()
```

### Auto-stop interval

Daytona provides methods to set the auto-stop interval.

The auto-stop interval sets the amount of time after which a running sandbox will be automatically stopped. The auto-stop triggers even if there are internal processes running in the sandbox.

The system differentiates between "internal processes" and "active user interaction". Merely having a script or background task running is not sufficient to keep the sandbox alive.

- [What resets the timer](#what-resets-the-timer)
- [What does not reset the timer](#what-does-not-reset-the-timer)

The parameter can either be set to:

- a time interval in minutes
- `0`: disables the auto-stop functionality, allowing the sandbox to run indefinitely

If the parameter is not set, the default interval of `15 minutes` will be used.

```python
sandbox = daytona.create(CreateSandboxFromSnapshotParams(
    snapshot="my-snapshot-name",
    # Disables the auto-stop feature - default is 15 minutes
    auto_stop_interval=0,
))
```

##### What resets the timer

The inactivity timer resets only for specific external interactions:

- Updates to [sandbox lifecycle states](#sandbox-lifecycle)
- Network requests through [sandbox previews](./preview.md)
- Active [SSH connections](./ssh-access.md)
- API requests to the [Daytona Toolbox SDK](../api/README.md#daytona-toolbox)

##### What does not reset the timer

The following do not reset the timer:

- SDK requests that are not toolbox actions
- Background scripts (e.g., `npm run dev` run as a fire-and-forget command)
- Long-running tasks without external interaction
- Processes that don't involve active monitoring

If you run a long-running task like LLM inference that takes more than 15 minutes to complete without any external interaction, the sandbox may auto-stop mid-process because the process itself doesn't count as "activity", therefore the timer is not reset.

### Auto-archive interval

Daytona provides methods to set the auto-archive interval.

The auto-archive interval sets the amount of time after which a continuously stopped sandbox will be automatically archived. The parameter can either be set to:

- a time interval in minutes
- `0`: the maximum interval of `30 days` will be used

If the parameter is not set, the default interval of `7 days` will be used.

```python
sandbox = daytona.create(CreateSandboxFromSnapshotParams(
    snapshot="my-snapshot-name",
    # Auto-archive after a sandbox has been stopped for 1 hour
    auto_archive_interval=60,
))
```

### Auto-delete interval

Daytona provides methods to set the auto-delete interval.

The auto-delete interval sets the amount of time after which a continuously stopped sandbox will be automatically deleted. By default, sandboxes will never be automatically deleted. The parameter can either be set to:

- a time interval in minutes
- `-1`: disables the auto-delete functionality
- `0`: the sandbox will be deleted immediately after stopping

If the parameter is not set, the sandbox will not be deleted automatically.

```python
sandbox = daytona.create(CreateSandboxFromSnapshotParams(
    snapshot="my-snapshot-name",
    # Auto-delete after a sandbox has been stopped for 1 hour
    auto_delete_interval=60,
))

# Delete the sandbox immediately after it has been stopped
sandbox.set_auto_delete_interval(0)

# Disable auto-deletion
sandbox.set_auto_delete_interval(-1)
```

### Running indefinitely

Daytona provides methods to run sandboxes indefinitely.

By default, Daytona sandboxes auto-stop after 15 minutes of inactivity. To keep a sandbox running without interruption, set the auto-stop interval to `0` when creating a new sandbox:

```python
sandbox = daytona.create(CreateSandboxFromSnapshotParams(
    snapshot="my_awesome_snapshot",
    # Disables the auto-stop feature - default is 15 minutes
    auto_stop_interval=0,
))
```
