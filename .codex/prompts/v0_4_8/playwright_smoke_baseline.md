# Playwright Smoke Baseline (Phase 0 and Later Phases)

Use the `qa_playwright` role for browser validation.

## Preconditions
- Verify `npx` exists
- Use wrapper: `/Users/zocho/.codex/skills/playwright/scripts/playwright_cli.sh`
- Store artifacts under `output/playwright/phase-XX/`

## Wrapper Compatibility Fallback
If the wrapper launches `playwright-mcp` server help instead of the documented command loop (`open`, `snapshot`, `click`, etc.), record the mismatch in the phase log and use a deterministic fallback for smoke evidence:

- `npx playwright screenshot <url> <file>`
- `npx playwright open <url>` (manual observation, if needed)

Prefer the wrapper when compatible, but do not block the phase on wrapper/tooling drift if browser evidence can be captured safely.

## Deterministic Steps
1. Start the app (repo-standard entrypoint, usually `uv run fleet web`)
2. Open target URL with wrapper
3. `snapshot`
4. Basic navigation (home -> settings -> artifact/canvas entry point if reachable)
5. Re-snapshot after each navigation or major DOM change
6. Capture screenshots for evidence
7. Log exact commands and artifact paths in phase outcome log

## Negative Routing Examples
- Do not use `frontend_impl` as a substitute for browser validation.
- Do not rely on stale element refs without re-snapshotting.
- Do not store screenshots outside `output/playwright/phase-XX/` unless explicitly required.
