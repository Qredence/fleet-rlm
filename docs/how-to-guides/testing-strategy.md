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
| Deno | named unit/contract tests marked `deno` | real deterministic DSPy Deno/Pyodide contract |
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

Daytona branch coverage is enforced separately over the same canonical
non-live corpus:

```bash
make test-daytona-cov
```

This target measures `src/fleet_rlm/daytona`, fails below 70%, prints missing
lines, and writes `.scratch/coverage/daytona.xml`. CircleCI runs it as one
aggregate job because the existing unit and E2E jobs execute independent test
shards that cannot enforce a package-wide threshold individually. Coverage is
not a substitute for the opt-in live Daytona durability checks below.

`make check` includes:

- Ruff lint and format checks;
- `ty` for `src/fleet_rlm`;
- backend/script/LiteLLM/contract/end-to-end tests excluding `deno`, live,
  benchmark, and database markers;
- `make api-check` for OpenAPI and generated TUI HTTP types;
- pi-tui format, lint, type, and Vitest checks;
- codebase-tree and documentation/harness checks.

`git diff --check` is required separately. Useful focused commands are:

```bash
uv run pytest tests/unit/backend tests/unit/scripts tests/contracts/backend tests/e2e -q
uv run ruff check src/fleet_rlm tests scripts
uv run ty check src/fleet_rlm
make api-check
pnpm --dir tools/fleet-tui run test
git diff --check
```

The TUI suite observes the application through an injected deterministic
terminal. It covers strict stream state, live/durable ordering, atomic hydration,
commands and Skill selection, cancellation, complete static rendering,
10,000-row native scrollback, absence of mouse-mode sequences, and cleanup.

## Deno gate

```bash
make test-deno
```

This requires Deno on `PATH` and runs without provider network calls. It
validates DSPy's actual default Deno/Pyodide interpreter, progressive Skill
loading, bounded inputs, execution, `SUBMIT`, terminal projection, and failure,
cancellation, and timeout handling.

CircleCI installs the pinned Deno 2.9.2 runtime and runs this as a required
workflow job. The normal fast split excludes `deno` markers.

## Database gate

Alembic owns live schema creation. Against an explicitly configured empty
database:

```bash
uv run alembic upgrade head
uv run alembic check
```

Tests and Deno local SQLite helpers may create ephemeral schemas explicitly.

## Credentialed Daytona gates

Live checks require canonical credentials and explicit opt-in:

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
FLEET_LIVE=1 uv run python scripts/live_daytona_verify.py \
  --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json \
  --root-model <approved-root-model> \
  --sub-model <approved-sub-model>
```

It must record a passing receipt at the exact candidate SHA, verify provider
cleanup and secret isolation, and be paired with same-SHA CI, local release, and
human attestations before promotion. Historical receipts do not prove a later
tip.

## Security, packaging, and release

```bash
make check-security
make check-release
make build-release
git diff --check
```

`make build-release` builds the Python distributions and validates wheel content
and metadata. These lanes do not replace the primary repository gate.
