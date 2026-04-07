# Reference

Authoritative contracts, interfaces, and implementation-facing facts.

## Interfaces

- [CLI Commands](cli.md)
- [HTTP and WebSocket API](http-api.md)
- [Python API](python-api.md)
- [Source Layout](source-layout.md)
- [Frontend Architecture](frontend-architecture.md)
- [Frontend ↔ Backend Integration](frontend-backend-integration.md)

## Platform and Runtime

- [Auth Modes](auth.md)
- [Database Architecture](database.md)
- [Sandbox File System](sandbox-fs.md)

## Architecture Decision Records

- [ADR Index](adr/README.md)
  Architecture Decision Records documenting key design choices.
- [ADR-001: RLM Runtime Architecture](adr/001-rlm-runtime-architecture.md)
  dspy.RLM as the core reasoning engine with ReAct orchestration.
- [ADR-003: Neon/Postgres with RLS](adr/003-neon-postgres-rls-persistence.md)
  Serverless PostgreSQL with Row-Level Security for multi-tenant persistence.
- [ADR-004: Dual Auth Modes](adr/004-dual-auth-modes.md)
  Development and Entra authentication modes.

## Internal Maps

- [Codebase Map and Simplification Audit](codebase-map.md)
  Current full-package architecture map for `src/fleet_rlm`, including runtime surfaces, support packages, and refactor hotspots.
