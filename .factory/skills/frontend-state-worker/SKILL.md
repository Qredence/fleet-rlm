---
name: frontend-state-worker
description: Handles state/context refactoring - separating contexts, cleaning hook exports
---

# Frontend State Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for features related to:
- Creating contexts/ directory structure
- Moving Provider/Context files
- Separating hook exports from provider exports
- Creating barrel exports for hooks and contexts

## Work Procedure

1. **Analyze current structure** - List all files in hooks/ and identify which are providers vs hooks
2. **Plan the reorganization** - Document which files move where
3. **Create contexts/ directory** - `mkdir -p src/contexts`
4. **Move files atomically** - Move provider/context files, then update all imports
5. **Update exports** - Ensure hooks/ exports only hooks, contexts/ exports providers
6. **Create barrel exports** - index.ts in both hooks/ and contexts/
7. **Verify all imports work** - `bun run type-check` should catch any issues
8. **Run quality gate** - `bun run type-check && bun run lint && bun run test:unit && bun run build`

## Example Handoff

```json
{
  "salientSummary": "Created src/contexts/ directory and moved all Provider/Context files from hooks/. AuthProvider.tsx, NavigationProvider.tsx, auth-context.ts, navigation-context.ts now in contexts/. Updated 12 import statements across the codebase. Created barrel exports for both directories.",
  "whatWasImplemented": "Directory creation: src/contexts/. File moves: 4 files from hooks/ to contexts/. Import updates: 12 files updated. Barrel exports: src/contexts/index.ts, src/hooks/index.ts. Cleaned useAuth.ts to export only useAuth hook.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "bun run type-check", "exitCode": 0, "observation": "No TypeScript errors" },
      { "command": "bun run lint", "exitCode": 0, "observation": "No lint errors" },
      { "command": "bun run test:unit", "exitCode": 0, "observation": "All tests passed" },
      { "command": "bun run build", "exitCode": 0, "observation": "Build succeeded" }
    ],
    "interactiveChecks": [
      { "action": "Started dev server, tested login flow", "observed": "Auth context works correctly" },
      { "action": "Tested navigation", "observed": "Navigation context works correctly" }
    ]
  },
  "tests": {
    "added": []
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Moving context files reveals unexpected dependencies
- Reorganization would require changes to how contexts are consumed throughout the app
- Circular dependency issues arise
