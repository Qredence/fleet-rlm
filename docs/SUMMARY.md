# Table of contents

* [Documentation Home](index.md)
* [fleet-rlm Documentation](README.md)
* [Product Spec](explanation/product-spec.md)
* [Architecture Overview](architecture.md)
* [Current Architecture and Transition Note](notes/current-architecture-transition.md)
* [Frontend Product Surface Guide](guides/frontend-product-surface.md)
* [Optimization Page Spec](specs/optimization-page.md)
* [Wiring Analysis](wiring-analysis.md)
* [Reference Index](reference/index.md)
* [Explanation Index](explanation/index.md)

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
  * [CLI Commands](reference/cli.md)
  * [HTTP and WebSocket API](reference/http-api.md)
  * [Python API](reference/python-api.md)
  * [Auth Modes](reference/auth.md)
  * [Database Architecture](reference/database.md)
  * [Sandbox API](reference/sandbox-api.md)
  * [Sandbox File System](reference/sandbox-fs.md)
  * [Source Layout](reference/source-layout.md)
  * [Frontend Architecture](reference/frontend-architecture.md)
  * [Frontend Feature Spec](reference/frontend-feature-spec.md)
  * [Frontend Backend Integration](reference/frontend-backend-integration.md)
  * [Daytona Runtime Architecture](reference/daytona-runtime-architecture.md)
  * [Codebase Map](reference/codebase-map.md)
  * [Module Map](reference/module-map.md)
  * [Architecture Decision Records](reference/adr/README.md)
    * [ADR-001: RLM Runtime Architecture](reference/adr/001-rlm-runtime-architecture.md)
    * [ADR-003: Neon/Postgres with RLS](reference/adr/003-neon-postgres-rls-persistence.md)
    * [ADR-004: Dual Auth Modes](reference/adr/004-dual-auth-modes.md)
  * [Release Notes 0.4.99](reference/release-notes-0.4.99.md)
  * [Release Notes 0.4.94](reference/release-notes-0.4.94.md)

## Explanation

* [Explanation](explanation/index.md)
  * [Product Spec](explanation/product-spec.md)
  * [fleet-rlm Concepts](explanation/concepts.md)
  * [User Interaction Flows](explanation/user-flows.md)
  * [Component UML](explanation/component-uml.md)

## Historical Notes

* [Phase 1: Worker Boundary Extraction](notes/phase-1-worker-boundary.md)
* [Phase 2: Websocket Transport Thinning](notes/phase-2-ws-thinning.md)
* [Phase 3: Orchestration Seams](notes/phase-3-orchestration-seams.md)
* [Phase 4: Outer Orchestration](notes/phase-4-outer-orchestration.md)
* [Phase 5: Session Orchestration](notes/phase-5-session-orchestration.md)
* [Phase 6: Terminal Orchestration](notes/phase-6-terminal-orchestration.md)
* [Phase 7/8: Agent Framework Transition](notes/phase-7-8-agent-framework-transition.md)
* [Phase 9: Agent Host HITL Migration](notes/phase-9-agent-host-hitl-migration.md)
* [Phase 10: Agent host session migration](notes/phase-10-agent-host-session-migration.md)
* [Phase 11: Agent Host REPL Bridge](notes/phase-11-agent-host-repl-bridge.md)
* [Phase 12: DSPy Recursive Module GEPA](notes/phase-12-dspy-recursive-module-gepa.md)
* [Phase 13: Recursive Context Assembly](notes/phase-13-recursive-context-assembly.md)
* [Phase 14: Recursive Decomposition Module](notes/phase-14-recursive-decomposition-module.md)
* [Phase 15: Recursive Verification Module](notes/phase-15-recursive-verification-module.md)
* [Phase 17: Recursive Repair Module](notes/phase-17-recursive-repair-module.md)
* [Phase 18: Working Backend Frontend Path](notes/phase-18-working-backend-frontend-path.md)
