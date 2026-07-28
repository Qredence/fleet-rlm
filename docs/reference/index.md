# Backend Reference

- [Configuration](configuration.md) — canonical `FLEET_*` settings and profile prerequisites.
- [HTTP API](http-api.md) — supported routes and SSE behavior.
- [CLI](cli.md) — supervised, backend-only, diagnostics, and Artifact commands.
- [Database](database.md) — canonical tables and Alembic ownership.
- [Source layout](source-layout.md) — package and client ownership.
- [Codebase map](codebase-map.md) — module boundaries and dependency direction.

`openapi.yaml` is authoritative for HTTP shapes; generated TUI HTTP types are
checked alongside it by `make api-check`.

For long-form, generated repo knowledge (a Qoder wiki mirror), see the
[Repo Wiki](../wiki/README.md).
