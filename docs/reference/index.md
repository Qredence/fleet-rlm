# Reference

Implementation-facing contracts, interfaces, and current-state facts.

## Current Interfaces

- [CLI Commands](cli.md)
- [HTTP and WebSocket API](http-api.md)
- [Python API](python-api.md)
- [Source Layout](source-layout.md)
- [Frontend Architecture](frontend-architecture.md)
- [Frontend Feature Spec](frontend-feature-spec.md)
- [Frontend Backend Integration](frontend-backend-integration.md)

## Runtime and Platform

- [Auth Modes](auth.md)
- [Database Architecture](database.md)
- [Sandbox File System](sandbox-fs.md)
- [Daytona Runtime Architecture](daytona-runtime-architecture.md)

## Current Maps

- [Codebase Map](codebase-map.md)
  Current ownership map for `src/fleet_rlm`, including runtime surfaces, support packages, and transition hotspots.
- [Module Map](module-map.md)
  Package-level module relationships and runtime exports.

## Architecture Decision Records

- [ADR Index](adr/README.md)
  Architecture Decision Records documenting durable design choices.
- [ADR-001: RLM Runtime Architecture](adr/001-rlm-runtime-architecture.md)
  DSPy `RLM` as the core reasoning engine with ReAct orchestration.
- [ADR-003: Neon/Postgres with RLS](adr/003-neon-postgres-rls-persistence.md)
  Serverless PostgreSQL with Row-Level Security for multi-tenant persistence.
- [ADR-004: Dual Auth Modes](adr/004-dual-auth-modes.md)
  Development and Entra authentication modes.

## Historical Reference Material

- [Release Notes 0.4.99](release-notes-0.4.99.md)
- [Release Notes 0.4.94](release-notes-0.4.94.md)
