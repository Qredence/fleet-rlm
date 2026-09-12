# Testing Strategy

The primary gate covers the canonical backend, scripts, generated API artifacts,
documentation/harness, and maintained pi-tui client. Credentialed provider and
database lanes remain explicit.

## Suite inventory

The [Phase 3 consolidation ledger](../testing/phase3-consolidation-ledger.md)
records the pre-move ownership inventory, scenario dispositions, and validation.

Add regressions to the existing behavior-owning test file by default. Create a
new file only for a distinct contract, fixture/process boundary, generated-contract
lane, or live marker. Coverage is a coarse floor, not a reason to test every
internal branch. Retain independent concurrency, durability, privacy, and provider
assertions even when nearby tests use similar setup.

Shared setup belongs in small `tests/support` modules, existing domain fakes,
or a narrowly scoped fixture. Do not import collected test functions or fixtures
from another `test_*.py` module. Local SQLite and live PostgreSQL wrappers call
ordinary shared scenario functions; their fixture lifetimes and certification
claims remain distinct.

| Suite | Path | Purpose |
| --- | --- | --- |
| Backend unit | `tests/unit/backend/` | domain, adapters, configuration, routes, runtime modules |
| Script unit | `tests/unit/scripts/` | supported helper behavior |
| LiteLLM invariant | `tests/unit/test_litellm_invariant.py` | forbids direct application LiteLLM use |
| Backend contracts | `tests/contracts/backend/` | API, persistence, composition, and boundary contracts |
| Packaging/release | `tests/unit/backend/packaging/` | artifact metadata, clean installs, CLI guards, and VCS-free builds |
| End to end | `tests/e2e/` | canonical local process and request flows |
| TUI | `tools/fleet-tui/src/tests/`, `tools/fleet-tui/src/tui/tests/` | transport, projection, store, commands, rendering, terminal lifecycle |
| Database | tests marked `db` | explicit configured database behavior |
| Daytona MVP | `tests/live/backend/test_fleet_rlm_daytona_mvp.py` | complete real FastAPI/DSPy/Daytona flow, including Session Workspace durability across Sandbox replacement |
| Attachment/Artifact durability | `tests/live/backend/test_attachment_artifact_durability.py` | Volume persistence and committed content |

## Primary non-live gate

```bash
make check
```

The default pytest targets mask local live `FLEET_*` credentials so `.env`
cannot silently select provider composition. They install deterministic private
composition where required, run with at most four xdist workers by default, and
use xdist `loadfile` scheduling to keep module-scoped fixtures together. The
packaging/release matrix is intentionally excluded because it creates isolated
virtual environments and artifacts; run it explicitly with `make test-packaging`.

Package-wide coverage remains available locally over the same canonical
non-live corpus:

```bash
make test-daytona-cov
```

This target measures `src/fleet_rlm`, fails below 75%, prints missing lines,
and writes `.scratch/coverage/daytona.xml`. CircleCI instead measures coverage
inside the four `test-unit` shards, persists each shard's `.coverage.*` data,
and combines it in the downstream `coverage-gate` job, which enforces the same
75% floor. Coverage is not a substitute for the opt-in live Daytona durability
checks below.

`make check` includes:

- Ruff lint and format checks;
- `ty` for `src`;
- backend/script/LiteLLM/contract/end-to-end tests excluding live,
  benchmark, database, and packaging markers;
- `make api-check` for OpenAPI and generated TUI HTTP types;
- pi-tui format, lint, type, and Vitest checks;
- codebase-tree, dependency-boundary, and documentation/harness checks.

CircleCI enforces the same non-live surface: the `ci` workflow runs `quality`
(on the Node-bearing `cimg/python:*-node` executor so `api-check` can run
openapi-typescript there: release, docs, security, dependency, `api-check`,
and `stream-check`), `lint-typecheck`, the four-way `test-unit` job (unit,
contract, freeze, and E2E atoms through `pytest-unit`, with per-shard
coverage), `coverage-gate`, lightweight `python-compat-311` /
`python-compat-312` / `python-compat-313` jobs (lock/install, import check,
and `tests/unit/backend` + `tests/contracts/backend` only, through the
`pytest-compat` testsuite with the same first-flake `max-auto-rerun`
containment as `pytest-unit`), and the `tui` job (pnpm format, lint,
typecheck, and Vitest against the maintained client). Python 3.13 remains the
full gate image; 3.11/3.12 certify declared support without duplicating
Daytona coverage or the canonical E2E atoms. Packaging/install certification
runs in the release package gate rather than every unit shard. The opt-in
`deploy-pypi` bridge is attached to this workflow and runs only on `main`
after every listed quality, test, compatibility, coverage, and TUI gate.

`git diff --check` is required separately. The packaging lane is intentionally
separate from the fast gate and runs serially to avoid build-metadata races:

```bash
make test-packaging
```

Useful focused commands are:

```bash
uv run pytest tests/unit/backend tests/unit/scripts tests/contracts/backend tests/e2e -q \
  -m "not live_llm and not live_daytona and not benchmark and not db and not packaging"
uv run ruff check src tests scripts migrations
uv run ty check src
make api-check
pnpm --dir tools/fleet-tui run test
git diff --check
```

For separation-of-concerns changes, keep boundary checks close to the
production seam: composition inventory tests live in
`tests/unit/backend/test_live_composition.py`, Turn execution tests
in `tests/unit/backend/chat/test_turn_coordinator_execution.py`, binding repository tests
in `tests/unit/backend/test_sandbox_binding_repository.py`, and pure broker
source and transport tests in `tests/unit/backend/daytona/test_broker.py`. Include
the claim-heartbeat, cleanup, claim-parity, live-preparation, orphan-cleanup,
broker-binding, and interpreter-observation suites when changing lifecycle or
provider ownership.

The TUI suite observes the application through an injected deterministic
terminal. It covers strict stream state, live/durable ordering, atomic hydration,
commands and Skill selection, cancellation, complete static rendering,
alternate-screen follow-end scroll (`viewport-scroll.test.ts`), large-history
render cost (`transcript.bench.ts`), and cleanup.

## Documentation and bundled Skills

For authored guides and agent instructions, run `make check-docs` and
`git diff --check`. The documentation gate checks the generated profile matrix,
internal documentation links and reachability, CLI/OpenAPI sanity, the root
agent-guide line budget, guide/Claude-import integrity, documented Make targets,
development-skill reference reachability, and script inventory/help. It does not verify every
prose claim, external URL, or historical receipt.

Bundled Skill Markdown is loaded by the runtime. After correcting its bodies
or manifested references, also run the existing Skill catalog, manifest, tools,
resolver, API, and Turn-selection contracts. Do not turn a documentation refresh
into an implicit live-provider or certification campaign.

## Database gate

Alembic owns live schema creation. Against an explicitly configured empty
database:

```bash
uv run alembic upgrade head
uv run alembic check
```

Private deterministic tests may create ephemeral schemas explicitly.

## Credentialed Daytona gates

### Focused certification matrix

Run only lanes affected by a change, including changes to their shared support
modules. Every row proves a provider-specific fact; deterministic scenario replay
does not substitute for its live evidence. Keep one matched quality/performance
campaign through the existing benchmark helpers, rather than repeating campaign
accounting in individual pytest modules.

| Contract | Operator entry point / evidence | Why live evidence is necessary |
| --- | --- | --- |
| Context containment and whole-Sandbox deletion | `test_daytona_containment.py`, `test_daytona_deletion_lifecycle.py`; explicitly requested bounded evidence | Provider process survival and deletion observation cannot be inferred from fake responses. |
| Snapshot capabilities, Session host tools, recursive child | `scripts/live_p27_snapshot_verify.py`; aggregate JSON receipt | Both immutable images must import and execute through real Daytona and the configured LM. |
| Recursive batch, cancellation, deadline cleanup | Corresponding `tests/live/backend/test_daytona_*.py` canaries with `FLEET_LIVE_EVIDENCE_PATH` | Concurrent provider leases and in-flight remote cleanup cross the process boundary. |
| Workspace, attachment, artifact and memory durability | Existing MVP and durability/memory canaries; per-case JSON receipts | Mounted bytes, child isolation and replacement-Sandbox continuity depend on the provider. These are separate contracts from snapshot imports. |
| PostgreSQL contention and migration rehearsal | `scripts/benchmarks/certify_postgres.py --query-plans`; JSON receipt after Alembic rehearsal on an owned test database | Real PostgreSQL locking, compare-and-swap and planner behavior differ from SQLite. Record disposable versus configured/deployed provenance. |
| Configured MLflow export | `scripts/benchmarks/certify_mlflow.py --backend configured`; JSON receipt | Backend authentication, export and retrieval cannot be proved by fail-soft unit mocks. |
| Matched quality/performance | Existing benchmark campaign helpers and fixed dataset; comparison receipt | Real model quality, latency and cost require matched provider runs. Fixture-only Phase 6 cases are not a completed campaign. |

The live pytest opt-out contract is exercised in an isolated subprocess: all
live cases must skip without operator opt-in. Stable verifier arguments, pytest
node IDs, evidence environment variables and receipt schemas remain supported.
Sharing fixtures must not change the meaning of a previously recorded receipt.

Live pytest suites remain separately marked and require explicit live test
environment setup. The live verifier scripts instead use `runtime.live_enabled`
from the selected TOML policy (true by default; set it to `false` to fail
closed) and still require canonical credentials:

```bash
FLEET_LIVE=1 uv run python scripts/benchmark_daytona_lifecycle.py \
  --output .scratch/daytona-lifecycle-benchmark.json
FLEET_LIVE=1 uv run pytest tests/live/backend/test_fleet_rlm_daytona_mvp.py -q -n 0 --timeout=900
FLEET_LIVE=1 uv run pytest tests/live/backend/test_attachment_artifact_durability.py -q -n 0
```

The lifecycle benchmark always runs three warmups and twenty measured cycles
against the configured immutable Snapshot and Workspace-scoped Volume mount.
Only a create-through-first-execution p95 at or below ten seconds with all
twenty measured Sandboxes deleted selects per-Turn lifecycle. A missing,
partial, slower, or cleanup-failing receipt retains Session Sandboxes.

The complete release-oriented verifier loads `.env` with `override=False`, so
existing process exports win:

```bash
uv run python scripts/live_daytona_verify.py \
  --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json
```

Select the intended provider profile through `[config] default_profile` before
this gate; the shipped default is `daytona-recursive`. The [profile matrix](../reference/profile-matrix.md)
identifies the required provider values. The verifier requires the committed
configured Root and Sub policy roles, records a passing receipt at
the exact candidate SHA, verifies provider cleanup and secret isolation, and
must be paired with same-SHA CI, local release, and human attestations before
promotion. Historical receipts do not prove a later tip.

## Security, packaging, and release

```bash
make check-security
make check-release
make build-release
git diff --check
```

`make build-release` builds the Python distributions and validates wheel content
and metadata. These lanes do not replace the primary repository gate.
