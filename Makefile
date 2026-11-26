.PHONY: help check check-backend check-frontend dev-backend dev-frontend mlflow daytona-image-context

help:
	@echo "Fleet-RLM targets:"
	@echo "  make check          Run all backend and frontend checks"
	@echo "  make check-backend  Run ruff check/format, lint-imports, ty, and pytest"
	@echo "  make check-frontend Run pnpm lint, typecheck, test, and build"
	@echo "  make dev-backend    Start FastAPI dev server on port 8000"
	@echo "  make dev-frontend   Start Vite dev server on port 5173"
	@echo "  make mlflow         Start external MLflow 3.18 server on 127.0.0.1:5001"
	@echo "  make daytona-image-context REV=<commit> OUT=<tar>  Archive committed sandbox build inputs"

check: check-backend check-frontend

.PHONY: check-hygiene
check-hygiene:
	uv run --locked --project backend python scripts/check_hygiene.py

check-backend: check-hygiene
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run ty check src && uv run pytest

check-frontend:
	cd frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm build

dev-backend:
	cd backend && uv run --locked $(if $(wildcard backend/.env),--env-file .env) \
		env -u DAYTONA_API_URL -u DAYTONA_V2_API_URL uvicorn fleet_rlm.api:app --reload --port 8000

dev-frontend:
	cd frontend && pnpm dev --port 5173 --strictPort

mlflow:
	docker compose up -d mlflow

daytona-image-context: export DAYTONA_CONTEXT_REV = $(REV)
daytona-image-context: export DAYTONA_CONTEXT_OUT = $(OUT)
daytona-image-context:
	@test -n "$$DAYTONA_CONTEXT_REV" && test -n "$$DAYTONA_CONTEXT_OUT" || { echo 'Usage: make daytona-image-context REV=<commit> OUT=<tar>' >&2; exit 2; }
	@commit=$$(git rev-parse --verify --end-of-options "$$DAYTONA_CONTEXT_REV^{commit}") && \
		git archive --format=tar --output="$$DAYTONA_CONTEXT_OUT" "$$commit" -- \
		backend/snapshot/Dockerfile backend/snapshot/requirements.lock backend/skills && \
		echo "Daytona image context for $$commit: $$DAYTONA_CONTEXT_OUT"
