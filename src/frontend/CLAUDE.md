# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This is a React + Vite frontend managed by `pnpm`.

- **Install**: `pnpm install --frozen-lockfile`
- **Dev Server**: `pnpm run dev`
- **Build**: `pnpm run build`
- **Format**: `pnpm run format`
- **Lint**: `pnpm run lint`
- **Type Check**: `pnpm run type-check`
- **Unit Tests**: `pnpm run test:unit` (or `pnpm run test:watch` for interactive mode)
- **E2E Tests**: `pnpm run test:e2e` (uses Playwright)
- **Full QA Check**: `pnpm run check` (runs type-check, lint, test:unit, build, and test:e2e)
- **API Types Sync**: `pnpm run api:sync` (syncs OpenAPI spec from backend and generates TS types)

_Note: The FastAPI backend must be running for full functionality. Start it from the root `fleet-rlm` repo with `uv run fleet-rlm serve-api --port 8000`._

## High-Level Architecture
