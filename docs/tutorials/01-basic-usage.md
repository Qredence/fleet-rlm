# Tutorial 01: Basic Usage

This tutorial walks through the current interactive surfaces.

## 1. Confirm CLI Surfaces

```bash
uv run fleet-rlm --help
uv run fleet --help
```

## 2. Start Terminal Chat

```bash
uv run fleet-rlm chat --trace-mode compact
```

Try a prompt such as:

- `Summarize the architecture in this repository.`

## 3. Start Web UI

```bash
uv run fleet web
```

Open `http://localhost:8000`.

## 4. Session State API Call

With server running, call a REST status endpoint:

```bash
curl -sS http://127.0.0.1:8000/api/v1/sessions/state
```

## 5. Python Runner Example

```bash
uv run python - <<'PY'
from fleet_rlm.runners import run_long_context

result = run_long_context(
    docs_path="README.md",
    query="What are the key runtime layers?",
    mode="analyze",
)
print(result["answer"])
PY
```
