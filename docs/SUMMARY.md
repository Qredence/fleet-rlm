# Table of contents

Current guides, active migration records, and historical baselines are grouped
in [Documentation Home](index.md). Retained broker-backed execution is the sole
supported runtime; Fleet child RLM tools follow the single configuration and are
enabled by the shipped `rlm.recursion_enabled = true` setting. A dated
decision or passing receipt is scoped to its recorded revision and is not
current production certification.

* [Documentation Home](index.md)
* [Architecture](../ARCHITECTURE.md)
* [Testing Strategy](how-to-guides/testing-strategy.md)
* [DSPy RLM and Daytona Integration](how-to-guides/dspy-integration.md)
* [Daytona Snapshot](how-to-guides/daytona-snapshot.md)
* [Evaluation and optimization](how-to-guides/evaluation-optimization.md)
* Historical benchmark runner guides
  * [Evaluation and optimization runners](internal/history/benchmarks/evaluation-optimization-runners.md)
  * [Oolong runner](internal/history/benchmarks/oolong-runner.md)
  * [Retained benchmark material](internal/history/benchmarks/retained-material.md)
* [Terminal UI](how-to-guides/terminal-tui.md)
* [Workspace Memory degradation diagnostics](how-to-guides/workspace-memory-degradation.md)
* Proposals
  * [Book-scale RLM implementation (proposed, unimplemented)](testing/book-scale-rlm-implementation.md)
* Historical baselines
  * [Maintainability freeze](how-to-guides/maintainability-freeze.md)
  * [P41 behavior freeze](reference/behavior-freeze.md)
* [Reference](reference/index.md)
  * [Configuration](reference/configuration.md)
  * [Configuration Environment Reference](reference/configuration-environment.md)
  * [HTTP API](reference/http-api.md)
  * [CLI](reference/cli.md)
  * [Database](reference/database.md)
  * [Source Layout](reference/source-layout.md)
  * [Performance Budget Decision](reference/performance-budget.md)
* [Lakebase Postgres](how-to-guides/lakebase-postgres.md)
* [Local Issue Tracker](agents/issue-tracker.md)
* [Triage Labels](agents/triage-labels.md)
