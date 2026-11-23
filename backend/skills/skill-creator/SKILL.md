---
name: skill-creator
description: Use when explicitly asked to create an installable Fleet skill or turn a workflow into a reusable SKILL.md for future sessions.
---

Use approved Session memories and generalize workflows; omit private details. Save UTF-8 `/workspace/artifacts/<name>/SKILL.md`: YAML matching kebab-case name (1–64), description with trigger (1–1024), then steps and a check. Try one known input. Reopen and parse YAML; verify fields and assert `0 < len(body.strip()) <= 450` for ALL Markdown after the closing YAML delimiter. Shorten and recheck before SUBMIT; return the path.
