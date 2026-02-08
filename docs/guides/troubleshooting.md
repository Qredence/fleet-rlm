# Troubleshooting

Common issues encountered when working with `fleet-rlm` and their solutions.

## Configuration Issues

### "Planner LM not configured"

**Symptoms**: The agent fails immediately saying it cannot find a model.
**Cause**: The environment variables for the LLM are missing.
**Solution**:

1.  Check your `.env` file exists in the root.
2.  Ensure `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` are set.
3.  Restart your shell or Jupyter kernel to reload env vars.

### "Modal secret not found"

**Symptoms**: The code runs locally but fails inside the sandbox with authentication errors.
**Cause**: The secrets haven't been pushed to Modal.
**Solution**:
Run the secret creation command again:

```bash
uv run modal secret create LITELLM DSPY_LLM_API_KEY=...
```

## Execution Issues

### "Modal sandbox process exited unexpectedly"

**Symptoms**: The execution stops abruptly.
**Cause**: Often due to token expiry or connectivity issues.
**Solution**:
Refreshes your authentication:

```bash
uv run modal token set
```

### "No module named 'modal'"

**Symptoms**: Python import errors.
**Cause**: Dependencies are not installed in the current environment.
**Solution**:
Sync your dependencies:

```bash
uv sync
```

### Modal Package Shadowing

**Symptoms**: Weird `AttributeError` on `modal` objects.
**Cause**: You have a file named `modal.py` in your working directory, confusing Python imports.
**Solution**:
Delete or rename any file named `modal.py` in your project root or source folders.

## Debugging Tips

- **Check the Trajectory**: Use `run-trajectory` to see exactly what the Planner is thinking and what code it is writing.
- **Inspect Secrets**: Use `check-secret` and `check-secret-key` to verify the environment values that the application sees.
