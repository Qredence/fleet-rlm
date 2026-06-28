## Contents

- Create volumes
- Mount volumes
- Work with volumes
- Get a volume by name
- List volumes
- Delete volumes
- Share data between sandboxes
- Mount multiple volumes to one sandbox
- Multi-tenant isolation with subpaths
- Limitations
- Pricing & Limits
- See Also




Volumes are FUSE-based mounts that provide shared file access across Daytona sandboxes. They enable sandboxes to read from large files instantly - no need to upload files manually to each sandbox. Volume data is stored in an S3-compatible object store.

A sandbox reads and writes a mounted volume like any local directory, and the contents persist independently of the sandbox lifecycle. Use volumes to share datasets, model weights, build caches, or application state between sandboxes, scope per-user or per-tenant data with a `subpath`, and combine multiple volumes in the same sandbox at different mount paths.

- multiple volumes can be mounted to a single sandbox
- a single volume can be mounted to multiple sandboxes

## Create volumes

Daytona provides methods to create volumes using the [Daytona Dashboard ↗](https://app.daytona.io/dashboard/volumes) or programmatically using the Daytona [Python](../python-sdk/sync/volume.md), [TypeScript](./volume.md), [Ruby](../ruby-sdk/volume.md), [Go](../go-sdk/daytona.md#type-volumeservice), [Java](https://www.daytona.io/docs/en/java-sdk/volume-service) **SDKs**, [CLI](../cli.md#daytona-create), or [API](../api/README.md#daytona/tag/sandbox).

For persistent per-user, per-tenant, or per-workspace storage, use one shared volume per use case, environment, or project (for example a volume for staging and another for production), and set a dedicated `subpath` when you create each sandbox. The sandbox sees only that prefix inside the volume; it cannot access sibling subpaths.

This is the default pattern we recommend because it:

- stays within the per-organization volume [limits](#pricing--limits)
- avoids mounting a separate volume for every user or sandbox
- continues to provide strong isolation at the mount boundary

1. Navigate to [Daytona Volumes ↗](https://app.daytona.io/dashboard/volumes)
2. Click the **Create Volume** button
3. Enter the volume name

```typescript
import { Daytona } from "@daytona/sdk";

const daytona = new Daytona();
const volume = await daytona.volume.create("my-awesome-volume");
```

## Mount volumes

Daytona provides an option to mount a volume to a sandbox. Once a volume is created, it can be mounted to a sandbox by specifying it in the `CreateSandboxFromSnapshotParams` object. For per-user or multi-tenant data, pass `subpath` so only the specified folder inside the volume is visible at `mount_path`.

Mount the entire volume (omit `subpath`) when every sandbox that uses that volume should see the same tree, for example shared assets or single-tenant workloads.

Volume mount paths must meet the following requirements:

- **Must be absolute paths**: mount paths must start with `/` (e.g., `/home/daytona/volume`)
- **Cannot be root directory**: cannot mount to `/` or `//`
- **No relative path components**: cannot contain `/../`, `/./`, or end with `/..` or `/.`
- **No consecutive slashes**: cannot contain multiple consecutive slashes like `//` (except at the beginning)
- **Cannot mount to system directories**: the following system directories are prohibited: `/proc`, `/sys`, `/dev`, `/boot`, `/etc`, `/bin`, `/sbin`, `/lib`, `/lib64`

```typescript
import { Daytona } from '@daytona/sdk'
import path from 'path'

const daytona = new Daytona()
const volume = await daytona.volume.get('my-awesome-volume', true)
const mountDir = '/home/daytona/volume'

// Recommended for per-user / per-tenant data: one volume, unique subpath per sandbox
const sandbox = await daytona.create({
  language: 'typescript',
  volumes: [
    { volumeId: volume.id, mountPath: mountDir, subpath: 'users/alice' },
  ],
})

// Entire volume at mount path (omit subpath) when all sandboxes should share the same tree
const sandboxShared = await daytona.create({
  language: 'typescript',
  volumes: [{ volumeId: volume.id, mountPath: mountDir }],
})
```

## Work with volumes

Daytona provides an option to read from and write to the volume just like any other directory in the sandbox file system. Files written to the volume persist beyond the lifecycle of any individual sandbox.

The following snippet demonstrate how to read from and write to a volume:

```typescript
// Write to a file in the mounted volume using the Sandbox file system API
await sandbox.fs.uploadFile(
  Buffer.from('Hello from Daytona volume!'),
  '/home/daytona/volume/example.txt'
)

// When you're done with the sandbox, you can remove it
// The volume will persist even after the sandbox is removed
await daytona.delete(sandbox)
```

## Get a volume by name

Daytona provides an option to get a volume by its name.

```typescript
await daytona.volume.get('my-awesome-volume', true)
```

## List volumes

Daytona provides an option to list all volumes.

```typescript
await daytona.volume.list()
```

## Delete volumes

Daytona provides an option to delete a volume. Deleted volumes cannot be recovered.

The following snippet demonstrate how to delete a volume:

```typescript
await daytona.volume.delete(volume)
```

## Share data between sandboxes

Daytona provides an option to share data across sandboxes by mounting the same volume in each one. A producer sandbox writes to the volume and is then deleted; a separately created consumer sandbox mounts the same volume by ID and reads the data. Volume contents persist independently of any individual sandbox.

Sandboxes that mount the same volume see writes immediately, but FUSE-backed volumes are not transactional. If two sandboxes write to the same path concurrently, the last write wins. Coordinate access in your application when ordering matters.

```typescript
import { Daytona } from '@daytona/sdk'

const daytona = new Daytona()
const volume = await daytona.volume.get('shared-data', true)
const mountDir = '/home/daytona/volume'

// Producer: write data into the volume, then delete the sandbox
const producer = await daytona.create({
  language: 'typescript',
  volumes: [{ volumeId: volume.id, mountPath: mountDir }],
})
await producer.fs.uploadFile(Buffer.from('shared payload'), `${mountDir}/payload.bin`)
await daytona.delete(producer)

// Consumer: a separate sandbox mounts the same volume by ID and reads the data
const consumer = await daytona.create({
  language: 'typescript',
  volumes: [{ volumeId: volume.id, mountPath: mountDir }],
})
const data = await consumer.fs.downloadFile(`${mountDir}/payload.bin`)
console.log(data.toString())
```

## Mount multiple volumes to one sandbox

Daytona provides an option to mount more than one volume to a single sandbox by passing multiple entries in the `volumes` list. Use this pattern to combine shared assets, models, or datasets in one volume with separate per-application or per-user state in another, exposed at distinct mount paths.

```typescript
const sharedAssets = await daytona.volume.get('shared-assets', true)
const logs = await daytona.volume.get('logs', true)

const sandbox = await daytona.create({
  language: 'typescript',
  volumes: [
    { volumeId: sharedAssets.id, mountPath: '/home/daytona/assets' },
    { volumeId: logs.id, mountPath: '/home/daytona/logs' },
  ],
})
```

## Multi-tenant isolation with subpaths

Daytona provides an option to isolate per-tenant or per-user data inside a single shared volume by setting a unique `subpath` on each sandbox's volume mount. Each sandbox sees only files under its assigned subpath at `mount_path` and cannot read or write sibling subpaths within the same volume. This is the recommended pattern for multi-tenant workloads because it stays within the [per-organization volume limit](#pricing--limits) instead of creating one volume per tenant.

Isolation is enforced at the FUSE mount boundary. Each sandbox sees its assigned subpath as the volume root, so a sandbox mounted at `users/alice` cannot reach `users/bob` through relative paths such as `../bob`.

```typescript
const volume = await daytona.volume.get('tenants', true)
const mountDir = '/home/daytona/data'

// Tenant A
const aliceSandbox = await daytona.create({
  language: 'typescript',
  volumes: [{ volumeId: volume.id, mountPath: mountDir, subpath: 'users/alice' }],
})
await aliceSandbox.fs.uploadFile(Buffer.from("alice's data"), `${mountDir}/notes.txt`)

// Tenant B sees only its own subpath; alice's notes.txt is invisible
const bobSandbox = await daytona.create({
  language: 'typescript',
  volumes: [{ volumeId: volume.id, mountPath: mountDir, subpath: 'users/bob' }],
})
await bobSandbox.fs.uploadFile(Buffer.from("bob's data"), `${mountDir}/notes.txt`)
```

## Limitations

Since volumes are FUSE-based mounts, they can not be used for applications that require block storage access (like database tables). Volumes are generally slower for both read and write operations compared to the local sandbox file system.

## Pricing & Limits

Daytona volumes are included at no additional cost. Each organization can create up to 100 volumes, and volume data does not count against your storage quota.

You can view your current volume usage in the [Daytona Volumes ↗](https://app.daytona.io/dashboard/volumes).

## See Also
- [Python SDK - volumes](../python-sdk/volumes.md)
