<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: build_system
  name: Build & Release Pipeline (uv + Makefile + CircleCI)
  category: build_system
  scope:
      - '**'
  source_files:
      - Makefile
      - pyproject.toml
      - .circleci/config.yml
      - alembic.ini
      - .github/dependabot.yml
      - scripts/validate_release.py
      - scripts/openapi_tools.py
-->


The project uses a layered build system centered on `uv` for dependency resolution and packaging, a comprehensive `Makefile` as the developer-facing orchestration layer, and CircleCI for continuous integration. There is no Dockerfile; deployment targets FastAPI Cloud via the `fastapi` CLI entrypoint declared in `pyproject.toml`.

**Dependency management and packaging**
- `pyproject.toml` declares the package (`fleet-rlm`, version `0.7.0`), Python `>=3.11,<3.14`, setuptools build backend, and all runtime/optional dependencies. `uv.lock` pins the full transitive graph.
- `tool.uv.override-dependencies` enforces security-patched floors for vulnerable transitive deps (aiohttp, idna, mako, gitpython, starlette, litellm, urllib3) — this is the repo's policy mechanism for overriding upstream pinning.
- Package metadata, entry points (`fleet`, `fleet-rlm`), and package data (skills, snapshot requirements, `py.typed`) are declared under `[tool.setuptools]`.
- `alembic.ini` configures migrations against a local Postgres default URL.

**Developer workflow (Makefile)**
- `make install[/dev/all]` → `uv sync [--extra dev|--all-extras]`
- Formatting/linting/typecheck: `ruff format/check`, `ruff check`, `ty check src/fleet_rlm`
- Tests: pytest with markers (`unit`, `integration`, `contracts`, `db`, `e2e`, `benchmark`, `deno`, `live_llm`, `live_daytona`). Default run excludes live/benchmark suites via `PYTEST_FAST_MARKERS`. Parallelism via `pytest-xdist` (`-n auto --maxprocesses=$(PYTEST_XDIST_MAX_WORKERS)`, default 2). Coverage is scoped package-wide to `src/fleet_rlm` with branch coverage and a 75% fail-under (`[tool.coverage]`).
- Quality gate: `make check` chains lint, format-check, typecheck, `test-daytona-cov`, OpenAPI/TUI checks, codebase-tree boundary enforcement, and docs checks.
- Build/release: `make build` → `uv build`; `make build-release` validates wheel with `scripts/validate_release.py wheel` and `twine check`; `make release` runs clean + check + security + build-release.
- Daytona snapshot lifecycle: `make daytona-snapshot-create|check` delegates to `scripts/daytona_snapshot.py`.
- TUI validation: `make tui-check` runs pnpm format/lint/typecheck/test inside `tools/fleet-tui/`.
- Security scanning: `pip-audit` (with explicit CVE ignores for known unpatched transitive deps) + `bandit -lll` over `src/fleet_rlm`.
- Dependency auditing: `deptry .` with PEP621 dev-group mapping.

**CI pipeline (CircleCI `.circleci/config.yml`)**
- Executor: `cimg/python:3.13.13` with cached `uv` binary and lock-file–keyed dependency cache.
- Jobs:
  - `quality`: release metadata + docs checks, security scan, deptry.
  - `lint-typecheck`: ruff + ty.
  - `test-unit` / `test-e2e`: parallelized subsets via `circleci run testsuite`.
  - `daytona-coverage`: runs `make test-daytona-cov` and uploads XML coverage.
  - `test-deno`: installs pinned Deno 2.9.2 and runs deno contract tests.
- Workflows run all jobs in parallel per push.

**Frontend build (Node.js TUI)**
- `tools/fleet-tui/` is an independent Node.js project with its own `package.json`, `pnpm-lock.yaml`, `biome.json`, `tsconfig.json`. The Makefile invokes `pnpm` commands for formatting, linting, typechecking, and testing during `tui-check`.

**OpenAPI contract generation**
- `scripts/openapi_tools.py` generates and validates the OpenAPI spec and regenerates TypeScript HTTP client types used by the TUI. `make api-sync` regenerates; `make api-check` verifies against the committed spec.

**Versioning and release**
- Single source of truth: `pyproject.toml` `version = "0.7.0"`.
- Release hygiene enforced by `scripts/validate_release.py` (metadata, wheel validity, AGENTS.md freshness).
- No GitHub Actions workflows are present; CI lives exclusively in CircleCI.