# Direct DSPy model configuration

Install `fleet-rlm` and complete the existing PostgreSQL and Daytona setup. In guided
setup, enter one DSPy route, an optional compatible endpoint, and an optional API key.
The same model supplies root, sub, reflection, and correctness-judge roles.

```dotenv
FLEET_LLM_MODEL=openai/gpt-4o-mini
FLEET_LLM_API_KEY=your-key
# FLEET_LLM_BASE_URL=https://your-compatible-endpoint.example/v1
```

Keep real credentials in your local `.env` or process environment. DSPy's native
provider authentication works with process environment credentials when no explicit
Fleet key is configured. Fleet does not export arbitrary `.env` variables globally.

An optional schema-5 TOML file supports defaults and independent role overrides:

```toml
[config]
schema_version = 5
[llm]
model = "openai/gpt-4o-mini"
adapter = "chat"
[llm.sub]
model = "anthropic/claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"
```

A role with no model override inherits the default connection. An explicit role
model uses native authentication unless its own `api_base` or `api_key_env` is set.
Generation settings can be supplied in `[llm]` and overridden in a role table.
Existing role generation defaults still apply when neither is configured.

Credential references resolve from process environment before the selected dotenv
file and are snapshotted at settings load. Restart/reload to adopt changed values.
Session model selection changes the route while retaining the role's connection.

Model browsing uses only an explicit OpenAI-compatible endpoint's `/models` API.
Discovery is optional; manual route entry always works. `/models?role=root` selects
which configured connection to browse. The old `scope` parameter is ignored.

Remove abandoned `[models]` and `[connections]` tables. Replace catalog IDs with
actual DSPy routes. Remove Unity-specific discovery configuration from model setup.
Databricks tracking credentials and the MLflow diagnostic judge remain independent.

DSPy 3.4.1 remains pinned and inference uses native `lm15`. Code/configuration
changes invalidate prior optimization evidence; reevaluate before installation.
