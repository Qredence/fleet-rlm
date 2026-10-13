# Fleet RLM documentation

Fleet has one FastAPI/SSE backend in `src/fleet_rlm/` and one maintained
terminal client in `tools/fleet-tui/`. DSPy owns the RLM loop; Daytona executes
generated Python in Sandboxes. Authorized host tools and native semantic calls
cross the authenticated broker boundary. Every root invocation receives bounded
child RLM tools; the single TOML configuration controls their limits.

## Find the right guide

| Task | Guide |
| --- | --- |
| Understand owners and trust boundaries | [Architecture](../ARCHITECTURE.md) and [source layout](reference/source-layout.md) |
| Set up and change the project | [README](../README.md) and [contributing guide](../CONTRIBUTING.md) |
| Configure a runtime configuration | [Configuration](reference/configuration.md) and [generated configuration environment reference](reference/configuration-environment.md) |
| Use the backend or terminal | [HTTP API](reference/http-api.md), [CLI](reference/cli.md), and [terminal UI](how-to-guides/terminal-tui.md) |
| Understand DSPy and Daytona execution | [Integration guide](how-to-guides/dspy-integration.md) and [Daytona Snapshot guide](how-to-guides/daytona-snapshot.md) |
| Search public sources and navigate web pages | [Public web research](how-to-guides/web-research.md) |
| Validate a change | [Testing strategy](how-to-guides/testing-strategy.md) |
| Understand runtime routing, optimization, and MLflow observation | [Evaluation and optimization](how-to-guides/evaluation-optimization.md) |
| Run MLflow evaluation and monitoring workflows | [MLflow evaluation pilot](how-to-guides/mlflow-evaluation.md) |

Browse the [complete table of contents](SUMMARY.md) or the
[reference index](reference/index.md) for the remaining guides and dated reports.

## Sources of truth

Current behavior comes from the backend, TUI, `config/fleet.toml`, tests, and
generated contracts. The [architecture](../ARCHITECTURE.md) describes durable
ownership; the [testing strategy](how-to-guides/testing-strategy.md) states
the limits of local and live validation. Dated plans, measurements, and
receipts retain their original evidence scope and do not set current runtime
policy or certify a later revision. See [Dated reports](SUMMARY.md) for retained
PRD and capability receipts.

Regenerate `openapi.yaml` and the TUI HTTP types with `make api-sync`, stream
fixtures with `make stream-sync`, and the configuration environment reference with
`make config-reference`. The [agent guide](../AGENTS.md) lists the matching
verification commands.
