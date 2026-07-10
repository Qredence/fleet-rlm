# Domain documentation

Fleet RLM uses a multi-context glossary layout.

## Reading rules

1. Read `CONTEXT-MAP.md` and the context relevant to the work.
2. Read system-wide ADRs under `docs/adr/` that touch the area.
3. Use glossary terms consistently in issues, plans, tests, and review findings.
4. Surface conflicts with an existing ADR instead of silently overriding it.
5. Keep `CONTEXT.md` files free of implementation plans and code-level workflow detail.

## Contexts

- `CONTEXT.md` contains concepts shared across the whole product.
- `src/fleet_rlm/CONTEXT.md` contains backend runtime language.
- `src/frontend/CONTEXT.md` contains frontend interaction language.

Create context-local ADR directories only when a genuinely context-scoped, hard-to-reverse trade-off requires one. System-wide decisions remain in `docs/adr/`.
