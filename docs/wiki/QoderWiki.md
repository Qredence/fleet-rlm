# QoderWiki System

**Last verified against commit:** `cfc464c93765e06866279ce998d575d31cefce3a` (`dev-0.7`)

> **Note:** This page documents the **Qoder** tool that generated the `.qoder/repowiki/`
> knowledge base — it is *meta-documentation about the documentation generator*, not Fleet
> RLM product documentation. Treat it as context for how this wiki mirror was produced.
>
> The original generated page referenced a `droid-wiki/` tree (`.wiki-meta.json`, `overview/`,
> `systems/`, `features/`, …) and slash-command tools (`read_wiki`, `edit_wiki`, …). **That
> tree does not exist in this repository.** Those sections described the Qoder tool's own
> internal storage model rather than any committed Fleet RLM artifact, and have been removed
> from this mirror to avoid confusion. The actual generated output for this repo is the
> knowledge tree under `.qoder/repowiki/`, mirrored to `docs/repowiki/`.

## What QoderWiki produced for this repo

Qoder's repo-wiki feature generated two things from the `fleet-rlm` checkout:

1. **Content pages** (`.qoder/repowiki/en/content/`) — high-level entry points:
   - `FleetRLMBackend.md` — backend subsystem architecture (mirrored, corrected)
   - `FleetTerminalClient.md` — TUI application architecture (mirrored, corrected)
   - `QoderWiki.md` / `QoderWikiReference.md` / `KnowledgeBaseIndex.md` — tool meta-docs
     (this page and its siblings)
2. **A knowledge tree** (`.qoder/repowiki/knowledge/en/`) — per-module notes organized as a
   monorepo root, a `Fleet RLM Backend Core` subtree, sibling subtrees (TUI, scripts, tests,
   docs), and standalone technology pages. Each module directory holds `overview.md`,
   `architecture_design.md`, `tech_stack.md`, `coding_conventions.md`,
   `unique_setup_and_commands.md`, and a `_module.yaml` manifest.

The substantive prose lives in the module `architecture_design.md` / `coding_conventions.md`
fragments and the standalone technology pages. Most per-module `overview.md`,
`tech_stack.md`, and `unique_setup_and_commands.md` files were generated **empty**; the
manifests (`_module.yaml`) only declare each module's `scope`/`module_path`.

## Generation metadata

From `.qoder/repowiki/en/meta/repowiki-metadata.json`:

- Repository: `fleet-rlm`, branch `dev-0.7`
- Generation completed: 2026-07-28 (Qoder), source snapshot commit
  `0e619c109963233c65b0af51bbf329f0118558fb`
- Status: `wiki_generation_completed`

The repository has advanced since that snapshot; this mirror carries the corrections needed
to re-ground the content on the current `dev-0.7` HEAD (see the per-page "Last verified"
stamps and `docs/wiki/README.md`).

## Relationship to canonical docs

- **Canonical**: `docs/` (architecture, reference, how-to), `AGENTS.md`,
  `src/fleet_rlm/AGENTS.md`, `tools/fleet-tui/AGENTS.md`, generated `openapi.yaml` +
  `tools/fleet-tui/src/generated/openapi.ts`.
- **This mirror**: a secondary, generated long-form knowledge base under `docs/wiki/`.
  When they disagree, the canonical docs win.

## Related

- [QoderWiki Reference](QoderWikiReference.md) — tool reference
- [Knowledge Base Index](KnowledgeBaseIndex.md) — navigation hub
- [Repo Wiki index](../README.md) — how this mirror is organized
