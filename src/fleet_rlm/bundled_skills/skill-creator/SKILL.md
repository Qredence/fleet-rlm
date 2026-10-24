---
name: skill-creator
description: Create or improve a Fleet user Skill for a repeatable workflow in the Daytona sandbox. Use when asked to author a Skill, package reusable task guidance, or troubleshoot `.fleet/skills/` synchronization.
---

# Create Fleet Skills

Fleet Skills are Markdown instructions and optional resources synchronized into a Session's `/workspace/skills/` directory. They guide the sandbox agent; they are not host tools, plugins, or a way to add Python packages to Fleet's server. User Skills are authored in `.fleet/skills/<name>/` and override a bundled Skill with the same name.

## Create or update

1. Define the repeatable workflow and when it applies. Keep the description specific so Fleet's Skill selector can distinguish it from other Skills.
2. Create `.fleet/skills/<name>/SKILL.md` with YAML frontmatter containing matching `name` and a non-empty `description`, followed by concise instructions.
3. Put detailed examples or reference material in `references/`; put reusable Python or shell helpers in `scripts/`. Reference these with paths relative to the Skill directory. Keep instructions grounded in Fleet's available sandbox interfaces and the task's actual requirements.
4. Prefer standard-library and snapshot-installed packages. Do not imply that installing a package into an isolated subprocess changes the base snapshot, Fleet server, or host model access. Never put credentials in Skill files.
5. Make the instructions tell the agent how to inspect inputs, produce outputs under `artifacts/`, and verify results appropriate to the task. Do not claim that a helper receipt or successful file write proves correctness.

## Validate in Fleet

- Check the directory and frontmatter name match, and that the description and instructions are non-empty.
- Run `/skills push` to synchronize local Skills, then `/skills` to inspect the Session catalog. `/skills pull` exports runtime-authored Skills to `.fleet/skills/`.
- Try the Skill with a representative task and inspect the resulting execution trace, workspace artifacts, and any errors. Revise claims that do not match observed runtime behavior.
- Keep `.agents/skills/` separate: it is developer guidance and is not a user Skill source.

Fleet validates Skill frontmatter and bounds synchronized resources. A local same-name Skill overrides a bundled Skill. Skill selection is optional; direct-answer Turns do not preload Skills, and failed selection falls back to the catalog.
