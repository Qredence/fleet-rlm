# Documentation Home

`fleet-rlm` is a Daytona-backed recursive DSPy workbench. Start with the tutorials and how-to guides below; move down into explanation and reference when you need conceptual or implementation detail.

## Start Here (User Path)

1. **[Tutorials](tutorials/index.md)** — learn by doing: basic usage, document analysis, interactive chat.
2. **[How-to Guides](how-to-guides/index.md)** — solve specific problems: installation, deployment, DSPy integration, troubleshooting, MLflow workflows.
3. **[Explanation](explanation/index.md)** — understand the product: spec, concepts, user flows.

## Reference

- **[Reference Index](reference/index.md)** — CLI, HTTP/WebSocket API, Python API, auth modes, database, sandbox surfaces, source layout.
- **[Frontend Product Surface Guide](explanation/frontend-product-surface.md)**
- **[Optimization Page Spec](specs/optimization-page.md)**

## Architecture & Internals

Read these after you've seen the product:

- **[Architecture Overview](architecture.md)** — current layer ownership model.
- **[Wiring Analysis](explanation/wiring-analysis.md)**
- **[Frontend Simplification Design](specs/frontend-simplification-design.md)**

## Current Product Surfaces

- [Workbench](explanation/product-spec.md)
- [Volumes](explanation/product-spec.md)
- [Optimization](explanation/product-spec.md)
- [Settings](explanation/product-spec.md)
- [History](reference/frontend-backend-integration.md)

## API Reference Surfaces

- [Sandbox API](reference/sandbox-api.md) — Daytona sandbox lifecycle management
- [Runs API](reference/runs-api.md) — Execution trace step browsing
- [Memory API](reference/retired/memory-api.md) — Retired product surface

## Complete Table Of Contents

- [SUMMARY.md](SUMMARY.md)

## Source of Truth

When docs disagree with the code, trust the code and generated contracts:

- backend routes and websocket behavior in `src/fleet_rlm/api/`
- transport, auth, and websocket lifecycle in `src/fleet_rlm/api/`
- runtime and Daytona execution in `src/fleet_rlm/runtime/` and `src/fleet_rlm/integrations/daytona/`
- frontend route and workspace behavior in `src/frontend/src/`
