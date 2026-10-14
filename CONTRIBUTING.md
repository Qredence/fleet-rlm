# Contributing to Fleet RLM Lite

Start with the [README](README.md), [architecture](ARCHITECTURE.md), and
[repository guidelines](AGENTS.md). The application uses Python 3.11–3.13 and
uv; no Node toolchain is required.

## Setup and checks

```bash
uv sync --dev
uv run fleet --help
make check
```

For focused changes, run the relevant tests with `uv run pytest -q <path>` and
the relevant Make targets. `make check` is the repository's non-live quality
gate. It must not require Daytona, model-provider, or production credentials.

Running the application itself requires PostgreSQL, Daytona API key,
organization ID, and Volume ID, plus model-provider configuration. See
[Configuration](docs/reference/configuration.md). Tables are created by the
application at startup; there is no migration command. Use only disposable or
authorized external resources for integration work.

## Making changes

1. Read the owning code and its tests. Current runtime owners are documented in
   [Architecture](ARCHITECTURE.md).
2. Keep changes focused and add regression tests for behavior changes.
3. Update the docs and API contract when public behavior changes.
4. Run focused tests, `make check` as appropriate, and `git diff --check`.
5. Preserve `.agents/skills/` as developer skills; user Skills live under
   `.fleet/skills/`.

## Pull requests

Use a conventional prefix such as `feat:`, `fix:`, `test:`, `docs:`, or
`chore:`. Summarize behavior, validation, and any external integration that
remains unverified. Never include credentials. Report security issues through
[SECURITY.md](SECURITY.md), not a public issue.
