# Sandbox environment

[`sandbox/snapshots.py`](../../src/fleet_rlm/sandbox/snapshots.py) owns the Python
3.13 snapshot recipe, preflight and build/activation. Required floors are 4 vCPU,
8 GiB RAM and 8 GiB disk. Generated Python, file operations and Git run in Daytona;
credentials and host-mediated model/search clients stay on the server.

## Included tools

| Capability | Packages/commands |
| --- | --- |
| Data/math | NumPy, pandas, mpmath |
| Plots/documents | matplotlib, Pillow, openpyxl, pypdf |
| HTTP/HTML | requests, Beautiful Soup |
| Python | uv, pip, pytest, pytest-cov, Ruff |
| Repository/JS | Git, Node.js/npm, pnpm, TypeScript, ripgrep, jq |

The [curated requirements](../../config/daytona-snapshot-requirements.in) describe
lock regeneration. The packaged [lock](../../src/fleet_rlm/sandbox/snapshot-requirements.txt)
pins Python dependencies and hashes; the base image is digest-pinned. System
package versions resolve at build time. Projects use their own dependency manifests.

## Skills and host tools

[Bundled Skills](../../src/fleet_rlm/bundled_skills/) guide tabular analysis,
long-context search, delegation, Skill creation, repository inspection, web
research, document extraction and file editing. Optional dependencies are prepared
in isolated uv environments when needed; discovery does not install packages.

User Skills live in `.fleet/skills/`; `.agents/skills/` is excluded. Bundled names
are reserved: conflicting local/Volume copies are ignored with warnings.
`load_skill_module`, `run_skill_script` and `read_skill_reference` access validated
resources. Helpers run with sandbox permissions, not privileged server access.
Script runs use `/workspace`, allow 20 arguments of up to 4,096 bytes each, time
out after 60 seconds and return bounded output.

`web_search(query, max_results=5)` uses server-held Serper then You.com credentials,
returns at most ten bounded title/URL/snippet/provider records and charges one
shared budget unit when reaching a provider. Without keys it raises a configuration
error; the web-research Skill can fall back to keyless providers.

Native DSPy executes RLM/Flex through `SandboxSession`. Dataset handles enter the
guest through one-way serialization; forwarded views contain JSON metadata only.
`workspace.load_image(path)` returns a native `dspy.Image`. See the
[native DSPy boundary](evaluation.md#native-dspy-boundary).

## Lifecycle and provenance

A successful Turn keeps its warm root. Failure, cancellation or expiry fences
host work and stops children/root before acknowledgment. `stopped` means confirmed
cleanup; `unconfirmed` retains handles/intents and blocks writable reuse. Recovery
reconciles resources under the Session ownership epoch. Ambiguous code execution
is never retried. Stored history cannot restore Python variables.

Children use isolated inputs and shared Turn budgets. Only successful verified
outputs are promoted. Quota/collision checks happen before writes; identical bytes
can be reused, conflicting bytes are refused. A provider upload failure can still
leave a partial set: publication is not a provider transaction.

Preflight requires an active snapshot meeting floors before root creation.
Successes cache for 60 seconds; refresh failures invalidate prior success. Turns
never build or activate automatically. Recorded name, ID and resources come from
Daytona; definition digest identifies Fleet's recipe, while optional build digest
hashes provider-reported metadata. These fields propagate to traces/results/replays.
Provider TTL and idle limits apply even when Fleet's Turn deadline is disabled.

## Build and verify

Live snapshot operations require explicit authorization:

```bash
uv run fleet snapshot preflight
uv run fleet snapshot build
uv run fleet snapshot activate
```

A build wait timeout does not prove the remote build stopped. Inspect the existing
snapshot before retrying. Preflight verifies state/resources, not task correctness.
Before promotion, measure fresh startup, disk/image size, CSV/Excel/plot/PDF tasks,
command availability and optional Skill cold/cached preparation. Save snapshot IDs,
outputs and timings; offline tests do not establish provider behavior or speedups.
