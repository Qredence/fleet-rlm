# Validation Contract: Legacy Code Cleanup

**Milestone:** Legacy Code Cleanup
**Mission:** Frontend Architecture Optimization
**Created:** 2026-03-09

## Overview

This validation contract defines the assertions that must pass after removing the `@/` dead code directory and scanning for unused exports.

---

## Assertions

### VAL-CLEANUP-001: `@/` Directory Removed

**Title:** The `@/` directory no longer exists in the frontend source tree

**Behavioral Description:**
The `src/frontend/@/` directory containing duplicate UI components must be completely removed. This directory was identified as dead code - untracked in git, containing broken imports, and not referenced by any path alias configuration.

**Pass Criteria:**
- The directory `src/frontend/@/` does not exist on the filesystem
- No files remain under `src/frontend/@/components/ui/` (the 25 duplicate component files)

**Evidence Requirements:**
- Command `ls src/frontend/@/` returns "No such file or directory"
- Command `find src/frontend -type d -name "@"` returns no results

---

### VAL-CLEANUP-002: No Broken Imports from Removed Code

**Title:** No source files import from the removed `@/` directory

**Behavioral Description:**
After removal, no remaining source files should have imports that resolve to the deleted `@/` directory. All `@/components/ui/...` imports must resolve via the tsconfig path alias to `./src/components/ui/...` - not to the removed `@/` physical directory.

**Pass Criteria:**
- Grep for imports from `@/components/ui` in `@/` directory files returns no results (since directory is removed)
- All existing `@/components/ui/...` imports in `src/**/*.tsx` files resolve correctly via path alias
- No TypeScript errors about missing modules

**Evidence Requirements:**
- TypeScript compiler reports no module resolution errors
- `bun run type-check` exits with code 0
- No runtime import errors in browser console

---

### VAL-CLEANUP-003: Build Succeeds

**Title:** Production build completes without errors

**Behavioral Description:**
The Vite build process must complete successfully after cleanup. No build-time errors should occur from missing imports or removed code.

**Pass Criteria:**
- `bun run build` exits with code 0
- Build output is generated in `dist/` directory
- No build warnings about missing modules

**Evidence Requirements:**
- Build command output shows successful completion
- `dist/index.html` exists
- Build bundle contains expected entry points

---

### VAL-CLEANUP-004: Type Checking Passes

**Title:** TypeScript type checking passes with strict settings

**Behavioral Description:**
All TypeScript files must pass type checking with the project's strict configuration. The removed code should not cause any type errors in remaining files.

**Pass Criteria:**
- `bun run type-check` exits with code 0
- No TypeScript errors in compiler output
- `noUnusedLocals` and `noUnusedParameters` do not flag false positives

**Evidence Requirements:**
- TypeScript compiler output shows "0 errors"
- No errors related to missing type declarations
- All `@/` path alias imports resolve correctly

---

### VAL-CLEANUP-005: All Unit Tests Pass

**Title:** Unit test suite passes completely

**Behavioral Description:**
The Vitest unit test suite must pass after cleanup. No tests should fail due to missing components or broken imports.

**Pass Criteria:**
- `bun run test:unit` exits with code 0
- All tests pass (no failures)
- Test coverage is not significantly impacted

**Evidence Requirements:**
- Vitest output shows all tests passing
- No test failures related to missing modules
- Test count remains consistent with pre-cleanup baseline

---

### VAL-CLEANUP-006: No Orphaned Exports

**Title:** No unused exports remain in the codebase

**Behavioral Description:**
After the primary cleanup, scan for and identify any unused exports that can be safely removed. TypeScript's `noUnusedLocals` and `noUnusedParameters` settings help identify these.

**Pass Criteria:**
- TypeScript reports no unused local variables or parameters
- ESLint (if configured for unused exports) reports no issues
- Manual review confirms no obvious dead exports

**Evidence Requirements:**
- `bun run type-check` shows no unused variable warnings
- `bun run lint` shows no unused export errors
- Review of potentially unused exports documented

---

### VAL-CLEANUP-007: Git Status Clean

**Title:** Git working directory reflects only intentional changes

**Behavioral Description:**
After cleanup, git status should show the removal of `@/` directory as untracked files being removed, with no unexpected side effects.

**Pass Criteria:**
- `git status` shows `@/` directory files are no longer listed as untracked
- No unexpected file modifications from the cleanup operation
- Changes are limited to the removal of the `@/` directory

**Evidence Requirements:**
- `git status --porcelain` no longer shows `?? src/frontend/@/` entries
- No modified files (` M`) unless intentionally changed
- Clean working state for the cleanup operation

---

## Validation Commands Summary

```bash
# 1. Verify @/ directory removed
ls src/frontend/@/ 2>&1 | grep -q "No such file"

# 2. Type check
cd src/frontend && bun run type-check

# 3. Build
cd src/frontend && bun run build

# 4. Unit tests
cd src/frontend && bun run test:unit

# 5. Lint check
cd src/frontend && bun run lint

# 6. Git status verification
git status --porcelain | grep "^?? src/frontend/@/"
```

---

## Notes

- The `@/` directory was untracked (`??`) in git, indicating it was never committed
- The directory contained 25 duplicate UI component files
- Imports in `@/` files used `@/components/ui/...` which would resolve via path alias to `src/components/ui/...` - making them self-referential and broken
- No source files outside `@/` import from the physical `@/` directory
