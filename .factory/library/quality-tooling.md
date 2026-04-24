# Quality Tooling Quirks

Factual knowledge about the project's validation scripts discovered during api-rewiring milestone work.

## check_agents_md_freshness.py — Node.js Frontend Path Resolution

`scripts/check_agents_md_freshness.py` discovers AGENTS.md files by walking the repo and resolving each file's project root to find the corresponding package manager manifest (pyproject.toml, package.json, etc.). For the Node.js frontend at `src/frontend/AGENTS.md`, the script uses a subdirectory-candidate strategy:

1. Detects `package.json` in the AGENTS.md's directory or parent directories.
2. Tries subdirectory candidates `src`, `src/components`, `src/lib`, `src/features` when resolving the frontend root.
3. This logic was added in commit `8916178f` to fix a prior breakage where the freshness check failed on the frontend AGENTS.md.

If you encounter freshness-check failures for `src/frontend/AGENTS.md`, verify that the `package.json` detection branch in the script (around lines 167-171 of the version from that commit) is running correctly.

## check_docs_quality.py — Backtick Path References Not Validated

`scripts/check_docs_quality.py` validates only markdown hyperlinks (`[text](url)` syntax). It does **not** validate backtick-wrapped path references in documentation prose (e.g., `` `src/fleet_rlm/agent_host/` ``).

Consequence: stale path references in prose — like deleted directory paths wrapped in backticks — will not be caught by this validator or by `check_agents_md_freshness.py` (which only checks AGENTS.md files, not docs/*.md).
