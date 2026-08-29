# P41 behavior freeze

Status: **sealed** — integrated P41 delivery. The freeze is certified at one
Git SHA per delivery; the same-SHA receipts that seal it live in the private
evidence root (`.fleet-evidence/`), never in tracked docs.

The P41 freeze is **behavior-based**. It binds only externally observable and
durable behavior: public HTTP routes and OpenAPI shapes, SSE chunk
vocabulary and ordering, Runtime Event v1 identity/order/terminal semantics,
pi-tui projections, Turn settlement and replay, workspace and persistence
contracts, packaging metadata, and CLI behavior. It freezes behavior and
**never private Python structure**: internal filenames, module layouts,
helper boundaries, local classes, and file counts may change without
touching this contract. A future internal refactor is acceptable whenever
the behaviors below keep passing their lanes.

## Frozen behaviors and owners

| Frozen behavior | Public surface | Owner | Enforcing lanes |
| --- | --- | --- | --- |
| Exact `dspy==3.3.1` published dependency | Runtime version guard; locked install; wheel/sdist metadata | Release policy | `tests/unit/backend/packaging/`, `tests/unit/backend/rlm/` |
| Native RLM execution per Turn | Typed outputs, one native `dspy.RLM` per Turn, caller-owned interpreter lifecycle | RLM runner | `tests/unit/backend/rlm/` |
| Recursion contract | Root depth 0, one native child depth, Root-only batch, shared budgets, Sub-LM fallback | Recursion policy | `tests/unit/backend/rlm/`, `tests/live/backend/` |
| Turn orchestration | Claim/open/cancellation/deadline/heartbeat, stream settlement, replay determinism | Turn orchestration (`TurnRuntime`) | `tests/unit/backend/chat/` |
| Atomic Turn settlement | Commit, failure, cancellation settlement; result snapshot and Memory intents | Turn settlement (`RunLifecycleService`) | `tests/unit/backend/chat/`, `tests/unit/backend/test_committed_turn*.py` |
| Runtime Event vocabulary | Closed v1 event kinds, immutable identity, contiguous ordering, terminal semantics | Runtime Event recorder | `tests/freeze/test_public_stream_gate.py` |
| SSE transport | Closed projected chunk vocabulary and ordering; wire terminator per ending | SSE projector and stream route | `tests/freeze/test_public_stream_gate.py`, `make api-check` |
| pi-tui client | Live/durable projection convergence, timeline/cards/viewport behavior | pi-tui terminal client | `make tui-check`, tuistory interactive lanes |
| Public failure taxonomy | Closed sanitized HTTP/open-path/terminal categories, messages, phases | Public failure adapters | `tests/freeze/test_failure_taxonomy_golden.py` |
| Session Workspace and Project products | Explicit tool hosts, tool catalogs, path rules, delete/edit preconditions | Workspace/Project tool hosts | `tests/contracts/backend/test_p40_explicit_hosts` |
| Workspace Memory | Format, caps, digests, process-local append serialization | Workspace Memory host | `tests/unit/backend/workspace/test_memory_*.py`, `tests/unit/backend/daytona/test_workspace_memory*.py`, `tests/live/backend/` |
| Attachments and Artifacts | Upload/list/read, commit-gated publication, checksum integrity | Attachment/Artifact pipeline | `tests/unit/backend/test_attachment_*.py`, `tests/contracts/backend/` |
| Daytona provider lifecycle | Admission accounting, leases, cleanup and confirmed absence, Volume safety | Daytona runtime owner | `tests/live/backend/` (serial, `FLEET_LIVE=1`) |
| FastAPI and OpenAPI surface | Route set, one stream route, generated client types | API surface | `make api-check`, `tests/freeze/test_public_stream_gate.py` |
| Packaging | Wheel/sdist metadata, entry points, supported Python releases | Release machinery | `make build-release`, `make check-release` |
| CLI | `fleet cli` supervised loopback composition and bind guard; `fleet doctor daytona` probe; `fleet web`/`fleet-rlm serve-api` | CLI launchers | `tests/freeze/test_p41_cli_doctor_retention.py`, tuistory + live lanes |

## What is not frozen

Private implementation structure stays free to change: internal module and
helper boundaries, orchestration seams that never cross the public surface,
test-only instrumentation, and the number or names of source files. The
guard lane `tests/freeze/test_p41_behavior_over_structure.py` keeps the
validation suite bound to public surfaces, and the inventory lane
`docs/how-to-guides/p36-ownership-deletion-inventory.md` records which
internal owners changed under this contract.

## Explicitly unsupported: cross-Sandbox Memory append coordination

Cross-Sandbox Workspace Memory append coordination is **unsupported**. Memory
append serialization is process-local to one Fleet host: appends from
multiple host processes or independent Sandbox mounts are **not**
coordinated and may lose records; this limitation stays documented, not
certified. A completed append survives failed or cancelled Turns and Sandbox
replacement on the shared Volume — that is durability, not coordination.

## Drift control and evidence

- Behavior goldens (`tests/fixtures/p35e-golden-baseline.json` pins, the
  public failure-taxonomy golden, and the canonical stream fixture) are
  byte-pinned; changed golden bytes require an explicit recorded human
  decision in the baseline manifest.
- Deterministic freeze lanes live under `tests/freeze/` and run inside
  `make check`; `make api-check`, `make tui-check`, and `make check-docs`
  gate the generated contracts and these docs.
- Live Daytona lanes under `tests/live/backend/` run serially with
  `FLEET_LIVE=1` and write sanitized same-SHA receipts under
  `.fleet-evidence/receipts/`; the terminal cleanup proof ties each Runtime
  Event terminal to resource absence and admission baseline.
- Any genuine drift between code and this freeze without an inventory
  decision fails the gate; relabeling behavior silently is a contract
  violation.
