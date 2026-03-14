# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This is a React + Vite frontend managed by `pnpm` and the Vite+ (`vp`) toolchain.

- **Install**: `vp install`
- **Dev Server**: `vp dev`
- **Lint**: `vp lint`
- **Type Check**: `vp run type-check`
- **Unit Tests**: `vp test` (interactive watch mode by default) or `vp run test:unit` (single pass). To run a single test: `vp test <filename>`
- **E2E Tests**: `vp run test:e2e` (uses Playwright). To run a single E2E test: `vp run test:e2e <filename>`
- **Full QA Check**: `vp check` (runs types, lint, format) followed by `vp test`
- **API Types Sync**: `vp run api:sync` (syncs OpenAPI spec from backend and generates TS types)

_Note: The FastAPI backend must be running for full functionality. Start it from the root `fleet-rlm` repo with `uv run fleet-rlm serve-api --port 8000`._

## High-Level Architecture

- **`src/app/`**: Application shell, top-level layout (`RootLayout.tsx`), routing, and page components.
- **`src/features/`**: Domain-specific logic, components, and views (e.g., `chat`, `settings`, `skill-library`). This is where feature-specific business logic is encapsulated.
- **`src/components/ui/`**: Low-level, reusable UI primitives (mostly Shadcn UI).
- **`src/components/shared/`**: Reusable components specific to this application's domain that don't belong to a single feature.
- **`src/hooks/`**: Application-wide React hooks, Context providers, and React Query data fetching wrappers.
- **`src/lib/`**: Core utilities, configuration, and API clients.
  - **`src/lib/rlm-api/`**: Contains the client logic for communicating with the FastAPI backend, utilizing both REST endpoints and WebSockets (`/api/v1/ws/chat`, `/api/v1/ws/execution`).
  - **`src/lib/rlm-api/generated/openapi.ts`**: Auto-generated types from the backend OpenAPI spec. **Do not edit manually**.
- **Testing**:
  - Unit tests use Vitest + jsdom and are strictly colocated with the code they test (in `__tests__/` directories or as `*.test.{ts,tsx}` files). Global test setup is in `src/test/setup.ts`.
  - End-to-end tests use Playwright and are located in `tests/e2e/`.

## Environment Variables

The application relies on a `.env` file (copy from `.env.example`). Key variables include:

- `VITE_FLEET_API_URL` (usually `http://localhost:8000`)
- `VITE_FLEET_WS_URL` (usually `ws://localhost:8000/ws/chat`)

If the backend isn't available or capability checks fail, the app gracefully disables unsupported features (like memory or skills management) and shows notices instead of attempting to use legacy mocks.
