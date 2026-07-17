# Bundled runtime Skills

Fleet ships two runtime Skills:

| Skill | Version | Trigger |
|---|---:|---|
| `long-context` | 2.0.0 | Large-variable exploration, evidence synthesis, and exact retrieval. |
| `workspace-files` | 1.0.0 | Durable Session files, authorized Attachments, and Artifact creation. |

The catalog follows three disclosure levels:

1. A Turn starts with authorized visible names and descriptions.
2. `load_skill` returns the selected `SKILL.md` only when invoked.
3. `read_skill_resource` returns an allowed `scripts/`, `references/`, or `assets/` file only after that Skill is loaded.

Bundled Skills contain model-facing product workflows only. Repository maintenance, operator diagnostics, browser development, and Skill authoring guidance belong in the development agent catalog and canonical documentation.
