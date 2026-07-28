# QoderWiki Knowledge System Reference

**Last verified against commit:** `cfc464c93765e06866279ce998d575d31cefce3a` (`dev-0.7`)

> **Note:** This page is a reference for the **Qoder** tool's knowledge model — meta-docs
> about the generator, not Fleet RLM product documentation.
>
> The original generated page described Qoder's slash-command tools (`list_wiki`,
> `read_wiki`, `edit_wiki`, `write_wiki`, `delete_wiki`, `list_knowledge`,
> `read_knowledge`, `edit_knowledge`, `write_knowledge`, `link_knowledge`,
> `unlink_knowledge`, `delete_knowledge`) and a `droid-wiki/` path-based store with
> `.wiki-meta.json`. **None of those commands or that directory exist in this repository.**
> They are part of Qoder's own IDE/tooling surface and its internal storage model, so the
> fabricated repo-file references and TypeScript pseudocode have been removed from this
> mirror. Accurate, repo-grounded content lives in the other mirrored pages.

## The two artifacts Qoder generated here

Qoder's repo-wiki generation for `fleet-rlm` produced (under `.qoder/repowiki/`):

- **Content pages** (`en/content/`) — prose entry points. Of the five, two are substantive
  and repo-grounded (`FleetRLMBackend.md`, `FleetTerminalClient.md`); the three tool
  meta-pages (`QoderWiki.md`, `QoderWikiReference.md`, `KnowledgeBaseIndex.md`) described
  Qoder itself.
- **Knowledge modules** (`knowledge/en/`) — per-module notes with structured categories:
  - Core categories: `overview`, `architecture_design`, `tech_stack`,
    `coding_conventions`, `unique_setup_and_commands`
  - A `_module.yaml` manifest declaring `module_path`, `scope`, and `title`

## Useful, accurate takeaways for navigating the mirror

- The richest seams are the `architecture_design.md` fragments (module layering and
  dependency direction) and the `coding_conventions.md` fragments (per-module invariants),
  plus the standalone technology pages (build pipeline, configuration system, error
  hierarchy, logging/tracing, TUI theme, uv+pnpm, etc.).
- Knowledge modules map to real source roots: the Backend Core subtree mirrors
  `src/fleet_rlm/`, and sibling subtrees mirror `tools/fleet-tui/`, `scripts/`, `tests/`,
  and `docs/`.
- Many per-module `overview.md` / `tech_stack.md` / `unique_setup_and_commands.md` files
  were generated empty; read the `architecture_design.md` / `coding_conventions.md`
  fragments first.

## Language consistency

- Project documentation language is en-US.
- Module/slug identifiers stay ASCII.

## Related

- [QoderWiki System](QoderWiki.md) — what QoderWiki is and what it produced
- [Knowledge Base Index](KnowledgeBaseIndex.md) — navigation hub
- [Repo Wiki index](../README.md)
