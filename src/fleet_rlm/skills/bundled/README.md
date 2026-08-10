# Bundled runtime Skills

Fleet ships five runtime Skills:

| Skill | Version | Trigger |
|---|---:|---|
| `dspy-rlm` | 1.0.0 | Analyze, explain, or implement `dspy.RLM` (Recursive LM / REPL; not RAG). |
| `long-context` | 2.0.0 | Large-variable exploration, evidence synthesis, and exact retrieval. |
| `workspace-files` | 1.1.0 | Durable Session files, authorized Attachments, and Artifact creation. |
| `data-analysis` | 1.0.0 | Verified descriptive statistics, trends, and qualified anomalies. |
| `report-builder` | 1.1.0 | Create, save, read back, and verify reports from trusted data. |

The catalog follows three disclosure levels:

1. A Turn starts with bounded system Skill Cards as its discovery surface.
2. With no explicit selection, `load_skill` may progressively load up to four
   advertised Skills. Exact version-pinned selections preload and restrict the
   Turn to that set.
3. `read_skill_resource` returns one explicitly manifested UTF-8 resource only
   after that Skill is loaded.

Bundled Skills contain model-facing product workflows only. Repository maintenance, operator diagnostics, browser development, and Skill authoring guidance belong in the development agent catalog and canonical documentation.

## Catalog conventions

- Every Skill Card advertises bounded `affordances` — the capability families
  the Skill expects (for example `workspace.files`, `artifacts.publish`,
  `fetch_url`, `llm_query`, `llm_query_batched`). Affordances are guidance only;
  they never gate which host tools exist.
- While a Turn has Session Workspace capability, loading a Skill (explicit
  selection preload or progressive `load_skill`) installs its resources at
  `skills/<name>/<path>` in the Session Workspace so generated code can read or
  execute them. Installs are idempotent per Turn, all-or-nothing on failure,
  and resources remain readable through `read_skill_resource` regardless.
