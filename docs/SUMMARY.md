# Table of contents

* [Documentation Home](index.md)
* [Architecture Overview](architecture.md)
* [fleet-rlm Documentation](README.md)
* [Contributing to fleet-rlm](../CONTRIBUTING.md)
* [LiteLLM Proxy Model Availability](litellm-models.md)

## Tutorials

* [Tutorials](tutorials/index.md)
  * [Tutorial 01: Basic Usage](tutorials/01-basic-usage.md)
  * [Tutorial 02: Document Analysis](tutorials/02-doc-analysis.md)
  * [Tutorial 03: Interactive Chat](tutorials/03-interactive-chat.md)

## How-to Guides

* [How-to Guides](how-to-guides/index.md)
  * [Installation Guide](how-to-guides/installation.md)
  * [Developer Setup](how-to-guides/developer-setup.md)
  * [Frontend Development](how-to-guides/frontend-development.md)
  * [Testing Strategy](how-to-guides/testing-strategy.md)
  * [Runtime Setup from Frontend Settings](how-to-guides/runtime-settings.md)
  * [DSPy Integration Guide](how-to-guides/dspy-integration.md)
  * [Deploying the API Server](how-to-guides/deploying-server.md)
  * [Using the MCP Server](how-to-guides/using-mcp-server.md)
  * [Using with Claude Code](how-to-guides/using-claude-code-agents.md)
  * [Jupyter Notebook Workflows](how-to-guides/using-notebooks.md)
  * [MLflow Tracing, Feedback, Eval, and Optimization](how-to-guides/mlflow-workflows.md)
  * [Performance Regression Guardrail](how-to-guides/performance-regression-guardrail.md)
  * [Troubleshooting](how-to-guides/troubleshooting.md)

## Reference

* [Reference Documentation](reference/index.md)
  * [CLI Reference](reference/cli.md)
  * [HTTP and WebSocket API Reference](reference/http-api.md)
  * [Python API Reference](reference/python-api.md)
  * [Auth Modes (Dev vs Entra)](reference/auth.md)
  * [Database Architecture](reference/database.md)
  * [Sandbox File System](reference/sandbox-fs.md)
  * [Source Layout (src/fleet_rlm)](reference/source-layout.md)
  * [Python Backend Module Map](reference/module-map.md)
  * [fleet-rlm Codebase Map](reference/codebase-map.md)
  * [Frontend Architecture](reference/frontend-architecture.md)
  * [Frontend ↔ Backend Integration](reference/frontend-backend-integration.md)
  * [Daytona Runtime Architecture](reference/daytona-runtime-architecture.md)
  * [fleet-rlm v0.4.99 Release Notes](reference/release-notes-0.4.99.md)
  * [fleet-rlm v0.4.94 Release Notes](reference/release-notes-0.4.94.md)
  * [Architecture Decision Records](reference/adr/README.md)
    * [ADR-001: RLM Runtime Architecture](reference/adr/001-rlm-runtime-architecture.md)
    * [ADR-003: Neon/Postgres with RLS](reference/adr/003-neon-postgres-rls-persistence.md)
    * [ADR-004: Dual Auth Modes](reference/adr/004-dual-auth-modes.md)

## Explanation

* [Explanation](explanation/index.md)
  * [Adaptive RLM Product Spec](explanation/product-spec.md)
  * [fleet-rlm Concepts](explanation/concepts.md)
  * [User Interaction Flows](explanation/user-flows.md)
  * [Component UML](explanation/component-uml.md)

## Notes

* [Phase 1: Worker Boundary Extraction](notes/phase-1-worker-boundary.md)
* [Phase 2: Websocket Transport Thinning](notes/phase-2-ws-thinning.md)
* [Phase 3: Orchestration Seams](notes/phase-3-orchestration-seams.md)
