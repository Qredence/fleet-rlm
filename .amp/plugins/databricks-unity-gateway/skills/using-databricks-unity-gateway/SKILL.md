---
name: using-databricks-unity-gateway
description: Uses Databricks Unity Gateway and the `ug` CLI to inspect service access and configure supported coding agents. Use for Databricks AI Gateway, model services, or Unity Gateway setup tasks.
builtin-tools:
  - ug_status
  - ug_skills_list
  - ug_configure_agent
---

# Using Databricks Unity Gateway

Use this skill to inspect the installed Unity Gateway CLI and, when the user names a supported coding agent, configure that agent for a Databricks workspace.

## What this Amp integration does

- `ug_status` and `ug_skills_list` are read-only checks.
- `ug_configure_agent` is limited to Claude, Codex, Gemini, OpenCode, or Copilot. It validates an HTTPS workspace origin and requires an explicit human confirmation before running `ug configure`.
- Amp is not a `ug configure` target. This plugin adds UG workflow tools and a skill to Amp; it does not change the model that Amp itself uses for inference.
- Fleet Lite's Databricks Unity Gateway model routing is separate. In Fleet, model names use the `openai/` prefix and are sent to `https://<workspace-host>/ai-gateway/mlflow/v1`.

## Workflow

1. Run `ug_status` to inspect the current setup. Do not assume a coding agent has been configured just because Databricks OAuth works.
2. Run `ug_skills_list` to see the Databricks skills currently installed for coding agents. Adding or removing those skills changes local agent configuration; get the user's approval before doing so.
3. If the user requests configuration, ask which supported agent to target if they have not named one. Then call `ug_configure_agent` with that agent and the workspace origin. The tool displays a human confirmation before making changes.
4. The installed UG CLI uses `--workspace` (singular) and accepts `--agents` for one or more agent names. Do not use `--workspaces` with this version.
5. If the command opens an OAuth flow, let the user complete sign-in. Never copy tokens, authorization codes, or credentials into chat or logs.

## Supported CLI commands

- `ug status --verbose low` — inspect UG configuration.
- `ug skills list` — inspect configured Databricks skills.
- `ug configure --agents <agent> --workspace https://<workspace-host>` — configure the selected external coding agent. The plugin tool requires human confirmation and accepts only its supported agent list.

Do not call `ug skills add` or `ug skills remove` without an explicit user request; they install or remove files or alter an agent's MCP scope.
