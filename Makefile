PYTHON_SOURCES = src tests scripts migrations
# Release/install matrix tests are intentionally opt-in: they create multiple
# virtual environments and are covered by the dedicated package gate.
PYTEST_FAST_MARKERS = not live_llm and not live_daytona and not benchmark and not db and not packaging
PYTEST_PACKAGING_MARKERS = packaging and not live_llm and not live_daytona and not benchmark and not db
PYTEST_FAST_PATHS = tests/unit/backend tests/unit/scripts tests/contracts/backend tests/freeze tests/unit/test_litellm_invariant.py tests/e2e
PYTEST_UNIT_PATHS = tests/unit/backend tests/unit/scripts tests/freeze tests/unit/test_litellm_invariant.py
PYTEST := uv run --no-sync pytest
PYTEST_ISOLATED := env \
	FLEET_DAYTONA_API_KEY= \
	FLEET_OPENAI_API_KEY= \
	FLEET_LLM_BASE_URL= \
	FLEET_DATABASE_URL= \
	$(PYTEST)
# Two workers keep local gate runs fast on typical dev machines while xdist
# `loadfile` scheduling keeps module-scoped fixtures together; override only
# with verified local runner capacity.
PYTEST_XDIST_MAX_WORKERS ?= 2
PYTEST_PARALLEL := -n auto --maxprocesses=$(PYTEST_XDIST_MAX_WORKERS) --dist=loadfile
PYTEST_FAST_ARGS = -q $(PYTEST_PARALLEL) $(PYTEST_FAST_PATHS) -m "$(PYTEST_FAST_MARKERS)"
PYTEST_UNIT_ARGS = -q $(PYTEST_PARALLEL) $(PYTEST_UNIT_PATHS) -m "$(PYTEST_FAST_MARKERS)"

TUI_DIR := tools/fleet-tui
TUI_PNPM := cd $(TUI_DIR) && pnpm

.PHONY: \
	help \
	install install-dev install-all \
	dev format format-check lint typecheck \
	test test-fast test-unit test-contract test-packaging test-db test-daytona-cov \
	check quality-gate check-release check-docs check-security check-deps check-codebase-tree check-dependency-boundaries \
	api-check api-sync tui-check stream-check stream-sync \
	build build-release release \
	clean cli precommit-install precommit-run precommit \
	cloud-preflight \
	daytona-snapshot-create daytona-snapshot-check profile-matrix \
	benchmark-daytona-lifecycle benchmark-native-long-context

help:
	@echo "Setup:"
	@echo "  make install          - Install runtime dependencies with uv"
	@echo "  make install-dev      - Install dev dependencies with uv"
	@echo "  make install-all      - Install all optional extras with uv"
	@echo ""
	@echo "Development:"
	@echo "  make dev              - Start the local app (fleet web)"
	@echo "  make format           - Run ruff format (writes changes)"
	@echo "  make format-check     - Run ruff format --check"
	@echo "  make lint             - Run ruff check"
	@echo "  make typecheck        - Run ty check"
	@echo ""
	@echo "Testing:"
	@echo "  make test             - Run default fast non-live tests (packaging is separate)"
	@echo "  make test-fast        - Alias for the default fast non-live tests"
	@echo "  make test-unit        - Run unit tests (non-live/non-benchmark; packaging separate)"
	@echo "  make test-contract    - Run backend contracts and CLI smoke tests"
	@echo "  make test-packaging   - Run serial artifact/install/release tests"
	@echo "  make test-db          - Run explicit configured-database tests (db marker)"
	@echo "  make test-daytona-cov - Run canonical non-live tests with Daytona branch coverage"
	@echo "  make benchmark-daytona-lifecycle - Measure full Daytona create-through-first-execution lifecycle"
	@echo "  make benchmark-native-long-context - Measure native whole-value URL context at 1/5/10 MiB"
	@echo "  (Credentialed live Daytona lanes run via FLEET_LIVE=1; see docs/how-to-guides/testing-strategy.md)"
	@echo ""
	@echo "Quality:"
	@echo "  make check            - Run the primary repo quality gate"
	@echo "  make quality-gate     - Alias for the primary repo quality gate"
	@echo "  make check-release    - Run release metadata/hygiene and AGENTS.md validation"
	@echo "  make check-docs       - Run docs quality and harness engineering checks"
	@echo "  make check-security   - Run pip-audit + bandit"
	@echo "  make check-deps       - Check Python dependencies with deptry"
	@echo "  make check-codebase-tree - Enforce import boundaries documented in ARCHITECTURE.md"
	@echo "  make check-dependency-boundaries - Enforce provider/domain dependency directions"
	@echo "  make api-check        - Verify OpenAPI and generated TUI HTTP types"
	@echo "  make api-sync         - Regenerate OpenAPI and generated TUI HTTP types"
	@echo "  make stream-check     - Verify the TUI turn-stream fixture is current"
	@echo "  make stream-sync      - Regenerate the TUI turn-stream fixture"
	@echo ""
	@echo "Build & release:"
	@echo "  make build            - Build Python distributions"
	@echo "  make build-release    - Build and verify the backend-only distribution"
	@echo "  make release          - Run clean + check + security + release artifacts"
	@echo ""
	@echo "Cloud:"
	@echo "  make cloud-preflight  - Validate the app boots for FastAPI Cloud deploy"
	@echo ""
	@echo "Utility:"
	@echo "  make daytona-snapshot-create - Create or validate the immutable Daytona Snapshot"
	@echo "  make daytona-snapshot-check  - Check the immutable Daytona Snapshot contract"
	@echo "  make profile-matrix          - Regenerate the TOML-derived provider/profile matrix"
	@echo "  make clean            - Remove caches and local generated artifacts"
	@echo "  make precommit-install - Install pre-commit and pre-push git hooks"
	@echo "  make precommit-run    - Run pre-commit on all files"
	@echo "  make cli              - Show fleet-rlm CLI help"

install:
	uv sync

install-dev:
	uv sync --extra dev

install-all:
	uv sync --all-extras

dev:
	uv run fleet web

format:
	uv run ruff format $(PYTHON_SOURCES)

format-check:
	uv run ruff format --check $(PYTHON_SOURCES)

lint:
	uv run ruff check $(PYTHON_SOURCES)

typecheck:
	uv run ty check src

test:
	$(PYTEST_ISOLATED) $(PYTEST_FAST_ARGS)

test-fast: test

test-unit:
	$(PYTEST_ISOLATED) $(PYTEST_UNIT_ARGS)

test-contract:
	$(PYTEST_ISOLATED) -q tests/contracts/backend tests/e2e -m "$(PYTEST_FAST_MARKERS)" -n 0

test-packaging:
	$(PYTEST_ISOLATED) -q tests/unit/backend/packaging -m "$(PYTEST_PACKAGING_MARKERS)" -n 0

test-db:
	$(PYTEST) -q -m "db" -n 0

test-daytona-cov:
	mkdir -p .scratch/coverage
	$(PYTEST_ISOLATED) $(PYTEST_FAST_ARGS) --cov --cov-config=pyproject.toml --cov-report=term-missing --cov-report=xml:.scratch/coverage/daytona.xml

NATIVE_LONG_CONTEXT_OUTPUT ?= .scratch/benchmark-reports/native-long-context-$(shell date +%Y-%m-%d).json

benchmark-native-long-context:
	uv run python scripts/benchmarks/run_native_long_context.py --output $(NATIVE_LONG_CONTEXT_OUTPUT)

benchmark-daytona-lifecycle:
	FLEET_LIVE=1 uv run python scripts/benchmark_daytona_lifecycle.py --output .scratch/daytona-lifecycle-benchmark.json

DAYTONA_SNAPSHOT_NAME ?= fleet-rlm-python313-v5

daytona-snapshot-create:
	uv run python scripts/daytona_snapshot.py create --name $(DAYTONA_SNAPSHOT_NAME)

daytona-snapshot-check:
	uv run python scripts/daytona_snapshot.py check --name $(DAYTONA_SNAPSHOT_NAME)

profile-matrix:
	uv run python scripts/generate_profile_matrix.py generate

tui-check:
	$(MAKE) api-check
	$(MAKE) stream-check
	# Run pnpm from inside the workspace so corepack resolves the pinned
	# packageManager version (pnpm --dir resolves from the invocation CWD and
	# misses it when make runs from the repo root).
	$(TUI_PNPM) run format:check
	$(TUI_PNPM) run lint
	$(TUI_PNPM) run typecheck
	$(TUI_PNPM) run test

check: lint format-check typecheck test-daytona-cov api-check tui-check check-codebase-tree check-dependency-boundaries check-docs

quality-gate: check

check-release:
	uv run python scripts/validate_release.py hygiene
	uv run python scripts/validate_release.py metadata
	uv run python scripts/check_agents_md_freshness.py

check-docs:
	uv run python scripts/generate_profile_matrix.py check
	uv run python scripts/check_docs_quality.py
	uv run python scripts/check_harness_engineering.py

check-security:
	uvx pip-audit
	uvx bandit -q -r src/fleet_rlm -x tests -lll

check-deps:
	uvx deptry .

check-codebase-tree:
	uv run python scripts/check_codebase_tree.py

check-dependency-boundaries:
	uv run python scripts/check_dependency_boundaries.py

api-check:
	uv run python scripts/openapi_tools.py check
	uv run python scripts/generate_tui_chunk_validation.py check

api-sync:
	uv run python scripts/openapi_tools.py generate
	uv run python scripts/generate_tui_chunk_validation.py generate

stream-check:
	uv run python scripts/generate_stream_fixture.py check

stream-sync:
	uv run python scripts/generate_stream_fixture.py generate

build:
	rm -rf dist build
	uv build

build-release: build
	uv run python scripts/validate_release.py wheel
	uvx twine check --strict dist/*
	uv run python scripts/validate_release.py artifacts

release: clean check check-security build-release

clean:
	@echo "Cleaning caches and local generated artifacts..."
	find . -type d \( -name ".ruff_cache" -o -name "__pycache__" -o -name ".pytest_cache" -o -name ".mypy_cache" \) -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist .coverage .venv-release-smoke
	rm -f server.log fleet_rlm.db
	@echo "Cleanup complete"

precommit-install:
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push

precommit-run:
	uv run pre-commit run --all-files

precommit: precommit-install

cli:
	uv run fleet-rlm --help

cloud-preflight:
	@echo "Checking fastapi CLI is available in the locked env..."
	uv run fastapi --help >/dev/null
	@echo "Importing configured FastAPI entrypoint..."
	uv run python -c "from fleet_rlm.main import app; print(f'{app.title} {app.version}')"
	@echo "Enumerating routes from create_app()..."
	uv run python -c "from fleet_rlm.app import create_app; a = create_app(); print('\n'.join(sorted({getattr(r, 'path', '<?>') for r in a.routes})))"
	@echo "cloud-preflight OK"
