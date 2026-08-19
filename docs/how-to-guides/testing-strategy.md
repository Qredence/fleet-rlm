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
| Backend contracts | `tests/contracts/backend/` | API, persistence, packaging, composition, boundary contracts |
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
composition where required and run with at most two xdist workers by default.

Package-wide coverage is enforced separately over the same canonical non-live
corpus:

```bash
make test-daytona-cov
```

This target measures `src/fleet_rlm`, fails below 75%, prints missing lines,
and writes `.scratch/coverage/daytona.xml`. CircleCI runs it as one aggregate
job because the existing unit and E2E jobs execute independent test shards that
cannot enforce a package-wide threshold individually. Coverage is not a
substitute for the opt-in live Daytona durability checks below.

`make check` includes:

- Ruff lint and format checks;
- `ty` for `src`;
- backend/script/LiteLLM/contract/end-to-end tests excluding live,
  benchmark, and database markers;
- `make api-check` for OpenAPI and generated TUI HTTP types;
- pi-tui format, lint, type, and Vitest checks;
- codebase-tree and documentation/harness checks.

CircleCI enforces the same non-live surface: the `ci` workflow runs `quality`
(on the Node-bearing `cimg/python:*-node` executor so `api-check` can run
openapi-typescript there: release, docs, security, dependency, `api-check`,
and `stream-check`),
`lint-typecheck`, `test-unit`, `test-e2e`, `daytona-coverage`, lightweight
`python-compat-311` / `python-compat-312` jobs (lock/install, import check, and
`tests/unit/backend` + `tests/contracts/backend` only), and the `tui`
job (pnpm format, lint, typecheck, and Vitest against the maintained client).
Python 3.13 remains the full gate image; 3.11/3.12 certify declared support
without duplicating Daytona coverage or E2E.

`git diff --check` is required separately. Useful focused commands are:

```bash
uv run pytest tests/unit/backend tests/unit/scripts tests/contracts/backend tests/e2e -q
uv run ruff check src tests scripts migrations
uv run ty check src
make api-check
pnpm --dir tools/fleet-tui run test
git diff --check
```

For separation-of-concerns changes, keep boundary checks close to the
production seam: composition inventory tests live in
`tests/unit/backend/test_live_composition.py`, Turn execution tests
in `tests/unit/backend/chat/test_run_execution.py`, binding repository tests
in `tests/unit/backend/test_sandbox_binding_repository.py`, and pure broker
source tests in `tests/unit/backend/daytona/test_broker_source.py`. Include
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

## Security, packaging, and release

```bash
make check-security
make check-release
make build-release
git diff --check
```

`make build-release` builds the Python distributions and validates wheel content
and metadata. These lanes do not replace the primary repository gate.
