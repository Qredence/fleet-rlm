---
name: skill-creator
description: Synthesize reusable Agent Skills (SKILL.md) from session history, approved memories, and workspace workflows.
---

Preserve relevant approved session rules from `memories` in the draft. Use the visible conversation and workspace files for workflow steps. Write `/workspace/artifacts/<name>/SKILL.md` with YAML frontmatter (kebab-case `name`, `description`, optional `allowed_tools` or `mcp_servers`). Parse it with `yaml.safe_load`, assert `0 < len(body) <= 450`, and verify the saved file preserves the approved rules before `SUBMIT`.
