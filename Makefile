PYTHON_SOURCES = src tests scripts migrations
# Release/install matrix tests are intentionally opt-in: they create multiple
# virtual environments and are covered by the dedicated package gate.
PYTEST_FAST_MARKERS = not live_llm and not live_daytona and not benchmark and not db and not packaging
PYTEST_PACKAGING_MARKERS = packaging and not live_llm and not live_daytona and not benchmark and not db
PYTEST := uv run --no-sync pytest
PYTEST_ISOLATED := env \
	FLEET_DAYTONA_API_KEY= \
	FLEET_OPENAI_API_KEY= \
	FLEET_LLM_BASE_URL= \
	FLEET_DATABASE_URL= \
	$(PYTEST)
PYTEST_XDIST_MAX_WORKERS ?= 2
# Keep module-scoped fixtures together; this avoids rebuilding expensive test
# state when xdist splits individual tests from the same module.
PYTEST_PARALLEL := -n auto --maxprocesses=$(PYTEST_XDIST_MAX_WORKERS) --dist=loadfile

.PHONY: \
	help \
	install install-dev install-all \
	dev format format-check lint typecheck \
	test test-fast test-unit test-contract test-packaging test-daytona-cov \
	check quality-gate check-release check-docs check-security check-deps check-codebase-tree check-dependency-boundaries api-check api-sync tui-check \
	build build-release release release-check \
	certification-gate certification-verify p53-live-certification \
	clean cli precommit-install precommit-run precommit \
	sync sync-dev sync-all metadata-check docs-check security-check dependency-check release-artifacts cli-help \
	cloud-preflight \
	daytona-snapshot-create daytona-snapshot-check profile-matrix \
	benchmark-oolong benchmark-native-long-context

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
	@echo "  make test-unit        - Run unit tests (non-live/non-benchmark; packaging separate)"
	@echo "  make test-packaging   - Run serial artifact/install/release tests"
	@echo "  make test-contract    - Run backend contracts and CLI smoke tests"
	@echo "  make test-daytona-cov - Run canonical non-live tests with Daytona branch coverage"
	@echo "  make p53-live-certification - Run serial credentialed P53 Session certification"
	@echo "  make benchmark-oolong - Run pinned Prime Oolong smoke (runtime.live_enabled=true; configure credentials)"
	@echo "  make benchmark-native-long-context - Measure native whole-value URL context at 1/5/10 MiB"
	@echo ""
	@echo "Quality:"
	@echo "  make check            - Run the primary repo quality gate"
	@echo "  make check-release    - Run release metadata/hygiene and AGENTS.md validation"
	@echo "  make check-docs       - Run docs quality and harness engineering checks"
	@echo "  make check-security   - Run pip-audit + bandit"
	@echo "  make check-deps       - Check Python dependencies with deptry"
	@echo "  make check-codebase-tree - Enforce import boundaries defined in codebase map"
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
	@echo "  make release-check    - Alias for release"
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
	$(PYTEST_ISOLATED) -q $(PYTEST_PARALLEL) tests/unit/backend tests/unit/scripts tests/contracts/backend tests/freeze tests/unit/test_litellm_invariant.py tests/e2e -m "$(PYTEST_FAST_MARKERS)"

test-fast: test

test-packaging:
	$(PYTEST_ISOLATED) -q tests/unit/backend/packaging -m "$(PYTEST_PACKAGING_MARKERS)" -n 0

test-unit:
	$(PYTEST_ISOLATED) -q $(PYTEST_PARALLEL) tests/unit/backend tests/unit/scripts tests/freeze tests/unit/test_litellm_invariant.py -m "$(PYTEST_FAST_MARKERS)"

test-contract:
	$(PYTEST_ISOLATED) -q tests/contracts/backend tests/e2e -m "$(PYTEST_FAST_MARKERS)" -n 0

test-daytona-cov:
	mkdir -p .scratch/coverage
	$(PYTEST_ISOLATED) -q $(PYTEST_PARALLEL) tests/unit/backend tests/unit/scripts tests/contracts/backend tests/freeze tests/unit/test_litellm_invariant.py tests/e2e -m "$(PYTEST_FAST_MARKERS)" --cov --cov-config=pyproject.toml --cov-report=term-missing --cov-report=xml:.scratch/coverage/daytona.xml

test-db:
	$(PYTEST) -q -m "db" -n 0

OOLONG_LIMIT ?= 12
OOLONG_OUTPUT ?= .scratch/benchmark-reports/prime-oolong-$(shell date +%Y-%m-%d).json
OOLONG_API_URL ?= http://127.0.0.1:8000
OOLONG_PROFILE ?= daytona-bench

benchmark-oolong:
	uv run python scripts/benchmarks/run_prime_oolong.py --limit $(OOLONG_LIMIT) --api-url $(OOLONG_API_URL) --profile $(OOLONG_PROFILE) --output $(OOLONG_OUTPUT)

NATIVE_LONG_CONTEXT_OUTPUT ?= .scratch/benchmark-reports/native-long-context-$(shell date +%Y-%m-%d).json

benchmark-native-long-context:
	uv run python scripts/benchmarks/run_native_long_context.py --output $(NATIVE_LONG_CONTEXT_OUTPUT)

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
	cd tools/fleet-tui && pnpm run format:check
	cd tools/fleet-tui && pnpm run lint
	cd tools/fleet-tui && pnpm run typecheck
	cd tools/fleet-tui && pnpm run test

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

p53-live-certification:
	FLEET_LIVE=1 uv run python scripts/live_p53_certification.py

certification-gate:
	uv run python scripts/certification_gate.py run

certification-verify:
	uv run python scripts/certification_gate.py verify

release: clean check check-security build-release

release-check: release

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

cli-help: cli

sync:
	$(MAKE) install

sync-dev:
	$(MAKE) install-dev

sync-all:
	$(MAKE) install-all

metadata-check:
	$(MAKE) check-release

docs-check:
	$(MAKE) check-docs

security-check:
	$(MAKE) check-security

dependency-check:
	$(MAKE) check-deps

release-artifacts:
	$(MAKE) build-release

cloud-preflight:
	@echo "Checking fastapi CLI is available in the locked env..."
	uv run fastapi --help >/dev/null
	@echo "Importing configured FastAPI entrypoint..."
	uv run python -c "from fleet_rlm.main import app; print(f'{app.title} {app.version}')"
	@echo "Enumerating routes from create_app()..."
	uv run python -c "from fleet_rlm.app import create_app; a = create_app(); print('\n'.join(sorted({getattr(r, 'path', '<?>') for r in a.routes})))"
	@echo "cloud-preflight OK"
