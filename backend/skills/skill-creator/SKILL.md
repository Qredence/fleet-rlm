---
name: skill-creator
description: Create an installable Fleet skill when asked to turn a repeatable workflow into a SKILL.md for future sessions.
approved-session-memories: true
---

Identify the request that should select the skill and what a future agent needs to do differently. When the workflow depends on approved session memories, generalize their relevant instructions and omit private details.

Write UTF-8 to `/workspace/artifacts/<name>/SKILL.md`. Start with YAML between two lines containing only `---`. Use a 1–64 character name matching the directory, with lowercase ASCII letters or digits separated by single hyphens. Write a nonempty description of at most 1,024 characters covering the outcome and trigger. If documenting existing host tools use the canonical space-separated `allowed-tools` field. Tool availability depends on the host.

Follow the frontmatter with self-contained Markdown instructions. Include the decisions, concrete operations and checks needed for this workflow. Fleet installs a single file up to 64 KiB. Reopen it, parse the YAML, check its body and byte size, and try the workflow on a representative input.

Return the artifact path and a brief validation result. Apply the requested memory scope to the handoff as well as the file. Keep excluded memory text and identifiers out of the answer, including omission lists and validation examples. Report a scope check simply as “scope check passed” when it succeeds.
