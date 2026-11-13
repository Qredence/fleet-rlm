# Sandbox environment

Fleet defines the Python 3.13 snapshot in
[`sandbox/snapshots.py`](../../src/fleet_rlm/sandbox/snapshots.py).
That module owns the recipe, readiness cache/provenance, and explicit build/activate
operations. Configuration and credential resolution for operator actions remain
lazy; importing metadata neither initializes a provider client nor builds an image.
The resource floors remain 4 vCPU, 8 GiB RAM, and 8 GiB disk. Generated code,
package preparation, file access, and Git commands execute in Daytona; credentials
and host-mediated model clients remain on the server.

## Default packages

The default snapshot contains these directly selected Python packages:

| Capability | Packages |
| --- | --- |
| Data and mathematics | NumPy, pandas, mpmath |
| Plotting and basic documents | matplotlib, Pillow, openpyxl, pypdf |
| HTTP and HTML | requests, Beautiful Soup |
| Python coding | uv, pip, pytest, pytest-cov, Ruff |

Git, Node.js/npm, pnpm, TypeScript, ripgrep, and jq are available as commands.
Existing projects should use their own dependency manifests for project-specific
requirements. Fleet's workspace helper and ordinary Python file APIs already
provide sandbox filesystem access; Git CLI commands run through subprocesses.

Turn preparation spans report character counts for the task, workspace manifest,
Skill catalog, memory summary, Session history, and dataset preview. These are
input-size indicators, not per-model token attribution. The
`sandbox.workspace_inventory` and `sandbox.artifact_collection` spans separately
time managed file listing and evidence collection and report counts, not file
names or contents. Model-call spans retain their existing usage and duration data.

Within an active trace, workspace transfers expose separate remote-operation spans:
`sandbox.workspace_metadata` validates source paths and sizes;
`sandbox.workspace_prepare` validates destinations and creates parent directories;
`sandbox.workspace_download` and `sandbox.workspace_upload` time batched transfers.
These retain the surrounding `sandbox.input_read`, `sandbox.input_write`, and
`sandbox.input_copy` spans. Each operation records `files_requested`, `attempts`,
and `retry_delay_seconds` (the cumulative requested backoff); transfer spans also
record `bytes_requested`. Failed operations retain their attempt counts and report
`completed: false`. Counts exclude payloads, filenames, and credentials. Span
durations include retries and their backoff; nested durations must not be summed.
The finer spans distinguish slow path checks from slow transfers before choosing
an optimization. File validation, child isolation, and success-only input receipts
still apply. Ambiguous generated-code execution is never retried.

Local computation timers must include materializing lazy values, representation
conversion, and answer extraction. They measure a different interval from a
host-observed sandbox execution or a full Turn. The DSPy RLM/Flex program generates
task-specific computation and verification code and executes it in the Daytona
REPL. Shared instructions guide timing and stopping behavior across tasks.

Semantic `llm_query_batched` calls delegate to injected native DSPy tools first.
`jev_decide_batched` handles batched item decisions with discrete choices or boolean
`Noul` classification. The outer Flex fallback reserves the whole prompt list once and
runs at most eight workers, each with a separate copy of the submitting Turn's ContextVars.
Results remain in input order and worker exceptions propagate. Skill relevance
batches use native DSPy boolean `Noul` classification with `timeout=0` to disable
speculative straggler resubmission; Fleet's independent Turn deadline still applies.
These logical budget units differ from observable model requests and token usage.

Flex/RLM and explicit children use native `XMLAdapter` execution by default (with
`ChatAdapter` and `JSONAdapter` available via configuration) and native wire serialization.
The guest environment runs with preloaded DSPy (`dspy.primitives.sandbox_guest`).
The host's `WorkspaceDataset` uses the native one-way `SandboxSerializable` contract
to create a file-backed `WorkspaceDatasetView` in guest Python. Forwarding that view
to a nested agent or host tool carries only JSON metadata (`path`, description, size,
and digest), never reconstruction code or file contents. A nested agent can use
`workspace.read_text(context['path'])` or `WorkspaceDatasetView(context)` to access
its copied input. DSPy's host decoder remains unmodified and rejects guest codecs.
`workspace.load_image(path)` loads workspace images as native `dspy.Image` objects.
Valid native responses are never preprocessed, and Fleet makes no corrective request.
The direct route retains its separate adapter policy. See the
[native DSPy implementation report](../runbooks/native-dspy-integration.md)
for offline coverage and the remaining compatibility gate.

The curated input is
[`config/daytona-snapshot-requirements.in`](../../config/daytona-snapshot-requirements.in).
Its header contains the uv command to regenerate the Linux/Python 3.13
[`snapshot-requirements.txt`](../../src/fleet_rlm/sandbox/snapshot-requirements.txt)
lock. The lock pins the complete Python dependency closure and artifact hashes;
image builds require hashes and binary wheels and discard the pip download cache.
The Python base image is pinned by OCI digest. The definition digest also records
system package names and pinned npm tools. Debian repositories still resolve
system package versions at build time: this recipe is not a claim of a
byte-for-byte reproducible OS image.

## Optional capabilities through Skills

Bundled Skills use the existing catalog, activation, resource readers, and
session synchronization. A user Skill with the same name in `.fleet/skills/`
continues to override the bundled version; `.agents/skills/` is excluded.
Discovery and activation do not install packages.

## Bundled task Skills

Fleet bundles a small set of Skills that guide common file-analysis and agent
workflows: [tabular analysis](../../src/fleet_rlm/bundled_skills/tabular-analysis/SKILL.md),
[long-context search](../../src/fleet_rlm/bundled_skills/long-context-search/SKILL.md),
[delegation](../../src/fleet_rlm/bundled_skills/delegation/SKILL.md),
[Skill authoring](../../src/fleet_rlm/bundled_skills/skill-creator/SKILL.md), and
[public repository inspection](../../src/fleet_rlm/bundled_skills/repository-inspection/SKILL.md).
They are instructions and optional resources, not server-side tools or automatic
package installers. User Skills in `.fleet/skills/` can extend them and override
a bundled Skill by name. `.agents/skills/` remains developer guidance and is not
synchronized.

The repository inspection Skill uses a commit-pinned GitHub source archive in
temporary sandbox storage. Its measured comparison with Daytona Git acquisition
is recorded in the [comparison runbook](../runbooks/daytona-repository-comparison.md);
the result is specific to that repository, commit, snapshot, and run.

## Preflight and recorded identity

### Unconfirmed cleanup and writable reuse

Cancellation fences host replies, attempts root interruption before serial child
deletion, and attempts every required cleanup even if another attempt fails.
`stopped` means all required cleanup was confirmed; `unconfirmed` preserves the
unresolved provider handle or allocation labels. Interpreter contexts are invalidated
in either case. Resetting a Turn budget does not remove the cleanup fence.

While cleanup remains unresolved, Fleet refuses sandbox provisioning and workspace
access through the manager. A later `shutdown()` retries the retained stop/delete
action. For an ambiguous create, exactly one label-matching resource can be adopted
for cleanup; an empty or ambiguous lookup remains unconfirmed. Fleet never reruns
interrupted generated code as part of cleanup.

Session eviction fails while retaining the fenced runtime and ownership lease if
shutdown is unconfirmed or raises. Retrying eviction, or admission after lease loss,
retries that cleanup before reconstruction. Registry shutdown attempts every runtime
and retains unresolved ones rather than reporting successful release. Confirmed
resources may be released independently.

This is the F0 same-process guard. It does not survive process exit or establish
cross-worker restart safety. Do not enable unattended restart on this basis. Keep
the guard during rollback, or deny writable admission until unresolved resources
are reconciled. Durable recovery and atomic child publication remain separate
gates in [PRD F](../archive/fleet-rlm-prds/F-cleanup-publication.md).

### Durable ownership and admission

Each Session tracks `ownership_epoch` on `fleet_lite_sessions` and normalized
per-resource intent rows in `fleet_lite_sandbox_resources` (see
[database](database.md)). Acquiring the Session lease advances its epoch, and
every resource mutation is compare-and-set against that epoch, so a late reply
from a previous owner cannot clear a newer intent. Before a runtime is admitted,
recorded resources are reconciled against the provider: a confirmed stop or delete
removes the entry, and a provider not-found answer is also confirmation because a
resource the provider has forgotten cannot still write. Any other provider
failure leaves the Session fenced. A Session with no outstanding resources does
not contact the provider at all.

Turn completion, failure, and claim writes carry the same epoch, so a stale owner
cannot finalize a Turn. Turns left `running` after a takeover are recorded as
failed with an explicit no-replay result; interrupted generated code is never
rerun.

### Child output promotion

A child's changed outputs are promoted as one set. The complete changed set is
enumerated and validated — count, aggregate bytes, individual size, duplicate and
colliding destinations — before the first public write, and the quotas are
enforced while reading so an oversized child is refused without buffering its
whole output on the host. A destination that already holds identical bytes is a
completed publication and is skipped, so a retried promotion is idempotent; a
destination holding different bytes is refused. Quota and collision failures
produce zero public writes. A provider upload failure can still leave a partial
set: the batch is not one provider transaction, and this is not an atomic
publication guarantee.

### Snapshot preflight

Preflight requires an active snapshot meeting all resource floors before creating
a root sandbox. Successes are cached for 60 seconds by credentials, organization,
and name. Ordinary concurrent requests share one lookup. Explicit refresh starts
a fresh lookup; refresh failure evicts previous success. Clearing the cache
detaches in-flight lookups so old results cannot repopulate or evict newer cache
entries. Existing sandboxes retain their originally recorded identity.

Missing snapshots, inactive snapshots, builds in progress, and lookup failures
have different recovery messages. Provider exception bodies are excluded from
client-visible refusals. Fleet never activates or rebuilds a snapshot
automatically during a Turn.

Name, provider ID, and resources describe what Daytona resolved.
`snapshot_definition_digest` is Fleet's intended recipe digest for the
repository-owned name; other names record `null` because their recipe is unknown.
Optional `snapshot_build_digest` hashes provider-reported image/build metadata,
excluding raw Dockerfile contents and timestamps. It is evidence about the
reported build, not an installed-package inspection. The same fields propagate
to Turn traces, stored results, completion/failure events, and selected replays.
Older records without the optional build digest remain readable.

## Build and promotion

The versioned definition must be built and validated before deploying the default
snapshot change. Live Daytona work requires explicit authorization. Run
`fleet snapshot build` (which calls `fleet_rlm.sandbox.snapshots.build_snapshot`,
streaming provider logs with a 20-minute default wait) to build from the packaged
recipe, or call the Python operator API directly to pass a custom finite timeout
or callback.

A wait timeout does not prove the remote build stopped. Inspect the existing
snapshot with `fleet snapshot preflight` (or `fleet doctor`) before deciding whether
to retry; do not blindly issue another create, and use `fleet snapshot activate`
for an existing inactive snapshot. Verification checks provider state and resource
floors, not Python imports or task correctness.

Before promotion, compare the current snapshot and candidate using saved snapshot
IDs and measured results for:

- Fresh sandbox creation and first Python execution time.
- Snapshot/image size and available disk after package installation.
- CSV/Excel processing, a plot, and a basic PDF operation.
- Git and Python/JS/TS command availability.
- One optional Skill's cold preparation versus subsequent cached execution.

Use task-representative outputs and actual timings; offline checks and package
counts do not establish startup improvements or provider behavior.
