---
name: frontend-styling-worker
description: Handles CSS/Tailwind styling consolidation - inline styles, CSS variables, utilities
---

# Frontend Styling Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Use for features related to:
- Replacing inline styles with CSS variables or Tailwind classes
- Creating or updating Tailwind utilities
- Modifying theme.css or tailwind.css
- Cleaning up styling files (fonts.css, etc.)

## Work Procedure

1. **Analyze the current state** - Grep for patterns like `style={{`, hardcoded values, arbitrary Tailwind values
2. **Plan the changes** - Identify which CSS variables already exist in theme.css, which need to be added
3. **Make changes incrementally** - One file or pattern at a time
4. **Run verification after each change** - `bun run type-check && bun run lint`
5. **Test visually** - Start dev server and verify UI still renders correctly
6. **Run full quality gate** - `bun run type-check && bun run lint && bun run test:unit && bun run build`

## Example Handoff

```json
{
  "salientSummary": "Replaced 15 inline style instances with CSS variables in SkillMarkdown.tsx, animated-tabs.tsx, and LogoutPage.tsx. Added --line-height-relaxed and --touch-target-size variables to theme.css. All type-check, lint, and tests pass.",
  "whatWasImplemented": "Inline style consolidation across 3 component files. Added 2 new CSS variables. Removed hardcoded numeric values (lineHeight: '1.6', height: '2rem', borderRadius: '50%').",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "bun run type-check", "exitCode": 0, "observation": "No TypeScript errors" },
      { "command": "bun run lint", "exitCode": 0, "observation": "No lint errors" },
      { "command": "bun run test:unit", "exitCode": 0, "observation": "All 42 tests passed" },
      { "command": "bun run build", "exitCode": 0, "observation": "Build succeeded" }
    ],
    "interactiveChecks": [
      { "action": "Started dev server, navigated to /app/workspace", "observed": "Chat interface renders correctly with updated styles" }
    ]
  },
  "tests": {
    "added": []
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- CSS variable needed that doesn't exist and would require design decisions
- Styling change breaks existing functionality unexpectedly
- Pattern requires changes to shadcn/ui components (those are managed by CLI)
