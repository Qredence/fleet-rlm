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
