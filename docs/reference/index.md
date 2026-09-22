# Backend Reference

- [Architecture](../../ARCHITECTURE.md) — current component ownership and dependency direction.
- [Configuration](configuration.md) — TOML policy, explicit environment references, and profile prerequisites.
- [Runtime profile matrix](profile-matrix.md) — policy-derived providers, token limits, and environment names.
- [HTTP API](http-api.md) — supported routes and SSE behavior.
- [CLI](cli.md) — supervised, backend-only, diagnostics, and Artifact commands.
- [Database](database.md) — canonical tables and Alembic ownership.
- [Source layout](source-layout.md) — package and client ownership.
- [Performance budget decision](performance-budget.md) — dated Sandbox, Volume, broker, and Run measurements; current policy is owned by TOML.
- [P41 behavior freeze](behavior-freeze.md) — frozen public behaviors, their owners, and the behavior-over-structure guarantee.

`openapi.yaml` is authoritative for HTTP shapes; generated TUI HTTP types are
checked alongside it by `make api-check`.
