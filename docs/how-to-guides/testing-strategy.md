# Testing Strategy

The primary gate covers the canonical backend, scripts, generated API artifacts,
documentation/harness, and maintained pi-tui client. Credentialed provider and
database lanes remain explicit.

## Suite inventory

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
composition where required, run with at most two xdist workers by default, and
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
- codebase-tree and documentation/harness checks.

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

## Database gate

Alembic owns live schema creation. Against an explicitly configured empty
database:

```bash
uv run alembic upgrade head
uv run alembic check
```

Private deterministic tests may create ephemeral schemas explicitly.

## Credentialed Daytona gates

Live pytest suites remain separately marked and require explicit live test
environment setup. The Phase 1 verifier and Prime Oolong runner instead use
`runtime.live_enabled` from the selected TOML policy (true by default; set it
to `false` to fail closed) and still require canonical credentials:

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
`databricks-deepseek-v4-flash-0731` Root and Sub roles, records a passing receipt at the exact
candidate SHA, verifies provider
cleanup and secret isolation, and must be paired with same-SHA CI, local
release, and human attestations before promotion. Historical receipts do not
prove a later tip.

### P39c live-evidence receipts and the receipts-archive convention

The P39c recursion certification lanes (`tests/live/backend/test_p39c_*_live.py`)
write same-SHA receipts plus one shared observed-Sandbox ledger
(`p39c-observed-sandboxes.json`) under `.fleet-evidence/receipts/`
(git-ignored, never committed). The canonical default-name receipt is ALWAYS
written there; when a runner sets `FLEET_LIVE_EVIDENCE_PATH`, that location
receives an additional env-stem copy, never a replacement. Every lane writes
the ledger through the single shared helper
`tests/live/backend/_p39c_evidence.py`, whose read-modify-write merge is
atomic and refuses to shrink recorded lane coverage (`LedgerCoverageError`).

Multiple workers certifying on one branch can split receipt SHAs, while the
aggregate zero-leak gate requires every lane receipt at HEAD and the full
seven-lane ledger. When HEAD moves, ARCHIVE the whole stale `p39c-*` set —
move, never delete — before re-certifying:

```bash
archive=.fleet-evidence/receipts-archive/p39c-pre-$(git rev-parse --short HEAD)
mkdir "$archive" && mv .fleet-evidence/receipts/p39c-*.json "$archive"/
```

Then re-run the lanes (or re-stamp receipts whose evidence is unchanged) at
the stable HEAD and run the zero-leak aggregate LAST.

Rebuild rule: the aggregate lane's pre-flight restores any lane key missing
from the canonical ledger by merging id lists from the NEWEST COMPLETE
`.fleet-evidence/receipts-archive/p39c-*/p39c-observed-sandboxes.json`
(complete = its `lanes` mapping covers all seven lane names; newest by
directory mtime). This restores ledger identity only: archived receipts stay
in the archive, archived files are never modified, and restored lanes whose
receipts still carry an old SHA FAIL the aggregate's same-SHA gates until
they are re-run or re-stamped at HEAD.

## Security, packaging, and release

```bash
make check-security
make check-release
make build-release
git diff --check
```

`make build-release` builds the Python distributions and validates wheel content
and metadata. These lanes do not replace the primary repository gate.
