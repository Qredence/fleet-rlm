PYTHON_SOURCES = src tests config/test_responses_endpoint.py

.PHONY: help sync sync-dev test lint format check precommit-install precommit-run cli-help sync-scaffold release-check

help:
	@echo "Targets:"
	@echo "  make sync              - Install runtime dependencies with uv"
	@echo "  make sync-dev          - Install dev dependencies with uv"
	@echo "  make test              - Run pytest"
	@echo "  make lint              - Run ruff checks"
	@echo "  make format            - Run ruff formatter"
	@echo "  make check             - Run lint + test"
	@echo "  make sync-scaffold     - Sync .claude/ to src/fleet_rlm/_scaffold/"
	@echo "  make release-check     - Run lint + test + build + twine checks"
	@echo "  make precommit-install - Install pre-commit git hooks"
	@echo "  make precommit-run     - Run pre-commit on all files"
	@echo "  make cli-help          - Show fleet-rlm CLI help"

sync:
	uv sync

sync-dev:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check $(PYTHON_SOURCES)

format:
	uv run ruff format $(PYTHON_SOURCES)

check: lint test

sync-scaffold:
	@echo "Syncing .claude/ to src/fleet_rlm/_scaffold/..."
	rsync -a --delete .claude/skills/ src/fleet_rlm/_scaffold/skills/
	rsync -a --delete .claude/agents/ src/fleet_rlm/_scaffold/agents/
	@echo "Scaffold sync complete"

release-check: sync-scaffold lint test
	rm -rf dist build
	uv build
	uvx twine check dist/*

precommit-install:
	uv run pre-commit install

precommit-run:
	uv run pre-commit run --all-files

cli-help:
	uv run fleet-rlm --help
