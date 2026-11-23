---
name: skill-creator
description: Author and export a reusable SKILL.md Agent Skill file to /workspace/artifacts when explicitly asked to create a skill.
---

Preserve relevant approved session rules from `memories` and workflow steps from the conversation/workspace. Write `/workspace/artifacts/<name>/SKILL.md` with YAML frontmatter (kebab-case `name`, `description`, optional `allowed_tools` or `mcp_servers`). Parse only the frontmatter between `---` delimiters with `yaml.safe_load`, assert `0 < len(body) <= 450`, and verify the saved file before `SUBMIT`.
