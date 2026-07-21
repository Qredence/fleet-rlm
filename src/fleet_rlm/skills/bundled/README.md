# Bundled runtime Skills

Fleet ships five runtime Skills:

| Skill | Version | Trigger |
|---|---:|---|
| `dspy-rlm` | 1.0.0 | Analyze, explain, or implement `dspy.RLM` (Recursive LM / REPL; not RAG). |
| `long-context` | 2.0.0 | Large-variable exploration, evidence synthesis, and exact retrieval. |
| `workspace-files` | 1.0.0 | Durable Session files, authorized Attachments, and Artifact creation. |
| `data-analysis` | 1.0.0 | Verified descriptive statistics, trends, and qualified anomalies. |
| `report-builder` | 1.0.0 | Create, save, read back, and verify reports from trusted data. |

The catalog follows three disclosure levels:

1. A Turn starts with bounded system Skill Cards.
2. `load_skill` returns the selected `SKILL.md` only when invoked.
3. `read_skill_resource` returns one explicitly manifested UTF-8 resource only
   after that Skill is loaded.

Bundled Skills contain model-facing product workflows only. Repository maintenance, operator diagnostics, browser development, and Skill authoring guidance belong in the development agent catalog and canonical documentation.
