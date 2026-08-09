# Backend Reference

- [Configuration](configuration.md) — canonical `FLEET_*` settings and profile prerequisites.
- [Runtime profile matrix](profile-matrix.md) — policy-derived providers, token limits, and environment names.
- [HTTP API](http-api.md) — supported routes and SSE behavior.
- [CLI](cli.md) — supervised, backend-only, diagnostics, and Artifact commands.
- [Database](database.md) — canonical tables and Alembic ownership.
- [Source layout](source-layout.md) — package and client ownership.
- [Codebase map](codebase-map.md) — module boundaries and dependency direction.

`openapi.yaml` is authoritative for HTTP shapes; generated TUI HTTP types are
checked alongside it by `make api-check`.
