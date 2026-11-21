.PHONY: help check check-backend check-frontend dev-backend dev-frontend mlflow

help:
	@echo "Fleet-RLM targets:"
	@echo "  make check          Run all backend and frontend checks"
	@echo "  make check-backend  Run ruff, lint-imports, and pytest"
	@echo "  make check-frontend Run pnpm lint, typecheck, and build"
	@echo "  make dev-backend    Start FastAPI dev server on port 8000"
	@echo "  make dev-frontend   Start Vite dev server on port 5173"
	@echo "  make mlflow         Start external MLflow 3.18 server on 127.0.0.1:5001"

check: check-backend check-frontend

check-backend:
	cd backend && uv run ruff check . && uv run lint-imports && uv run pytest

check-frontend:
	cd frontend && pnpm lint && pnpm typecheck && pnpm build

dev-backend:
	cd backend && uv run uvicorn fleet_rlm.api:app --reload --port 8000

dev-frontend:
	cd frontend && pnpm dev

mlflow:
	docker compose up -d mlflow
