# Harness Quality Standard

The harness is complete when an agent can identify the sole backend package,
run the backend-only gate, distinguish offline from live evidence, regenerate
the root OpenAPI contract, and build a wheel without relying on the frontend.

Required properties:

- current instructions point to `src/fleet_rlm/`;
- only canonical `FLEET_*` runtime configuration is documented;
- `make check` passes without live credentials;
- Alembic can create and check an empty schema;
- live L1/L2 results name the exact git tip;
- historical backend material cannot be mistaken for current documentation.
