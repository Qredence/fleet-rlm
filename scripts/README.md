# Maintained scripts and preserved data

Run commands from the repository root with `uv run python`, unless the row
names a Make target. This inventory separates executable operator/CI tools
from preserved benchmark case data. The retired benchmark sources and their
non-executable contracts are recorded in
[`docs/internal/history/benchmarks/retained-material.md`](../docs/internal/history/benchmarks/retained-material.md).
Generated outputs must come from their source commands. Live commands remain
operator-gated and receipts are evidence for only their stated contract.

## Executable scripts

| Path | Use case and consumer | Validation or invocation |
| --- | --- | --- |
| `scripts/check_repo_hygiene.py` | CI and contributors validate agent guidance, tracked documentation, bootstrap files, active script references, and safe command help. | `make check-docs`; `uv run python scripts/check_repo_hygiene.py --help` |
| `scripts/check_architecture.py` | CI checks backend ownership/test layout and dependency direction. | `make check-codebase-tree`; `make check-dependency-boundaries` |
| `scripts/contracts.py` | CI and maintainers generate/check OpenAPI, TUI HTTP types, chunk validation, stream fixtures, and the TOML configuration reference. | `make api-check`; `make stream-check`; `make check-docs`; sync with `make api-sync`, `make stream-sync`, and `make config-reference` |
| `scripts/database.py` | Operators initialize Alembic, explicitly import SQLite data, run Lakebase preflight, or certify an isolated PostgreSQL test database. | `uv run python scripts/database.py --help`; focused tests under `tests/scripts/test_{migrate_sqlite_to_postgres,lakebase_preflight,certify_postgres}.py` |
| `scripts/validate_release.py` | CI and release tooling validate metadata, package contents, artifact identities, and reproducible wheel/sdist normalization. | `make check-release`; `make build-release`; `uv run pytest tests/scripts/test_release_tooling.py -q` |
| `scripts/release_smoke.py` | Packaging tests install a built wheel into an isolated environment and smoke the supported application surface. | `make test-packaging` |
| `scripts/circleci_trigger_release.py` | Release operators dispatch and correlate the GitHub release workflow from CircleCI. | `uv run pytest tests/scripts/test_release_tooling.py -q` |
| `scripts/deployment_observability.py` | Release operators inspect bounded deployment observability inputs without changing deployment state. | `uv run python scripts/deployment_observability.py --help` |
| `scripts/daytona_snapshot.py` | Daytona operators plan, create, check, or verify immutable Session and SemanticChild snapshots. | `make daytona-snapshot-check`; `make daytona-child-snapshot-check` |
| `scripts/live_daytona_verify.py` | Authorized operators verify native FastAPI semantics, attachment/artifact durability, or recursive-batch behavior on a committed Daytona candidate. | `uv run python scripts/live_daytona_verify.py --help`; offline safety tests in `tests/scripts/test_live_daytona_verify.py` |

The native Daytona lane requires `FLEET_LIVE=1`, recursion disabled in the
selected TOML configuration, explicit bounded Root/Sub model IDs, configured
provider credentials, a clean tracked candidate, and a new allowed receipt
path. The recursive-batch lane has its own enabled-recursion checks and receipt
contract. Neither lane by itself certifies provider containment, promotion,
release readiness, or deployment. Database certification requires an explicit
exclusive test target and never runs migrations implicitly.

## Preserved non-executable benchmark data

| Path | Preserved content | Contract record |
| --- | --- | --- |
| `scripts/benchmarks/phase6_evaluation_cases.json` | Frozen task-family inputs, rubrics, and content hashes; data only, with no runner. | [`retained-material.md`](../docs/internal/history/benchmarks/retained-material.md#phase-6-case-set) |
| `scripts/benchmarks/oolong/fixture_validation_row.json` | Fixed HF-shaped Oolong row for offline fixture/reference use. | [`retained-material.md`](../docs/internal/history/benchmarks/retained-material.md#oolong-scoring-contract) |

The curated routing scenarios remain owned by `src/fleet_rlm/optimization/routing.py`.
They are source-level policy cases, not an executable benchmark runner.
