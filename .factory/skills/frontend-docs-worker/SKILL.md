---
name: frontend-docs-worker
description: Handles architecture documentation - AGENTS.md, library files, manifests
---

# Frontend Docs Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for features related to:
- Updating AGENTS.md with finalized conventions
- Creating architecture documentation
- Documenting styling system and CSS variables
- Documenting ai-elements usage patterns
- Creating component manifests

## Work Procedure

1. **Review completed work** - Understand all changes made during the mission
2. **Read existing documentation** - Check current AGENTS.md and any existing docs
3. **Create/update documentation** - Write clear, actionable documentation
4. **Verify accuracy** - Ensure documentation matches actual code state
5. **Run quality gate** - Documentation should not break builds

## Example Handoff

```json
{
  "salientSummary": "Updated AGENTS.md with finalized styling conventions, ai-elements usage patterns, and import conventions. Created 3 library documents: styling-system.md, ai-elements-guide.md, component-manifest.md.",
  "whatWasImplemented": "AGENTS.md updates: Added styling section, ai-elements section, import conventions. New files: .factory/library/styling-system.md (CSS variables, utilities), .factory/library/ai-elements-guide.md (usage patterns), .factory/library/component-manifest.md (directory structure).",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "bun run type-check", "exitCode": 0, "observation": "No TypeScript errors" },
      { "command": "bun run build", "exitCode": 0, "observation": "Build succeeded" }
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

- Documentation reveals inconsistencies in the codebase that need resolution
- Uncertainty about what should be documented vs what's implementation detail
