# Daytona facade dossier

## Phase 4 — Daytona facade split

- **Order:** `4`
- **Status:** `complete`
- **Track:** `Daytona`
- **Summary:** Concentrate Daytona ownership in a canonical facade while preserving compatibility imports.
- **Commit:** `a2c7e374`

### Goal and stable interfaces

`src/fleet_rlm/daytona/` owns interpreter, sandbox, volume, files, workspace,
session state, diagnostics, and pool behavior. Existing
`integrations.daytona` paths remain compatibility adapters while callers migrate.
Sandbox snapshots, `/home/daytona/memory`, volume naming, and session semantics
remain compatible.

### Non-goals

- Rewrite the Daytona SDK or sandbox lifecycle in one phase.
- Remove compatibility imports before all callers migrate.
- Require live Daytona credentials for imports or default tests.

### Acceptance criteria

- [x] Canonical facade imports are credential-free and side-effect-free.
- [x] Compatibility imports preserve legacy callers.
- [x] Legacy runtime and direct RLM use the same Daytona substrate.
- [x] Default tests require no live Daytona SDK session.

### Validation

```bash
uv run pytest tests/unit/daytona/ tests/unit/integrations/
```
