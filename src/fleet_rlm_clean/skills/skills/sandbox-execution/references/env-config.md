# Clean environment knobs

Prefix: `FLEET_CLEAN_`.

| Variable | Default | Notes |
|----------|---------|-------|
| `APP_NAME` | `fleet-rlm-clean` | FastAPI title |
| `DAYTONA_API_KEY` | unset | Required for live Daytona |
| `LLM_API_KEY` | unset | Required for live LM |
| `LLM_BASE_URL` | unset | Optional OpenAI-compatible base |
| `ROOT_MODEL` | `openai/gpt-4o-mini` | Root `dspy.LM` id |
| `SUB_MODEL` | `openai/gpt-4o-mini` | Sub-LM for `llm_query*` |
| `DATABASE_URL` | unset | Async SQLAlchemy URL |
| `VOLUME_NAME` | `rlm-volume-dspy` | Daytona volume |
| `VOLUME_MOUNT_PATH` | `/home/daytona/fleet` | Absolute mount |
| `LIVE_KERNEL` | `false` | Opt-in live providers |
| `AUTH_MODE` | `dev` | `dev` or `neon` only |
| `NEON_AUTH_URL` | product default | Neon Auth base override |
| `UPLOAD_ROOT` | unset | Host attachment blob root |
| `ARTIFACT_ROOT` | unset | Host artifact blob root |

Unknown `AUTH_MODE` values fail closed at settings load.
