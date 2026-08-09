# Run pinned Prime Oolong against Fleet RLM

Fleet's maintained Oolong lane uses `primeintellect/oolong-rlm@0.1.11` as the sole dataset and deterministic scoring authority. The trusted-host runner exports tasks and calls the environment's default `OolongRubric` through an isolated JSONL sidecar. It never starts Prime's legacy `RLMEnv`; Fleet execution remains stock `dspy.RLM` through the Attachment and HTTP/SSE interfaces.

## Setup

Install and authenticate the Prime CLI, then verify access:

```bash
uv tool install prime
prime login
prime whoami
```

Do not install `oolong_rlm` or Verifiers into Fleet's project environment. The runner verifies environment version `0.1.11`, Hub hash `97d47526`, version ID `zixnre6tq4e4drk82nm2ebph`, and the three pinned source hashes before it creates an isolated uv sidecar from Prime's package index.

Start Fleet with the `daytona-bench` profile. This profile uses `deepseek-v4-flash` for both DSPy model roles, routes through `uscentral.default.zencode-oai`, disables both LM caches, and leaves MLflow tracing off. Put real provider values in `.env`; scripts load it with `python-dotenv` and `override=False`, so existing process exports win.

```bash
FLEET_DAYTONA_API_KEY=<real Daytona API key>
DATABRICKS_TOKEN=<real Databricks token>
FLEET_DATABRICKS_AI_GATEWAY_BASE_URL=https://<your-workspace>/ai-gateway/openai/v1
FLEET_DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>/<database>
```

Select `[config] default_profile = "daytona-bench"` in `config/fleet.toml` or use the TUI `/profiles` command, then restart Fleet. Run migrations and the API from separate terminals:

```bash
uv run alembic upgrade head
uv run fleet-rlm serve-api --port 8000
```

The Prime Oolong runner is enabled by the committed
`[defaults.runtime].live_enabled = true` policy. Set that value to `false`
when live provider execution must fail closed; the runner still requires the
credential variables above and does not run merely because the policy is true.

Verify the active server before a paid run:

```bash
curl -fsS http://127.0.0.1:8000/openapi.json >/dev/null
curl -fsS http://127.0.0.1:8000/api/settings | jq '{active_profile, default_profile}'
uv run fleet doctor daytona
```

## Phase mechanics gate

Run one task first:

```bash
uv run python scripts/benchmarks/run_prime_oolong.py \
  --profile daytona-bench --limit 1 \
  --output .scratch/fleet-rlm-recursive-runtime/evidence/prime-oolong-one-row.json
```

Continue with the fixed twelve-task gate only after the first receipt has one typed completion, one deterministic reward, matching prepared/accessed context evidence, and no infrastructure error:

```bash
uv run python scripts/benchmarks/run_prime_oolong.py \
  --profile daytona-bench --limit 12 \
  --output .scratch/fleet-rlm-recursive-runtime/evidence/prime-oolong-12-row.json
```

`make benchmark-oolong` runs the same twelve-task configuration. The fixed task set is the first twelve filtered rows from synth validation `trec_coarse` at context length `131072`, with numerical tasks removed, no shuffle, no environment tips, and `reward_mode="oolong"`.

The receipt contains only environment identity, dataset arguments, example IDs and digests, model roles, DSPy contract identity, sanitized usage, prepared/accessed evidence, typed termination, deterministic rewards, and the bounded DSPy-mapped trajectory diagnostic. It contains no contexts, gold answers, raw trajectories, credentials, preview-token URLs, or provider objects.

This is a mechanics gate: all tasks must complete through typed DSPy `SUBMIT`, context evidence must match, and rescoring must be identical. Mean Oolong reward and the trajectory diagnostic are recorded but are not promotion thresholds in this phase.

After the benchmark, return to the intended interactive profile (the shipped
normal default is `daytona-recursive`), restart Fleet, and verify both
`active_profile` and `default_profile`.
