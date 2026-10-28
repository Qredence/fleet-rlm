# Sandbox environment

Fleet defines the Python 3.13 snapshot in
[`sandbox/snapshots/definition.py`](../../src/fleet_rlm/sandbox/snapshots/definition.py).
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

The curated input is
[`config/daytona-snapshot-requirements.in`](../../config/daytona-snapshot-requirements.in).
Its header contains the uv command to regenerate the Linux/Python 3.13
[`snapshot-requirements.txt`](../../src/fleet_rlm/sandbox/snapshots/snapshot-requirements.txt)
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
snapshot change. Live Daytona work requires explicit authorization. The operator
setup action **Build snapshot** uses the packaged recipe and streams provider logs
and uses a 20-minute wait by default; pass a positive finite timeout or another
callback through the Python operator API in
`fleet_rlm.sandbox.snapshots.operations`. The TUI uses the FastAPI setup routes.

A wait timeout does not prove the remote build stopped. Inspect the existing
snapshot with **Verify** in `/setup` before deciding whether to retry; do not
blindly issue another create. Verification checks provider state and resource
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
