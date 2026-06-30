# Documentation Home

`fleet-rlm` is a Daytona-backed recursive DSPy workbench. Start with the tutorials and how-to guides below; move down into explanation and reference when you need conceptual or implementation detail.

## Start Here (User Path)

1. **[Tutorials](tutorials/index.md)** — learn by doing: basic usage, document analysis, interactive chat.
2. **[How-to Guides](how-to-guides/index.md)** — solve specific problems: installation, Codex local setup, deployment, DSPy integration, troubleshooting, MLflow workflows.
3. **[Explanation](explanation/index.md)** — understand the product: spec, concepts, user flows.
4. **[Agent Harness](agent-harness/README.md)** — Codex operating model, local feedback loop, architecture invariants, and drift control.

## Reference

- **[Reference Index](reference/index.md)** — CLI, HTTP/WebSocket API, Python API, auth modes, database, sandbox surfaces, source layout.
- **[Frontend Product Surface Guide](explanation/frontend-product-surface.md)**

## Architecture & Internals

Read these after you've seen the product:

- **[Architecture Overview](architecture.md)** — current layer ownership model.
- **[Agent Harness](agent-harness/README.md)** — repo-local harness engineering controls.
- **[Wiring Analysis](explanation/wiring-analysis.md)**
- **[Releasing to PyPI](how-to-guides/releasing.md)** — automated and manual release flow

## Current Product Surfaces

- [Workbench](explanation/product-spec.md)
- [Volumes](explanation/product-spec.md)
- [Settings](explanation/product-spec.md)

## API Reference Surfaces

- [Sandbox API](reference/sandbox-api.md) — Daytona sandbox lifecycle management
- [Runs API](reference/runs-api.md) — Execution trace step browsing

## Complete Table Of Contents

- [SUMMARY.md](SUMMARY.md)

## Source of Truth

When docs disagree with the code, trust the code and generated contracts:

- backend routes and websocket behavior in `src/fleet_rlm/api/`
- transport, auth, and websocket lifecycle in `src/fleet_rlm/api/`
- runtime and Daytona execution in `src/fleet_rlm/runtime/` and `src/fleet_rlm/integrations/daytona/`
- frontend route and workspace behavior in `src/frontend/src/`
