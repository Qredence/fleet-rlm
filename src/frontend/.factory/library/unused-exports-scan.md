# Unused Exports Scan Results

**Date:** 2026-03-09
**Feature:** scan-unused-exports
**Commit:** 4cb7e42

## Summary

Scanned the frontend codebase for unused exports and dead code using TypeScript's `noUnusedLocals` and `noUnusedParameters` checks plus ESLint `@typescript-eslint/no-unused-vars` rule.

## Fixed Issues

### 1. Unused Import in ArtifactREPL.tsx
- **File:** `src/components/domain/artifacts/ArtifactREPL.tsx`
- **Issue:** `mapToolState` was imported but never used
- **Fix:** Removed the unused import
- **Note:** The function is used in `ChatMessageList.tsx`, so it wasn't removed from the source

### 2. Unused ESLint Disable Directives in code-block.tsx
- **File:** `src/components/ai-elements/code-block.tsx`
- **Issue:** `eslint-disable-next-line no-bitwise` directives on lines 34 and 37 were unused
- **Reason:** The ESLint config doesn't have `no-bitwise` rule enabled
- **Fix:** Removed the unused directives, kept biome-ignore and oxlint-disable comments

### 3. Unused ESLint Disable Directive in prompt-input.tsx
- **File:** `src/components/ai-elements/prompt-input.tsx`
- **Issue:** `eslint-disable-next-line react-hooks/exhaustive-deps` directive was unused
- **Fix:** Removed the directive

## Verification

- `bun run lint` passes with 0 errors
- No unused variable warnings from ESLint
- TypeScript `noUnusedLocals` and `noUnusedParameters` checks are enabled in tsconfig.app.json

## Pre-Existing Issues (Outside Scope)

The following issues were identified but are NOT related to unused exports:

### TypeScript Type Errors (18 errors)
These are type mismatches and missing exports, not unused code:
- Missing exports from `input-group`: `InputGroupAddon`, `InputGroupButton`, `InputGroupTextarea`, `InputGroupText`
- Type errors in `jsx-preview.tsx`, `speech-input.tsx`, `stack-trace.tsx`, `terminal.tsx`, `reasoning.tsx`, `ArtifactREPL.tsx`

### Test Failures (9 failed tests)
Tests appear to be affected by recent changes:
- `ArtifactTimeline.test.tsx` - rendering issues

### Lint Warnings (19 warnings)
All are `react-refresh/only-export-components` warnings about Fast Refresh optimization:
- These suggest refactoring for better hot module replacement
- Not dead code - just architectural suggestions

## Tools Used

- TypeScript: `noUnusedLocals: true`, `noUnusedParameters: true` in tsconfig.app.json
- ESLint: `@typescript-eslint/no-unused-vars` rule with `argsIgnorePattern: "^_"`, `varsIgnorePattern: "^_"`
- Manual grep for "is declared but never" patterns
