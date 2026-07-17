PYTHON_SOURCES = src/fleet_rlm tests/unit/backend tests/unit/scripts tests/contracts/backend tests/e2e scripts/openapi_tools.py scripts/db_init.py scripts/live_daytona_verify.py migrations
PYTEST_FAST_MARKERS = not deno and not live_llm and not live_daytona and not benchmark and not db
PYTEST := uv run --no-sync pytest
PYTEST_ISOLATED := env \
	FLEET_RUN_ENVIRONMENT=daytona \
	FLEET_DAYTONA_API_KEY= \
	FLEET_LLM_API_KEY= \
	FLEET_LLM_BASE_URL= \
	FLEET_DATABASE_URL= \
	$(PYTEST)
PYTEST_XDIST_MAX_WORKERS ?= 2
PYTEST_PARALLEL := -n auto --maxprocesses=$(PYTEST_XDIST_MAX_WORKERS)

.PHONY: \
	help \
	install install-dev install-all \
	dev format format-check lint typecheck \
	test test-fast test-unit test-contract test-deno \
	check quality-gate check-release check-docs check-security check-deps check-codebase-tree api-check api-sync tui-check \
	build build-release release release-check \
	clean cli precommit-install precommit-run precommit \
	sync sync-dev sync-all metadata-check docs-check security-check dependency-check release-artifacts cli-help \
	cloud-preflight

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
	@echo "  make test             - Run default non-live/non-benchmark tests"
	@echo "  make test-unit        - Run unit tests (non-live/non-benchmark)"
	@echo "  make test-contract    - Run backend contracts and CLI smoke tests"
	@echo "  make test-deno        - Run deterministic contracts against the Deno runtime"
	@echo ""
	@echo "Quality:"
	@echo "  make check            - Run the primary repo quality gate"
	@echo "  make check-release    - Run release metadata/hygiene and AGENTS.md validation"
	@echo "  make check-docs       - Run docs quality and harness engineering checks"
	@echo "  make check-security   - Run pip-audit + bandit"
	@echo "  make check-deps       - Check Python dependencies with deptry"
	@echo "  make check-codebase-tree - Enforce import boundaries defined in codebase map"
	@echo "  make api-check        - Verify the backend-only OpenAPI artifact"
	@echo "  make api-sync         - Regenerate the backend-only OpenAPI artifact"
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
	uv run ty check src/fleet_rlm

test:
	$(PYTEST_ISOLATED) -q $(PYTEST_PARALLEL) tests/unit/backend tests/unit/scripts tests/contracts/backend tests/unit/test_litellm_invariant.py tests/e2e -m "$(PYTEST_FAST_MARKERS)"

test-fast: test

test-unit:
	$(PYTEST_ISOLATED) -q $(PYTEST_PARALLEL) tests/unit/backend tests/unit/scripts tests/unit/test_litellm_invariant.py -m "$(PYTEST_FAST_MARKERS)"

test-contract:
	$(PYTEST_ISOLATED) -q tests/contracts/backend tests/e2e -m "$(PYTEST_FAST_MARKERS)" -n 0

test-deno:
	$(PYTEST) -q tests/unit/backend/test_deno_run_environment.py tests/contracts/backend/test_deno_turn_flow.py -m "deno" -n 0 --timeout=120

test-db:
	$(PYTEST) -q -m "db" -n 0

tui-check:
	$(MAKE) api-check
	pnpm --dir tools/fleet-tui run format:check
	pnpm --dir tools/fleet-tui run lint
	pnpm --dir tools/fleet-tui run typecheck
	pnpm --dir tools/fleet-tui run test

check: lint format-check typecheck test api-check tui-check check-codebase-tree check-docs

quality-gate: check

check-release:
	uv run python scripts/validate_release.py hygiene
	uv run python scripts/validate_release.py metadata
	uv run python scripts/check_agents_md_freshness.py

check-docs:
	uv run python scripts/check_docs_quality.py
	uv run python scripts/check_harness_engineering.py

check-security:
	# TODO: Remove this ignore once Pygments ships a patched release for
	# GHSA-5239-wwwm-4pmq / CVE-2026-4539.
	# TODO: Remove the pip ignore once the uvx pip-audit runtime no longer
	# pulls pip 26.0.1 / CVE-2026-3219.
	# TODO: Remove the DiskCache ignore once upstream ships a patched release
	# or DSPy removes the transitive dependency.
	uvx pip-audit --ignore-vuln GHSA-5239-wwwm-4pmq --ignore-vuln CVE-2026-3219 --ignore-vuln CVE-2025-69872
	uvx bandit -q -r src/fleet_rlm -x tests -lll

check-deps:
	uvx deptry .

check-codebase-tree:
	uv run python scripts/check_codebase_tree.py

api-check:
	uv run python scripts/openapi_tools.py check

api-sync:
	uv run python scripts/openapi_tools.py generate

build:
	rm -rf dist build
	uv build

build-release: build
	uv run python scripts/validate_release.py wheel
	uvx twine check dist/*

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
