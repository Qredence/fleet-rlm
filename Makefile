PYTHON_SOURCES := src/fleet_rlm tests scripts notebooks

.PHONY: help install install-dev dev serve web cli eval-dataset eval optimize format format-check lint typecheck test api-check api-sync check-docs check build clean check-security check-release build-release verify-release-artifacts release-smoke release-manifest-check precommit-run

help:
	@echo "Fleet-RLM Lite development targets:"
	@echo "  make install-dev  Install runtime and development dependencies"
	@echo "  make serve        Start the local FastAPI SSE server"
	@echo "  make web          Serve the Textual client in a browser on loopback"
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

web:
	uv run fleet web

cli:
	uv run fleet --help

format:
	uv run ruff format $(PYTHON_SOURCES)

format-check:
	uv run ruff format --check $(PYTHON_SOURCES)

lint:
	uv run ruff check $(PYTHON_SOURCES)

typecheck:
	uv run ty check

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

# DiskCache 5.6.3 has no fixed upstream release; suppress only PYSEC-2026-2447.
# pip-audit cannot hash the pinned LongCoT Git source; retain its resolved dependencies in the audit.
check-security:
	@set -eu; requirements=$$(mktemp); trap 'rm -f "$$requirements"' EXIT; \
		uv export --locked --all-groups --no-emit-project --no-emit-package longcot --format requirements.txt --python 3.12 --output-file "$$requirements" > /dev/null; \
		uv run --locked -- pip-audit --requirement "$$requirements" --ignore-vuln PYSEC-2026-2447

check-release:
	@test -n "$$FLEET_RELEASE_VERSION" && test -n "$$FLEET_RELEASE_SHA" || (echo "FLEET_RELEASE_VERSION and FLEET_RELEASE_SHA are required" && exit 2)
	@test -n "$$CIRCLE_BRANCH" && test -n "$$CIRCLE_SHA1" || (echo "CIRCLE_BRANCH and CIRCLE_SHA1 are required" && exit 2)
	uv run --locked python scripts/release_checks.py preflight --version "$$FLEET_RELEASE_VERSION" --sha "$$FLEET_RELEASE_SHA" --branch "$$CIRCLE_BRANCH" --pipeline-sha "$$CIRCLE_SHA1"

build-release:
	@test -n "$$FLEET_RELEASE_VERSION" || (echo "FLEET_RELEASE_VERSION is required" && exit 2)
	@test ! -e dist || (echo "dist/ already exists; refusing to overwrite release artifacts" && exit 2)
	uv build
	uv run --locked python scripts/release_checks.py verify-artifacts --version "$$FLEET_RELEASE_VERSION" --dist dist
	uv run --locked twine check dist/fleet_rlm-*
	uv run --locked python scripts/release_checks.py create-manifest --dist dist
	uv run --locked python scripts/release_checks.py verify-manifest --dist dist

verify-release-artifacts:
	@test -n "$$FLEET_RELEASE_VERSION" || (echo "FLEET_RELEASE_VERSION is required" && exit 2)
	uv run --locked python scripts/release_checks.py verify-artifacts --version "$$FLEET_RELEASE_VERSION" --dist dist
	uv run --locked python scripts/release_checks.py verify-manifest --dist dist

release-manifest-check:
	uv run --locked python scripts/release_checks.py verify-manifest --dist dist

release-smoke:
	@test -n "$$FLEET_RELEASE_VERSION" || (echo "FLEET_RELEASE_VERSION is required" && exit 2)
	@set -eu; requirements=$$(mktemp); trap 'rm -f "$$requirements"' EXIT; \
		uv export --locked --no-dev --no-emit-project --format requirements.txt --python 3.12 --output-file "$$requirements" > /dev/null; \
		uv run --locked python scripts/release_checks.py smoke --version "$$FLEET_RELEASE_VERSION" --wheel dist/fleet_rlm-"$$FLEET_RELEASE_VERSION"-*.whl --requirements "$$requirements"

build:
	uv build

check: test lint format-check typecheck check-docs api-check build

precommit-run:
	uv run pre-commit run --all-files

clean:
	rm -rf .ruff_cache .pytest_cache .ty build dist
	find src tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
