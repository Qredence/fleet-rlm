# Environment Configuration

| Variable | Description |
|----------|-------------|
| `DAYTONA_API_KEY` | Daytona credentials (required) |
| `DAYTONA_API_URL` | Daytona broker endpoint (required) |
| `DAYTONA_TARGET` | SDK routing/config only (NOT workspace id or volume name) |
| `RLM_CHILD_ISOLATION_MODE` | auto \| context (child sandbox strategy) |
| `RLM_CHILD_FORK_FALLBACK` | clean \| fail (no-volume fallback policy) |
| `RLM_DELEGATE_EXECUTION_TIMEOUT` | Child execution timeout (seconds) |
| `DAYTONA_BROKER_HEALTH_TIMEOUT` | Broker startup timeout (seconds) |
| `DSPY_LM_MODEL` | Planner LM (LiteLLM format, e.g. openai/gpt-4) |
| `DSPY_LLM_API_KEY` / `DSPY_LM_API_KEY` | Planner API key |
| `DELEGATE_LM` | Sub-LM for child RLM (auto-resolves if not set) |
