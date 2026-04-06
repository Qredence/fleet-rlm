---
name: frontend-refactor-worker
description: Moves frontend files between directories, updates imports, renames symbols, and validates with type-check/lint/test/build.
---

# Frontend Refactor Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for features that involve moving frontend files between directories, updating import paths across the codebase, renaming exported symbols, updating configuration files (vite.config.ts, AGENTS.md), and deleting old directories. All work is in `src/frontend/`.

## Required Skills

None. All verification is done via shell commands (pnpm run type-check, lint, test, build).

## Work Procedure

1. **Read the feature description carefully.** Understand exactly which files to move, where they go, what symbols to rename, and what configuration to update.

2. **Read `.factory/library/architecture.md`** for the target directory structure and dependency direction.

3. **Read each source file** before moving it. Understand its imports and exports.

4. **Perform the file operations:**
   - For file moves: read the source file, create the new file at the target location with updated imports and symbol names, then delete the old file.
   - For import updates: use grep to find ALL consumers, then update each one. Be thorough — a missed import will break type-check.
   - For symbol renames: maintain dual exports (old name + new name) unless the feature description says otherwise.
   - For configuration updates: read the config file first, then make targeted edits.

5. **After each logical group of changes, run validation:**
   - `cd src/frontend && pnpm run type-check` — must exit 0
   - If type-check fails, fix the issue immediately before proceeding.

6. **After all changes for the feature are complete, run full validation:**
   - `cd src/frontend && pnpm run type-check` — must exit 0
   - `cd src/frontend && pnpm run lint:robustness` — must exit 0
   - `cd src/frontend && pnpm run test:unit` — all tests must pass
   - `cd src/frontend && pnpm run build` — must exit 0

7. **Verify completeness:**
   - For file moves: confirm old files are deleted (`ls` the old path should fail)
   - For import updates: `rg` the old import path should return zero matches
   - For symbol renames: check that new names are used in the expected places

8. **Commit the changes** with a descriptive message.

### Critical Rules

- **Never leave broken type-check.** If a move breaks imports, fix all consumers before moving on.
- **Use `rg` (ripgrep) for codebase-wide searches**, not `grep`. Search `src/frontend/src/` to find all import consumers.
- **When moving a file, update ALL internal imports** within that file to reflect its new location. Relative imports (`./`, `../`) need recalculation. Absolute imports (`@/...`) usually don't change.
- **Preserve dual-name exports** (e.g., `export { ShellHeader, ShellHeader as LayoutHeader }`) unless explicitly told to remove the old name.
- **When updating vite.config.ts**, be precise — the lint boundary rules use glob patterns that must match the new directory structure exactly.
- **Do not modify files outside `src/frontend/`** unless the feature description explicitly says to.
- **Do not modify generated files** (`routeTree.gen.ts`, `generated/openapi.ts`, `dist/`).
- **Run `pnpm run format` before committing** to ensure consistent formatting.

## Example Handoff

```json
{
  "salientSummary": "Moved 5 leaf-node files (panel-meta, route-outlet, login-dialog, mobile-tab-bar, route-sync) from screens/shell/ and app/shell/ into features/layout/, inlined their implementations replacing the re-export wrappers, updated 2 intra-shell imports to use local paths, and ran full validation (type-check, lint, 296 tests pass, build succeeds).",
  "whatWasImplemented": "Migrated panel-meta.ts, shell-route-outlet.tsx, login-dialog.tsx, mobile-tab-bar.tsx, and route-sync.tsx into features/layout/ with actual implementation (not re-exports). Updated shell-header.tsx and shell-sidepanel.tsx to import panel-meta from @/features/layout/panel-meta instead of @/screens/shell/shell-panel-meta. Deleted old files from screens/shell/ and app/shell/.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "cd src/frontend && pnpm run type-check", "exitCode": 0, "observation": "No type errors" },
      { "command": "cd src/frontend && pnpm run lint:robustness", "exitCode": 0, "observation": "0 warnings, 0 errors" },
      { "command": "cd src/frontend && pnpm run test:unit", "exitCode": 0, "observation": "296 tests passed in 59 files" },
      { "command": "cd src/frontend && pnpm run build", "exitCode": 0, "observation": "Built successfully in 12s" },
      { "command": "rg '@/screens/shell/shell-panel-meta|@/screens/shell/shell-route-outlet|@/app/shell/login-dialog|@/app/shell/mobile-tab-bar|@/app/shell/route-sync' src/frontend/src/", "exitCode": 1, "observation": "Zero matches — no stale imports remain" },
      { "command": "ls src/frontend/src/screens/shell/shell-panel-meta.ts", "exitCode": 1, "observation": "File deleted" },
      { "command": "ls src/frontend/src/app/shell/login-dialog.tsx", "exitCode": 1, "observation": "File deleted" }
    ],
    "interactiveChecks": []
  },
  "tests": {
    "added": []
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- A file move creates a circular dependency that cannot be resolved
- An import boundary rule in vite.config.ts is blocking a legitimate import and needs architectural guidance
- A test fails after migration and the root cause is not obvious (may indicate a behavioral change)
- The feature description is ambiguous about which files to move or how to rename symbols
