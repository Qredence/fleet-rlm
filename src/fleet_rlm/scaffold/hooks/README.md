# Claude Code Hooks

This directory contains event-driven hooks for the fleet-rlm project using the [hookify](https://docs.claude.ai/code/docs/hooks) system.

## What Are Hooks?

Hooks automatically trigger actions based on events (like user prompts) to:
- Suggest relevant skills/agents for specific tasks
- Provide contextual help when issues are detected
- Guide users toward optimal workflows

## Hook Types

### 1. Prompt-Based Hooks (Hookify Rules)

Files with `.local.md` extension using YAML frontmatter:

```yaml
---
name: hook-name
enabled: true
event: prompt
conditions:
  - field: user_prompt
    operator: regex_match
    pattern: (keyword).*?(trigger)
---

Hook content in Markdown...
```

**Current Hooks:**

| Hook File | Trigger | Purpose |
|-----------|---------|---------|
| `hookify.fleet-rlm-document-process.local.md` | Document processing keywords | Suggests RLM skills for document analysis |
| `hookify.fleet-rlm-large-file.local.md` | Large file mentions | Recommends RLM for files >100K lines |
| `hookify.fleet-rlm-llm-query-error.local.md` | llm_query errors | Provides debugging guidance |
| `hookify.fleet-rlm-modal-error.local.md` | Modal/sandbox errors | Suggests debugging skills/agents |

### 2. Tool-Based Hooks

Defined in `../settings.local.json` under the `hooks` key:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "uv run ruff format \"$FILE\" && uv run ruff check --fix \"$FILE\"",
            "statusMessage": "Running ruff format and check..."
          }
        ]
      }
    ]
  }
}
```

**Current Tool Hook:**
- **Post-Edit**: Automatically runs `ruff format` and `ruff check --fix` after any file edit

## Hook Naming Convention

Prompt hooks follow the pattern:
```
hookify.{context}.{trigger}.{scope}.md
```

- `context`: Project/domain (e.g., `fleet-rlm`)
- `trigger`: What activates the hook (e.g., `large-file`, `modal-error`)
- `scope`: `local` (project-specific) or `global` (user-wide)

## Adding New Hooks

1. Create a new `.local.md` file in this directory
2. Add YAML frontmatter with `name`, `enabled`, `event`, and `conditions`
3. Write helpful Markdown content
4. Test by triggering the condition

## Documentation

- [Claude Code Hooks Documentation](https://docs.claude.ai/code/docs/hooks)
- [Hookify Plugin Guide](https://docs.claude.ai/code/docs/plugins/hookify)
