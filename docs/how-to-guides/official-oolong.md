# Run official Oolong against Fleet RLM

Use the official scorer checkout rather than the upstream LiteLLM evaluator: Fleet is exercised through its Attachment and HTTP/SSE interfaces.

## Setup

Clone the scorer alongside this repository and install the benchmark extra:

```bash
git clone https://github.com/abertsch72/oolong.git ../oolong
uv sync --all-extras --dev
```

Start Fleet with the dedicated Databricks-Qwen benchmark profile. It uses Daytona, disables both LLM caches, and intentionally leaves MLflow off.
Fleet scopes the stock `dspy.JSONAdapter()` to each Turn with
`dspy.context(adapter=...)`. Provider-native token streams and sectioned text
are not reinterpreted as RLM actions; malformed responses remain bounded DSPy
adapter-parse failures. See the [DSPy adapter contract](https://dspy.ai/diving-deeper/adapters).

Put real values for the following variables in the repository `.env`; both Fleet and Alembic load that file. Do **not** export literal placeholder values such as `"..."`: shell exports take precedence over `.env`, and `FLEET_DATABASE_URL="..."` is not a valid SQLAlchemy URL.

```bash
FLEET_DAYTONA_API_KEY=<real Daytona API key>
DATABRICKS_TOKEN=<real Databricks token>
FLEET_DATABRICKS_AI_GATEWAY_BASE_URL=https://<your-workspace>/ai-gateway/openai/v1
FLEET_DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>/<database>
```

Select `daytona-bench` by setting
`[config] default_profile = "daytona-bench"` in `config/fleet.toml`, or choose
it with the TUI `/profiles` command. Profile edits are pending until Fleet is
restarted. With `.env` configured, run the migration and the server from
separate terminals:

```bash
uv run alembic upgrade head
```

```bash
uv run fleet-rlm serve-api --port 8000
```

In another terminal, verify the server before a paid sweep:

```bash
curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null && echo "Fleet API is healthy"
curl -fsS http://127.0.0.1:8000/api/settings | jq '{active_profile, default_profile}'
```

Run `uv run fleet doctor daytona` before the smoke. The release verifier is intentionally
pinned to the production Databricks DeepSeek v4-free Root and Qwen Sub roles and is not a
benchmark-profile preflight.

## Smoke and sweep

The published real split begins around 49K `o200k_base` tokens; a three-example
32–64K smoke is therefore the first valid real bucket. The Make target runs it
by default and verifies that the live server uses the `daytona-bench` profile:

```bash
export FLEET_LIVE=1
make benchmark-oolong
```

Only continue when parse confidence is high, iterations are below 20, and scores are not uniformly zero. Sweep each bucket separately; results remain in `.scratch/`:

```bash
for bounds in "32000 64000" "64000 132000"; do
  set -- $bounds
  FLEET_LIVE=1 uv run python scripts/benchmarks/run_official_oolong.py \
    --split real --min-len "$1" --max-len "$2" --limit 20 \
    --expected-profile daytona-bench \
    --output ".scratch/oolong-real-$1-$2.json"
done
```

For the raised-iteration ablation, select
`[config] default_profile = "daytona-bench-40"` (or choose it with
`/profiles`), restart Fleet, and verify `active_profile` before running the
same command. Add `--skill-id` and `--skill-version`, obtained from
`GET /api/skills`, for the long-context-skill ablation.

The runner reads the active profile, root model, and iteration ceiling from the
live server's loopback-only settings endpoint. The JSON receipt records that
server-authoritative policy alongside the scorer revision, per-example official
score, parse confidence, usage, and aggregate parse-failure, iteration-ceiling,
and error rates.

After the benchmark, select the normal `daytona` profile through the same TOML
or `/profiles` workflow, restart Fleet, and verify that both `active_profile`
and `default_profile` report `daytona`.
