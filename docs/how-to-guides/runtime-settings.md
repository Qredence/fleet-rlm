# Runtime Setup from Frontend Settings

Use the Runtime settings surface to configure LM + Modal runtime values and validate connectivity from the UI.

Runtime settings are available in both:

- the full settings page (`/settings`)
- the settings dialog (`Settings -> Runtime`)

## What You Can Configure

The Runtime pane edits these allowlisted keys:

- `DSPY_LM_MODEL`
- `DSPY_LLM_API_KEY`
- `DSPY_LM_API_BASE`
- `DSPY_LM_MAX_TOKENS`
- `MODAL_TOKEN_ID`
- `MODAL_TOKEN_SECRET`
- `SECRET_NAME`
- `VOLUME_NAME`

Secret-like values are always returned masked by the API and rendered as masked values in the UI.

The Runtime UI uses **write-only secret inputs**:

- secret fields are never prefilled as editable values
- entering a value rotates that secret on save
- use **Clear saved value** to send an explicit empty-string clear on save

## Runtime Test Buttons

The Runtime pane provides:

- **Test Modal Connection**: preflight checks + live Modal sandbox smoke (`python -c "print('ok')"`).
- **Test LM Connection**: preflight checks + live planner LM smoke prompt.
- **Test All**: runs Modal then LM tests sequentially.

The pane also shows:

- preflight check badges (`configured` / `missing`)
- last smoke result and timestamp
- active planner/delegate model resolution from `/api/v1/runtime/status`
- backend guidance text for remediation

## Local-Only Settings Writes

Runtime settings updates are intentionally restricted to local development:

- `PATCH /api/v1/runtime/settings` succeeds only when `APP_ENV=local`.
- Non-local environments return `403` for runtime writes.
- Read and test endpoints remain available in all environments.

When local writes are enabled, model-related updates (`DSPY_LM_MODEL`, `DSPY_DELEGATE_LM_MODEL`) are also applied to in-memory runtime config before LM clients are rebuilt, so new values take effect immediately.

## Expected Remediation Steps

When tests fail:

1. Ensure LM model + API key are set (`DSPY_LM_MODEL`, `DSPY_LLM_API_KEY`).
2. Ensure Modal credentials are available (`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` or `modal setup` profile).
3. Confirm `SECRET_NAME` points to an existing Modal secret.
4. Re-run **Test Modal Connection** and **Test LM Connection**.

Skill creation chat uses soft-gating: runtime health warnings are shown, but chat input remains enabled unless a separate backend-disabled condition applies.
