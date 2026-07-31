# Harness Quality Standard

The harness is complete when an agent can identify the sole backend package and
maintained pi-tui client, configure the public runtime, run the repository
gate, distinguish offline from credentialed evidence, regenerate both API
artifacts, and build a release without relying on a graphical frontend.

Required properties:

- current instructions point to `src/fleet_rlm/` and `tools/fleet-tui/`;
- only canonical `FLEET_*` configuration is documented;
- `make check` passes without live credentials and includes the TUI/docs lanes;
- Alembic can upgrade and check an empty configured database;
- `make api-sync` owns OpenAPI and generated TUI HTTP types together;
- Daytona MVP, Attachment/Artifact durability, and Session Workspace live
  evidence name the exact tested git tip and cleanup result;
- release promotion uses CI, local, live, and human evidence for the same SHA;
- historical plans cannot be mistaken for current documentation.
