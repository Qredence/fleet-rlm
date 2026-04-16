# Documentation Home

`fleet-rlm` is a Daytona-backed recursive DSPy workbench. The maintained product path is the live workspace runtime, the durable volume browser, the optimization surface, runtime settings and diagnostics, and the session history and replay view. Historical audits and migration notes are kept separate so the current docs stay readable.

## Start Here

- [README](README.md) for the top-level docs landing page
- [Product Spec](explanation/product-spec.md) for the user-facing product contract
- [Architecture Overview](architecture.md) for the current layer ownership model
- [Reference Index](reference/index.md) for implementation-facing contracts
- [Explanation Index](explanation/index.md) for conceptual docs
- [Current Architecture and Transition Note](notes/current-architecture-transition.md) for the active migration boundary

## Current Docs

- [Tutorials](tutorials/index.md)
- [How-to Guides](how-to-guides/index.md)
- [Reference](reference/index.md)
- [Explanation](explanation/index.md)
- [Frontend Product Surface Guide](guides/frontend-product-surface.md)
- [Optimization Page Spec](specs/optimization-page.md)
- [Wiring Analysis](wiring-analysis.md)

## Current Product Surfaces

- [Workbench](explanation/product-spec.md)
- [Volumes](explanation/product-spec.md)
- [Optimization](explanation/product-spec.md)
- [Settings](explanation/product-spec.md)
- [History](reference/frontend-backend-integration.md)

## Historical Notes

- [Historical Snapshots](historical/index.md)
- [Architecture and migration history](historical/index.md#architecture-and-migration-history)

## Complete Table Of Contents

- [SUMMARY.md](SUMMARY.md)

## Source of Truth

When docs disagree with the code, trust the code and generated contracts:

- backend routes and websocket behavior in `src/fleet_rlm/api/`
- outer host policy in `src/fleet_rlm/agent_host/`
- runtime and Daytona execution in `src/fleet_rlm/runtime/` and `src/fleet_rlm/integrations/daytona/`
- frontend route and workspace behavior in `src/frontend/src/`
