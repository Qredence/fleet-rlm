---
name: backend-refactor
description: Simplifies fleet-rlm backend by rewriting/deleting modules and updating tests
---

# Backend Refactor Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Any feature that involves:
- Rewriting Python modules in `src/fleet_rlm/runtime/` or `src/fleet_rlm/api/`
- Deleting directories (`agent_host/`, `worker/`)
- Creating new simplified modules
- Updating or creating unit tests
- Removing dangling imports

## Required Skills

None.

## Work Procedure

1. **Read the feature description** carefully. Understand exactly what files to create, modify, or delete.

2. **Read existing code first.** Before modifying any file, read it to understand current patterns, imports, and conventions. Read `.factory/library/architecture.md` and `.factory/library/environment.md` for context.

3. **Write tests first** (TDD). For new modules, write failing tests in `tests/unit/runtime/` before implementation. For deletions, skip this step.

4. **Implement the change.** Write clean, minimal code following existing conventions:
   - Type hints on all signatures
   - No import-time side effects
   - Follow ruff formatting
   - DSPy 3.1.3: tools are plain callables or `dspy.Tool(func)`, NOT `@dspy.tool` decorator
   
5. **For deletion features:** Delete the directories/files, then grep for dangling imports and fix them. Remove corresponding test directories.

6. **Run validation:**
   ```bash
   make format
   make lint
   make typecheck
   make test
   ```
   Fix all failures before completing.

7. **Verify no dangling imports** for any deleted modules:
   ```bash
   rg "from fleet_rlm\.(agent_host|worker)" src/fleet_rlm/
   rg "import fleet_rlm\.(agent_host|worker)" src/fleet_rlm/
   ```

## Example Handoff

```json
{
  "salientSummary": "Created simplified dspy.ReAct agent module in runtime/agent/agent.py with RLMReActChatSignature. Wrote 6 unit tests covering module construction, forward(), optimizer compat, and tool registration. All pass. make lint + make typecheck clean.",
  "whatWasImplemented": "New runtime/agent/agent.py with FleetAgent(dspy.Module) wrapping dspy.ReAct. Constructor accepts tools list and max_iters. Forward passes chat_history and user_message through ReAct. Tests in tests/unit/runtime/agent/test_agent.py.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      { "command": "make format", "exitCode": 0, "observation": "No changes needed" },
      { "command": "make lint", "exitCode": 0, "observation": "Clean" },
      { "command": "make typecheck", "exitCode": 0, "observation": "Clean" },
      { "command": "make test", "exitCode": 0, "observation": "142 passed, 3 skipped" },
      { "command": "rg 'from fleet_rlm.agent_host' src/fleet_rlm/", "exitCode": 1, "observation": "No matches" }
    ],
    "interactiveChecks": []
  },
  "tests": {
    "added": [
      {
        "file": "tests/unit/runtime/agent/test_agent.py",
        "cases": [
          { "name": "test_agent_is_dspy_module", "verifies": "Agent subclasses dspy.Module" },
          { "name": "test_agent_forward_returns_prediction", "verifies": "forward() returns dspy.Prediction" },
          { "name": "test_agent_accepts_tools", "verifies": "Constructor accepts tools list" }
        ]
      }
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- A module you need to modify is not in the expected location or has unexpected structure
- Existing tests fail before your changes (pre-existing failures)
- DSPy API behaves differently than documented in `.factory/library/environment.md`
- You need to modify off-limits directories (integrations/, quality/, frontend/)
