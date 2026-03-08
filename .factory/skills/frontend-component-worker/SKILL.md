---
name: frontend-component-worker
description: Handles component reorganization - moving components, creating barrel exports
---

# Frontend Component Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for features related to:
- Moving components between directories
- Creating barrel exports (index.ts)
- Reorganizing component directory structure
- Creating component manifests

## Work Procedure

1. **Identify affected components** - List all components that need to move or be exported
2. **Find all import references** - Use grep to find all files importing the affected components
3. **Make changes atomically** - Move files and update imports in the same commit
4. **Create barrel exports** - Add index.ts with named exports for each directory
5. **Verify no broken imports** - `bun run type-check` should catch any missed imports
6. **Run quality gate** - `bun run type-check && bun run lint && bun run test:unit && bun run build`

## Example Handoff

```json
{
  "salientSummary": "Moved 3 AI-specific components (queue, suggestion-chip, streamdown) from ui/ to ai-elements/. Created barrel exports for all 6 component directories. Updated 8 import statements across the codebase.",
  "whatWasImplemented": "Component moves: ui/queue.tsx → ai-elements/queue.tsx, ui/suggestion-chip.tsx → ai-elements/suggestion-chip.tsx, ui/streamdown.tsx → ai-elements/streamdown.tsx. Created index.ts in: ui/, shared/, ai-elements/, domain/, chat/, chat/input/. All imports updated to use new paths.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "bun run type-check", "exitCode": 0, "observation": "No TypeScript errors - all imports resolved" },
      { "command": "bun run lint", "exitCode": 0, "observation": "No lint errors" },
      { "command": "bun run test:unit", "exitCode": 0, "observation": "All tests passed" },
      { "command": "bun run build", "exitCode": 0, "observation": "Build succeeded, tree-shaking works" }
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

- Component has circular dependencies that prevent clean reorganization
- Moving a component would require changes to external packages or dependencies
- Uncertainty about which directory a component belongs in
