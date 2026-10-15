PYTHON_SOURCES := src/fleet_rlm tests scripts

.PHONY: help install install-dev dev serve cli eval-dataset eval optimize format format-check lint typecheck test api-check api-sync check-docs check build clean check-security precommit-run

help:
	@echo "Fleet-RLM Lite development targets:"
	@echo "  make install-dev  Install runtime and development dependencies"
	@echo "  make serve        Start the local FastAPI SSE server"
	@echo "  make cli          Show terminal client help"
	@echo "  make eval-dataset Create the synthetic evaluation dataset (writes to Unity Catalog)"
	@echo "  make eval         Run synthetic cases against a live server (live model and sandbox cost)"
	@echo "  make optimize SESSION=<id>  Run GEPA compile and gated evaluation (live, explicit approval only)"
	@echo "  make test         Run non-live unit and boundary tests"
	@echo "  make lint         Run Ruff"
	@echo "  make typecheck    Run ty on the public package"
	@echo "  make api-sync     Regenerate openapi.yaml from FastAPI"
	@echo "  make check        Run tests, formatting, lint, type, docs, API, and build checks"

install:
	uv sync --no-dev

install-dev:
	uv sync --dev

dev: serve

serve:
	uv run fleet serve

cli:
	uv run fleet --help

format:
	uv run ruff format $(PYTHON_SOURCES)

format-check:
	uv run ruff format --check $(PYTHON_SOURCES)

lint:
	uv run ruff check $(PYTHON_SOURCES)

typecheck:
	uv run ty check src/fleet_rlm

eval-dataset:
	uv run fleet eval dataset

eval:
	uv run fleet eval run

optimize:
	@test -n "$(SESSION)" || (echo "SESSION=<id> is required" && exit 2)
	uv run fleet optimize --session $(SESSION)

test:
	uv run pytest

api-check:
	uv run python scripts/openapi_contract.py check

api-sync:
	uv run python scripts/openapi_contract.py generate

check-docs:
	uv run python scripts/check_docs.py

check-security:
	uvx pip-audit

build:
	uv build

check: test lint format-check typecheck check-docs api-check build

precommit-run:
	uv run pre-commit run --all-files

clean:
	rm -rf .ruff_cache .pytest_cache .ty build dist
	find src tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
