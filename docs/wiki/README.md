# Repo Wiki (Qoder-generated mirror)

This directory is a **mirror of the Qoder-generated repository wiki** that lives at
`.qoder/repowiki/`. It re-publishes that knowledge base directly under `docs/wiki/` so it
sits alongside the canonical documentation without any extra nesting.

- **Source:** `.qoder/repowiki/` (English), generated on 2026-07-28 from commit
  `0e619c109963233c65b0af51bbf329f0118558fb` (branch `dev-0.7`).
- **Mirror directory names** replace spaces and `&`, `+`, `(`, `)`, `—`, `,` with hyphens
  (kebab-case); file contents are preserved, with YAML frontmatter converted to an HTML
  comment and corrections applied where the wiki drifted from the code.
- **Last verified against commit:** `cfc464c93765e06866279ce998d575d31cefce3a` (`dev-0.7`).

## Relationship to canonical docs

This mirror is a **secondary, generated** knowledge base. The authoritative sources remain:

- `docs/` — architecture, reference, how-to guides (entry point: [../index.md](../index.md))
- `AGENTS.md`, `src/fleet_rlm/AGENTS.md`, `tools/fleet-tui/AGENTS.md`
- generated contracts: `openapi.yaml` and `tools/fleet-tui/src/generated/openapi.ts`
  (owned by `make api-sync` / `make api-check`)

Where this mirror and the canonical docs disagree, **the canonical docs win**. Pages here
carry a "Last verified" stamp and were re-grounded against the current codebase; any claim
that could not be confirmed is marked with a `> **UNVERIFIED**:` admonition rather than
left as confident prose.

## Reading path

1. Start with the substantive entry pages under `content/`:
   - [Fleet RLM Backend](content/FleetRLMBackend.md) — backend subsystem architecture
   - [Fleet Terminal Client](content/FleetTerminalClient.md) — pi-tui TUI architecture
2. Browse the per-module knowledge tree:
   - [Monorepo root](knowledge/fleet-rlm-monorepo/README.md)
   - [Backend Core](knowledge/fleet-rlm-monorepo/backend-core/README.md) (`src/fleet_rlm/`)
   - [Fleet TUI](knowledge/fleet-rlm-monorepo/fleet-tui/README.md), [Scripts](knowledge/fleet-rlm-monorepo/scripts/README.md),
     [Tests](knowledge/fleet-rlm-monorepo/test-suite/README.md), [Docs](knowledge/fleet-rlm-monorepo/project-docs/README.md),
     [Migrations](knowledge/fleet-rlm-monorepo/database-migrations/README.md)
   - [Standalone technology pages](knowledge/) (build pipeline, configuration system,
     error hierarchy, logging/MLflow, TUI theme, uv+pnpm, Ruff, Ty, Pytest, Business Glossary)
3. Use the [Knowledge Base Index](content/KnowledgeBaseIndex.md) as a flat catalog.
4. The Qoder tool meta-pages ([QoderWiki](content/QoderWiki.md),
   [QoderWikiReference](content/QoderWikiReference.md)) describe the generator, not the product.

## Caveats

- Many per-module `overview.md`, `tech_stack.md`, and `unique_setup_and_commands.md` files
  were generated **empty** by Qoder; they are mirrored as-is. The substantive prose lives in
  the `architecture_design.md` / `coding_conventions.md` fragments and the standalone
  technology pages.
- The two substantive content pages were **corrected** during this mirror: see the changelog
  in the verification report and each page's "Last verified" note for what was re-grounded.
