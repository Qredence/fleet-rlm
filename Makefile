PYTHON_SOURCES = src tests config/test_responses_endpoint.py

.PHONY: help sync sync-dev sync-all test lint format-check format typecheck metadata-check security-check frontend-check quality-gate check precommit-install precommit-run cli-help sync-scaffold release-check clean

help:
	@echo "Targets:"
	@echo "  make sync              - Install runtime dependencies with uv"
	@echo "  make sync-dev          - Install dev dependencies with uv"
	@echo "  make sync-all          - Install all extras + dev dependencies with uv"
	@echo "  make test              - Run pytest (-q)"
	@echo "  make lint              - Run ruff check"
	@echo "  make format-check      - Run ruff format --check"
	@echo "  make format            - Run ruff format (writes changes)"
	@echo "  make typecheck         - Run ty check"
	@echo "  make metadata-check    - Run release metadata/hygiene scripts"
	@echo "  make security-check    - Run pip-audit + bandit"
	@echo "  make frontend-check    - Run frontend checks when src/frontend exists"
	@echo "  make quality-gate      - Run lint + format-check + typecheck + test + metadata-check + frontend-check"
	@echo "  make check             - Alias for quality-gate"
	@echo "  make clean             - Remove caches and local generated artifacts"
	@echo "  make sync-scaffold     - Sync .claude/ to src/fleet_rlm/_scaffold/"
	@echo "  make release-check     - Run clean + quality-gate + security-check + build + twine checks"
	@echo "  make precommit-install - Install pre-commit git hooks"
	@echo "  make precommit-run     - Run pre-commit on all files"
	@echo "  make cli-help          - Show fleet-rlm CLI help"

sync:
	uv sync

sync-dev:
	uv sync --extra dev

sync-all:
	uv sync --all-extras --dev

test:
	uv run pytest -q

lint:
	uv run ruff check $(PYTHON_SOURCES)

format-check:
	uv run ruff format --check $(PYTHON_SOURCES)

format:
	uv run ruff format $(PYTHON_SOURCES)

typecheck:
	uv run ty check src --exclude "src/fleet_rlm/_scaffold/**"

metadata-check:
	uv run python scripts/check_release_hygiene.py
	uv run python scripts/check_release_metadata.py

security-check:
	uvx pip-audit
	uvx bandit -q -r src/fleet_rlm -x tests,src/fleet_rlm/_scaffold -lll

frontend-check:
	@if [ -f src/frontend/package.json ]; then \
		cd src/frontend && bun install --frozen-lockfile && bun run type-check && bun run lint:robustness && bun run test:unit && bun run build; \
	else \
		echo "No src/frontend/package.json found, skipping frontend checks."; \
	fi

quality-gate: lint format-check typecheck test metadata-check frontend-check

check: quality-gate

sync-scaffold:
	@echo "Syncing .claude/ to src/fleet_rlm/_scaffold/..."
	rsync -a --delete .claude/skills/ src/fleet_rlm/_scaffold/skills/
	rsync -a --delete .claude/agents/ src/fleet_rlm/_scaffold/agents/
	@echo "Scaffold sync complete"

release-check: clean quality-gate security-check
	rm -rf dist build
	uv build
	uvx twine check dist/*

clean:
	@echo "Cleaning caches and local generated artifacts..."
	find . -type d \( -name ".ruff_cache" -o -name "__pycache__" -o -name ".pytest_cache" -o -name ".mypy_cache" \) -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist .coverage .venv-release-smoke
	rm -f server.log fleet_rlm.db
	@echo "Cleanup complete"

precommit-install:
	uv run pre-commit install

precommit-run:
	uv run pre-commit run --all-files

cli-help:
	uv run fleet-rlm --help
