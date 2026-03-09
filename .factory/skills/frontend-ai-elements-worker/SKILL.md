---
name: frontend-ai-elements-worker
description: Handles ai-elements migration - audit, install official components, update imports
---

# Frontend AI Elements Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for features related to:
- Auditing custom ai-elements components against official registry
- Installing official ai-elements components
- Updating imports from local to official components
- Aligning custom components with ai-elements patterns

## Work Procedure

1. **Research official components** - Check https://ai-sdk.dev/elements for available components
2. **Audit current state** - List all components in `ai-elements/` and identify which have official equivalents
3. **Install official components** - Use `npx ai-elements add <component>` for each component to migrate
4. **Update imports** - Change `from '@/components/ai-elements/*'` to use installed components
5. **Remove or refactor custom implementations** - Delete migrated files or refactor to wrap official components
6. **Verify functionality** - Start dev server, test chat interface thoroughly
7. **Run quality gate** - `bun run type-check && bun run lint && bun run test:unit && bun run build`

## Example Handoff

```json
{
  "salientSummary": "Migrated 5 components to official ai-elements: message, conversation, task, tool, reasoning. Kept 3 custom components: sandbox, environment-variables, sources. All imports updated. Chat interface tested and working.",
  "whatWasImplemented": "Installed official ai-elements components via CLI. Updated 13 import statements in ChatMessageList.tsx. Removed 5 migrated component files. Aligned remaining custom components with official patterns (data-slot attributes).",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "npx ai-elements add message conversation task tool reasoning", "exitCode": 0, "observation": "Components installed successfully" },
      { "command": "bun run type-check", "exitCode": 0, "observation": "No TypeScript errors" },
      { "command": "bun run lint", "exitCode": 0, "observation": "No lint errors" },
      { "command": "bun run test:unit", "exitCode": 0, "observation": "All tests passed" },
      { "command": "bun run build", "exitCode": 0, "observation": "Build succeeded" }
    ],
    "interactiveChecks": [
      { "action": "Started dev server, navigated to /app/workspace", "observed": "Chat interface loads correctly" },
      { "action": "Sent a test message", "observed": "Message renders correctly with new components" },
      { "action": "Triggered tool execution display", "observed": "Tool component renders correctly" }
    ]
  },
  "tests": {
    "added": []
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Official component doesn't exist for a needed piece of functionality
- Official component has breaking API differences from custom implementation
- Migration would require significant refactoring of consuming code
