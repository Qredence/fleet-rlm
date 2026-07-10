PYTHON_SOURCES = src tests
PYTEST_FAST_MARKERS = not live_llm and not live_daytona and not benchmark and not db
PYTEST := uv run --no-sync pytest

.PHONY: \
	help \
	install install-dev install-all \
	dev format format-check lint typecheck \
	test test-fast test-unit test-integration test-e2e \
	check quality-gate check-release check-docs check-duplicates check-security check-deps check-frontend check-codebase-tree api-check api-sync \
	build build-ui build-release release release-check \
	clean cli mlflow precommit-install precommit-run precommit \
	sync sync-dev sync-all metadata-check docs-check security-check dependency-check frontend-check sync-ui release-artifacts cli-help mlflow-server mlflow-upgrade \
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
	@echo "  make test-integration - Run integration + e2e tests (non-live/non-benchmark)"
	@echo "  make test-e2e         - Run frontend Playwright tests when frontend exists"
	@echo ""
	@echo "Quality:"
	@echo "  make check            - Run the primary repo quality gate"
	@echo "  make check-release    - Run release metadata/hygiene and AGENTS.md validation"
	@echo "  make check-docs       - Run docs quality and harness engineering checks"
	@echo "  make check-duplicates - Detect duplicate handwritten source blocks with jscpd (requires frontend pnpm install)"
	@echo "  make check-security   - Run pip-audit + bandit"
	@echo "  make check-deps       - Check for unused dependencies (deptry, knip)"
	@echo "  make check-frontend   - Run frontend checks when src/frontend exists"
	@echo "  make check-codebase-tree - Enforce import boundaries defined in codebase map"
	@echo "  make api-check        - Validate OpenAPI artifacts and frontend API sync"
	@echo "  make api-sync         - Regenerate OpenAPI and frontend API artifacts"
	@echo ""
	@echo "Build & release:"
	@echo "  make build            - Build Python distributions"
	@echo "  make build-ui         - Build the frontend and sync packaged UI assets"
	@echo "  make build-release    - Build + verify publishable distributions with synced UI assets"
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
	@echo "  make mlflow           - Start a local MLflow OSS tracking server on port 5001"

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
	$(PYTEST) -q -n auto tests/unit tests/contracts -m "$(PYTEST_FAST_MARKERS)"
	$(PYTEST) -q tests/integration -m "$(PYTEST_FAST_MARKERS)" -n 0

test-fast: test

test-unit:
	$(PYTEST) -q -n auto tests/unit -m "$(PYTEST_FAST_MARKERS)"

test-integration:
	$(PYTEST) -q tests/integration tests/contracts -m "$(PYTEST_FAST_MARKERS)" -n 0

test-db:
	$(PYTEST) -q -m "db" -n 0

test-e2e:
	@if [ -f src/frontend/package.json ]; then \
		cd src/frontend && pnpm run test:e2e; \
	else \
		echo "No src/frontend/package.json found, skipping frontend e2e tests."; \
	fi

check: lint format-check typecheck test check-release check-docs check-duplicates check-frontend check-codebase-tree

quality-gate: check

check-release:
	uv run python scripts/validate_release.py hygiene
	uv run python scripts/validate_release.py metadata
	uv run python scripts/check_agents_md_freshness.py

check-docs:
	uv run python scripts/check_docs_quality.py
	uv run python scripts/check_harness_engineering.py

check-duplicates:
	./scripts/run_duplicate_check.zsh

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
	@if [ -f src/frontend/package.json ]; then \
		cd src/frontend && pnpm dlx knip --no-progress || echo "Unused dependencies detected - see report above"; \
	fi

check-frontend:
	@if [ -f src/frontend/package.json ]; then \
		cd src/frontend && pnpm install --frozen-lockfile && pnpm run api:check && pnpm run type-check && pnpm run lint:robustness && pnpm run test:unit && CI=true pnpm run build; \
	else \
		echo "No src/frontend/package.json found, skipping frontend checks."; \
	fi

check-codebase-tree:
	uv run python scripts/check_codebase_tree.py

api-check:
	uv run python scripts/openapi_tools.py validate
	@if [ -f src/frontend/package.json ]; then \
		cd src/frontend && pnpm run api:check; \
	fi

api-sync:
	uv run python scripts/openapi_tools.py generate
	@if [ -f src/frontend/package.json ]; then \
		cd src/frontend && pnpm run api:sync; \
	fi

build:
	rm -rf dist build
	uv build

sync-ui:
	@echo "Syncing frontend dist to packaged UI assets..."
	rm -rf src/fleet_rlm/ui/dist
	mkdir -p src/fleet_rlm/ui
	cp -R src/frontend/dist src/fleet_rlm/ui/dist

build-ui:
	cd src/frontend && pnpm install --frozen-lockfile && CI=true ./node_modules/.bin/vp build
	node src/frontend/scripts/ensure-entrypoint.mjs --dist-dir src/frontend/dist
	$(MAKE) sync-ui

build-release: build-ui
	rm -rf dist build
	uv build
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

MLFLOW_LOCAL_BACKEND_STORE_URI ?= sqlite:///.data/mlruns.db

mlflow:
	uv run mlflow server --backend-store-uri $(MLFLOW_LOCAL_BACKEND_STORE_URI) --port 5001

mlflow-upgrade:
	uv run mlflow db upgrade $(MLFLOW_LOCAL_BACKEND_STORE_URI)

mlflow-server: mlflow

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

frontend-check:
	$(MAKE) check-frontend

release-artifacts:
	$(MAKE) build-release

cloud-preflight:
	@echo "Checking fastapi CLI is available in the locked env..."
	uv run fastapi --help >/dev/null
	@echo "Importing configured FastAPI entrypoint..."
	FLEET_RLM_SERVE_UI=false APP_ENV=local DATABASE_REQUIRED=false \
		uv run python -c "from fleet_rlm.api.main import app; print(f'{app.title} {app.version}')"
	@echo "Enumerating routes from create_app()..."
	FLEET_RLM_SERVE_UI=false APP_ENV=local DATABASE_REQUIRED=false \
		uv run python -c "from fleet_rlm.api.main import create_app; a = create_app(); print('\n'.join(sorted({getattr(r, 'path', '<?>') for r in a.routes})))"
	@echo "cloud-preflight OK"
