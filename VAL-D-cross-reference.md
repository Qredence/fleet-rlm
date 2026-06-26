# VAL-D Cross-Reference Table

This document maps each boundary rule from `architecture.md` §2.7 to the corresponding entries in `docs/reference/codebase-map.md` and the implementation in `scripts/check_codebase_tree.py`.

| Rule ID | Architecture Reference | Codebase-Map Entry | Script Implementation |
|---------|----------------------|-------------------|----------------------|
| VAL-D-008 | §2.7: runtime/ may not import from api.routers | `runtime/` package table: "Off-Limits Imports: api.routers" | `check_backend_runtime_imports()` function, lines ~200-215 |
| VAL-D-009 | §2.7: quality/ may not import from api/ business logic | `quality/` package table: "Off-Limits Imports: api.routers, api.runtime_services" | `check_backend_quality_imports()` function, lines ~220-240 |
| VAL-D-010 | §2.7: quality/ may import from runtime/integrations | `quality/` package table: "Allowed Importers: runtime/, integrations/" | Allowed by default (not in violation list) |
| VAL-D-011 | §2.7: frontend/features may not import from src/fleet_rlm | `features/` package table: "Off-Limits Imports: src/fleet_rlm" | `check_frontend_features_imports()` function, lines ~245-265 |
| VAL-D-012 | §2.7: frontend/features may import via lib/rlm-api | `features/` package table: "Allowed Importers: lib/rlm-api" | Allowed by default (not in violation list) |
| VAL-D-014 | §2.7: codebase-map documents all off-limits imports | All package tables have "Off-Limits Imports" column | N/A (documentation requirement) |
| VAL-D-015 | §2.7: checks must be deterministic | N/A (implementation requirement) | All `for` loops use `sorted()` on file lists |
| VAL-D-017 | §2.7: generated artifacts exempt from checks | N/A (implementation requirement) | `is_exempt_path()` function, lines ~50-70 |
| VAL-D-018 | §2.7: all three sources must align | This cross-reference table | All rules implemented and tested |

## Verification Summary

All boundary rules from architecture.md §2.7 are:
1. Documented in docs/reference/codebase-map.md with explicit "Off-Limits Imports" columns
2. Implemented in scripts/check_codebase_tree.py with corresponding check functions
3. Verified through probe tests (temporary violations detected and reverted)
4. Deterministic (sorted file iteration)
5. Exempt generated artifacts (ui/dist, node_modules, tests, scaffold)

## Ambiguities Resolved

1. **`migrations/` location**: Architecture.md §2.1 states migrations lives at repo-root `migrations/`, not `src/fleet_rlm/migrations/`. The codebase-map documents this explicitly.

2. **`quality/eval/` layout**: The codebase-map documents both the current quality files at `src/fleet_rlm/quality/` root AND the future `quality/eval/` subpackage structure from architecture.md §2.6.
