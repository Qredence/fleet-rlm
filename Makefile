PYTHON_SOURCES = src tests
PYTEST_FAST_MARKERS = not live_llm and not benchmark

.PHONY: help sync sync-dev sync-all test test-fast test-unit test-ui test-integration lint format-check format typecheck metadata-check docs-check security-check dependency-check frontend-check quality-gate check precommit-install precommit-run cli-help sync-scaffold sync-ui build-ui release-check clean mlflow-server

help:
	@echo "Targets:"
	@echo "  make sync              - Install runtime dependencies with uv"
	@echo "  make sync-dev          - Install dev dependencies with uv"
	@echo "  make sync-all          - Install all extras + dev dependencies with uv"
	@echo "  make test              - Alias for test-fast"
	@echo "  make test-fast         - Run default non-live/non-benchmark tests"
	@echo "  make test-unit         - Run unit tests (non-live/non-benchmark)"
	@echo "  make test-ui           - Run UI tests (non-live/non-benchmark)"
	@echo "  make test-integration  - Run integration + e2e tests (non-live/non-benchmark)"
	@echo "  make lint              - Run ruff check"
	@echo "  make format-check      - Run ruff format --check"
	@echo "  make format            - Run ruff format (writes changes)"
	@echo "  make typecheck         - Run ty check"
	@echo "  make metadata-check    - Run release metadata/hygiene and AGENTS.md validation"
	@echo "  make docs-check        - Run docs quality checks"
	@echo "  make security-check    - Run pip-audit + bandit"
	@echo "  make dependency-check  - Check for unused dependencies (deptry, knip)"
	@echo "  make frontend-check    - Run frontend checks when src/frontend exists"
	@echo "  make quality-gate      - Run lint + format-check + typecheck + test + metadata-check + frontend-check"
	@echo "  make check             - Alias for quality-gate"
	@echo "  make sync-ui           - Copy src/frontend/dist/ into src/fleet_rlm/ui/dist/"
	@echo "  make build-ui          - Build the frontend and sync packaged UI assets"
	@echo "  make mlflow-server     - Start a local MLflow OSS tracking server on port 5001"
	@echo "  make clean             - Remove caches and local generated artifacts"
	@echo "  make sync-scaffold     - Sync .claude/ to src/fleet_rlm/_scaffold/"
	@echo "  make release-check     - Run clean + quality-gate + security-check + build + twine checks"
	@echo "  make precommit-install - Install pre-commit and pre-push git hooks"
	@echo "  make precommit-run     - Run pre-commit on all files"
	@echo "  make cli-help          - Show fleet-rlm CLI help"

sync:
	uv sync

sync-dev:
	uv sync --extra dev

sync-all:
	uv sync --all-extras --dev

test:
	$(MAKE) test-fast

test-fast:
	uv run pytest -q -m "$(PYTEST_FAST_MARKERS)"

test-unit:
	uv run pytest -q tests/unit -m "$(PYTEST_FAST_MARKERS)"

test-ui:
	uv run pytest -q tests/ui -m "$(PYTEST_FAST_MARKERS)"

test-integration:
	uv run pytest -q tests/integration tests/e2e -m "$(PYTEST_FAST_MARKERS)"

lint:
	uv run ruff check $(PYTHON_SOURCES)

format-check:
	uv run ruff format --check $(PYTHON_SOURCES)

format:
	uv run ruff format $(PYTHON_SOURCES)

typecheck:
	uv run ty check src \
		--exclude "src/fleet_rlm/_scaffold/**"

metadata-check:
	uv run python scripts/validate_release.py hygiene
	uv run python scripts/validate_release.py metadata
	uv run python scripts/check_agents_md_freshness.py

docs-check:
	uv run python scripts/check_docs_quality.py

security-check:
	uvx pip-audit
	uvx bandit -q -r src/fleet_rlm -x tests,src/fleet_rlm/_scaffold -lll

dependency-check:
	uvx deptry .
	@if [ -f src/frontend/package.json ]; then \
		cd src/frontend && pnpm dlx knip --no-progress || echo "⚠️ Unused dependencies detected - see report above"; \
	fi

frontend-check:
	@if [ -f src/frontend/package.json ]; then \
		cd src/frontend && pnpm install --frozen-lockfile && pnpm run api:check && pnpm run type-check && pnpm run lint:robustness && pnpm run test:unit && pnpm run build; \
	else \
		echo "No src/frontend/package.json found, skipping frontend checks."; \
	fi

quality-gate: lint format-check typecheck test-fast metadata-check docs-check frontend-check

check: quality-gate

mlflow-server:
	uv run mlflow server --backend-store-uri sqlite:///mlruns.db --port 5001

sync-scaffold:
	@echo "Syncing .claude/ to src/fleet_rlm/_scaffold/..."
	mkdir -p src/fleet_rlm/_scaffold/teams src/fleet_rlm/_scaffold/hooks
	rsync -a --delete .claude/skills/ src/fleet_rlm/_scaffold/skills/
	rsync -a --delete .claude/agents/ src/fleet_rlm/_scaffold/agents/
	[ -d .claude/hooks ] && rsync -a --delete .claude/hooks/ src/fleet_rlm/_scaffold/hooks/ || true
	[ -d .claude/teams ] && rsync -a --delete .claude/teams/ src/fleet_rlm/_scaffold/teams/ || true
	@echo "Scaffold sync complete"

sync-ui:
	@echo "Syncing frontend dist to packaged UI assets..."
	rm -rf src/fleet_rlm/ui/dist
	mkdir -p src/fleet_rlm/ui
	cp -R src/frontend/dist src/fleet_rlm/ui/dist

build-ui:
	cd src/frontend && pnpm install --frozen-lockfile && pnpm run build
	$(MAKE) sync-ui

release-check: clean quality-gate security-check build-ui
	rm -rf dist build
	uv build
	uv run python scripts/validate_release.py wheel
	uvx twine check dist/*

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

cli-help:
	uv run fleet-rlm --help
