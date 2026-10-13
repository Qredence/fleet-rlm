# Daytona resource ownership

Fleet uses concrete Daytona implementations. `DaytonaRuntime` coordinates root
Sessions, binding generations, fencing and shutdown. `provider.py` contains
existing SDK operations; `leases.py` contains admission, lease records and
confirmed cleanup. `children.py` owns disposable child records and factories;
`host_io.py` owns serialized host operations and warm reuse. No new protocol,
generic provider layer or cleanup scheduler is introduced.

## Transitions and release conditions

| Role | Acquisition and active ownership | Drain, failure and release |
| --- | --- | --- |
| Root | Runtime owns the acquisition task, admission permit and Session record. SQL binding generation plus workspace/session scope fence reuse. Each DSPy invocation constructs a fresh interpreter with its tools, history, context and execution hooks. | Revocation taints the root and force-stops it before worker drain. Unpublished/late leases remain retained. Stop confirmation is distinct from deletion; deletion-required resources release admission only after confirmed absence. While a Turn holds the root, a host heartbeat refreshes provider activity; three consecutive failures taint the root. |
| Child | `ChildSandboxes` owns the provider create and one cleanup record per returned Sandbox. The factory retains synchronous bridge futures until they settle. Children share existing admission and remain associated with their Session for fencing. Each child carries Session and Run labels and a provider TTL of its remaining deadline plus a margin. | Failed or late creation transfers into the same child cleanup records. Close is idempotent; failed deletion holds its permit and is retried from that record. Failed broker teardown is reported even when provider absence is confirmed. Volume-less semantic children retain their staging/harvest bounds. |
| Host I/O | `WorkspaceIO` owns operation serialization, context count, pending preparation cleanup and warm cache. Its `SandboxResource` retains the Sandbox and permit between warm operations. | Active operation groups drain before shutdown. Idle expiry, stale replacement and failed preparation use the same lease close. Unconfirmed deletion prevents replacement and remains retained. |

A timeout is not proof of remote cancellation or absence. The SDK client remains
open while provider work or resource ownership is unresolved. `SandboxResource`
owns one retained cleanup task; concurrent callers join that task. A caller deadline
limits its wait without cancelling cleanup. Failed cleanup retains the resource and
permit for retry. Deletion uses the pinned SDK's `delete(wait=True)` and confirms
absence before releasing capacity. Stop confirmation remains distinct from deletion.

An interpreter invocation owns its synchronous SDK client, Python context, and
host-tool broker. Shutdown deletes the context and closes the broker before
closing that client; failed cleanup retains the handles for retry. Application
storage instead uses local views of the composition-owned asynchronous client.
Run cleanup closes its storage view after owned work settles and before releasing
the invocation gate, including cancellation and failure paths. The shared client
remains open until composition disposal finishes its provider cleanup.

Ordinary SDK execution output is capped in the Sandbox before transfer. Reaching
that cap truncates output while execution continues; final submissions have a
separate bounded frame. Unexpected output beyond the host transport ceiling is
terminal, fences further actions, and taints the root for retirement. Disconnects
and output refusals never authorize replaying model code or its external effects.

## Authoritative state and indexes

- Root Session records own retained roots; the active-Run registry and binding
  authority index claims/generations rather than owning another Sandbox.
- Child cleanup records own returned child resources; retained factory futures
  represent bridge work and late delivery, not a second deletion authority.
- Retained Sandbox leases own their close and unresolved provider work. The shared
  lease collection indexes these owners. The host warm cache indexes reusable
  leases, and pending host tasks block replacement while preparation cleanup runs.
- Runtime tracked Sandbox IDs retain process-disposal responsibility until
  independently confirmed absence. They are deletion obligations rather than
  a second active lease.
- Runtime provider tasks retain outstanding asynchronous effects. Late root
  cleanup uses this collection rather than a separate late-task registry.
- The host active-lease set was removed: the operation count and retained lease
  collection already preserve shutdown ownership.

`lifecycle_counts()` derives active, acquiring, draining and quarantined counts
without retaining telemetry state. Sandbox references are deduplicated and the
most conservative observed state wins. These process-local counts and safe
cleanup logs are observations, not provider absence certificates.

## Verification boundaries

The acceptance receipt maps PRD 2 criteria to actual commands and evidence.
Deterministic fakes establish ownership, ordering and fault behavior. Real CLI
Turns and MLflow traces establish product execution and settlement; independent
Daytona probes establish stop/deletion state. Trace export never authorizes
commitment or permit release.

SQL task/memory cutover, migrations, network-policy waiver removal, event-model
extraction and installed-product bootstrap remain separate workstreams.
