# fleet-rlm Documentation

This index separates **active, maintained documentation** from **historical research/archive material**.

Use this page as the primary entry point for current `v0.4.8` workflows.

## Active Docs (Maintained)

### Getting Started
- [How-to Guides](how-to-guides/index.md)
- [Tutorials](tutorials/index.md)
- [Contributing Guide](contributing.md)

### Runtime and Operations
- [Auth Modes (Dev vs Entra)](auth.md)
- [Database Architecture (Neon + RLS)](db.md)
- [Architecture Overview](architecture.md)
- [Concepts](concepts.md)

### Interfaces and Contracts
- [Reference Index](reference/index.md)
- [CLI Reference](reference/cli.md)
- [HTTP and WebSocket API Reference](reference/http-api.md)
- [Python API Reference](reference/python-api.md)
- [Source Layout](reference/source-layout.md)
- [Frontend ↔ Backend Integration](reference/frontend-backend-integration.md)

### Reviews
- [Code Quality / Maintainability Audit (2026-02-25)](reviews/code_quality_maintainability_audit_2026-02-25.md)

## Archive / Research (Historical)

The folders below are preserved for design history and analysis context. They are **not** the operational source of truth for current runtime behavior.

- [Artifacts Archive](artifacts/README.md)
- [Plans Archive](plans/README.md)
- [References Archive](references/README.md)
- [Explanation Archive](explanation/README.md)

## Source-of-Truth Policy

When docs conflict with code, treat these as authoritative:
- CLI surfaces: `src/fleet_rlm/cli.py`, `src/fleet_rlm/cli_commands/`, `src/fleet_rlm/fleet_cli.py`
- HTTP contract: `openapi.yaml`
- WebSocket behavior: `src/fleet_rlm/server/routers/ws/api.py`
- Python interfaces: `src/fleet_rlm/runners.py`, `src/fleet_rlm/react/signatures.py`
