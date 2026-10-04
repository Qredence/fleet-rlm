# Fleet RLM documentation

Fleet has one FastAPI/SSE backend in `src/fleet_rlm/` and one maintained
terminal client in `tools/fleet-tui/`. DSPy owns the RLM loop; Daytona executes
generated Python in Sandboxes. Authorized host tools and native semantic calls
cross the authenticated broker boundary. The single TOML configuration
enables bounded child RLMs through `rlm.recursion_enabled`.

## Find the right guide

| Task | Guide |
| --- | --- |
| Understand owners and trust boundaries | [Architecture](../ARCHITECTURE.md) and [source layout](reference/source-layout.md) |
| Set up and change the project | [README](../README.md) and [contributing guide](../CONTRIBUTING.md) |
| Configure a runtime configuration | [Configuration](reference/configuration.md) and [generated configuration environment reference](reference/configuration-environment.md) |
| Use the backend or terminal | [HTTP API](reference/http-api.md), [CLI](reference/cli.md), and [terminal UI](how-to-guides/terminal-tui.md) |
| Understand DSPy and Daytona execution | [Integration guide](how-to-guides/dspy-integration.md) and [Daytona Snapshot guide](how-to-guides/daytona-snapshot.md) |
| Validate a change | [Testing strategy](how-to-guides/testing-strategy.md) |
| Evaluate or optimize behavior | [Evaluation and monitoring](how-to-guides/evaluation-optimization.md) |

Browse the [complete table of contents](SUMMARY.md) or the
[reference index](reference/index.md) for the remaining guides, decisions, and
historical baselines.

## Sources of truth

Current behavior comes from the backend, TUI, `config/fleet.toml`, tests, and
generated contracts. The [architecture](../ARCHITECTURE.md) describes durable
ownership; the [testing strategy](how-to-guides/testing-strategy.md) states
the limits of local and live validation. Dated plans, measurements, and
receipts retain their original evidence scope and do not set current runtime
policy or certify a later revision. See the [branch simplification test-pruning
receipt](testing/branch-simplification-test-pruning-receipt.md) for its
reduction evidence.

Regenerate `openapi.yaml` and the TUI HTTP types with `make api-sync`, stream
fixtures with `make stream-sync`, and the configuration environment reference with
`make config-reference`. The [agent guide](../AGENTS.md) lists the matching
verification commands.

See the [Daytona cleanup acceptance receipt](testing/daytona-cleanup-acceptance.md)
for local cleanup regression evidence and pending live acceptance.
