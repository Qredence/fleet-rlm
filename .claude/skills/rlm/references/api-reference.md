# ModalInterpreter API Reference

## Constructor

```python
ModalInterpreter(
    *,
    image: modal.Image | None = None,          # Custom sandbox image
    app: modal.App | None = None,               # Existing Modal App
    secrets: list[modal.Secret] | None = None,  # Default: [Secret.from_name(secret_name)]
    timeout: int = 600,                         # Sandbox lifetime (seconds)
    idle_timeout: int | None = None,            # Idle timeout
    execute_timeout: int | None = None,         # Per-execute() timeout (default: same as timeout)
    app_name: str = "dspy-rlm-interpreter",     # Modal App name
    secret_name: str = "LITELLM",               # Default secret name
    image_python_version: str = "3.12",         # Python version in sandbox
    image_pip_packages: Sequence[str] = ("numpy", "pandas"),  # Packages in sandbox
    volume_name: str | None = None,             # Modal Volume V2 name
    volume_mount_path: str = "/data",           # Volume mount point
)
```

## Methods

| Method               | Signature                                             | Returns              | Description                               |
| -------------------- | ----------------------------------------------------- | -------------------- | ----------------------------------------- |
| `start()`            | `start() -> None`                                     | —                    | Create sandbox, start driver (idempotent) |
| `execute()`          | `execute(code, variables=None)`                       | `str \| FinalOutput` | Run code in sandbox                       |
| `shutdown()`         | `shutdown() -> None`                                  | —                    | Terminate sandbox (idempotent)            |
| `commit()`           | `commit() -> None`                                    | —                    | Commit volume changes                     |
| `reload()`           | `reload() -> None`                                    | —                    | Reload volume from remote                 |
| `upload_to_volume()` | `upload_to_volume(local_dirs=None, local_files=None)` | —                    | Upload local files to volume              |

## Context Manager

```python
with ModalInterpreter(timeout=600) as interp:
    result = interp.execute("print('hello')")
# sandbox automatically shut down
```

## execute() Return Types

- **No SUBMIT**: Returns `str` (stdout + stderr)
- **With SUBMIT**: Returns `FinalOutput` — access fields as attributes:
  ```python
  result = interp.execute("SUBMIT(count=42, items=['a','b'])")
  print(result.count)   # 42
  print(result.items)   # ['a', 'b']
  ```

## Sandbox-Side Helpers

These functions are injected automatically by the driver and available inside
`interp.execute()` code:

| Helper             | Signature                                      | Returns                                   |
| ------------------ | ---------------------------------------------- | ----------------------------------------- |
| `peek`             | `peek(text, start=0, length=2000)`             | `str` — slice of text                     |
| `grep`             | `grep(text, pattern, *, context=0)`            | `list[str]` — matching lines              |
| `chunk_by_size`    | `chunk_by_size(text, size=4000, overlap=200)`  | `list[str]`                               |
| `chunk_by_headers` | `chunk_by_headers(text, pattern=r"^#{1,3}\s")` | `list[dict]` with keys `header`, `content`|
| `add_buffer`       | `add_buffer(name, value)`                      | `None` — append to named buffer           |
| `get_buffer`       | `get_buffer(name)`                             | `list` — buffer contents                  |
| `clear_buffer`     | `clear_buffer(name=None)`                      | `None` — clear one or all buffers         |
| `save_to_volume`   | `save_to_volume(path, content)`                | `str` — full path written                 |
| `load_from_volume` | `load_from_volume(path)`                       | `str` — file contents                     |
| `SUBMIT`           | `SUBMIT(**kwargs)`                             | Ends execution, returns structured output |

## DSPy Signatures

Built-in signatures from `src/fleet_rlm/signatures.py`:

| Signature               | Inputs              | Outputs                                                           |
| ----------------------- | ------------------- | ----------------------------------------------------------------- |
| `ExtractArchitecture`   | `docs`, `query`     | `modules` (list), `optimizers` (list), `design_principles` (str)  |
| `ExtractAPIEndpoints`   | `docs`              | `api_endpoints` (list)                                            |
| `FindErrorPatterns`     | `docs`              | `error_categories` (dict), `total_errors_found` (int)             |
| `ExtractWithCustomTool` | `docs`              | `headers` (list), `code_blocks` (list), `structure_summary` (str) |
| `AnalyzeLongDocument`   | `document`, `query` | `findings` (list), `answer` (str), `sections_examined` (int)      |
| `SummarizeLongDocument` | `document`, `focus` | `summary` (str), `key_points` (list), `coverage_pct` (int)        |
| `ExtractFromLogs`       | `logs`, `query`     | `matches` (list), `patterns` (dict), `time_range` (str)           |

## Volume Operations

### One-Time Setup

```bash
uv run modal volume create rlm-volume-dspy
```

### Upload Local Files

```python
interp = ModalInterpreter(volume_name='rlm-volume-dspy')
interp.upload_to_volume(
    local_dirs={'rlm_content/dspy-knowledge': '/dspy-knowledge'},
)
```

### Read from Volume Inside Sandbox

```python
result = interp.execute("""
import pathlib
doc = pathlib.Path('/data/dspy-knowledge/dspy-doc.txt').read_text()
print(f'Loaded {len(doc):,} chars from volume')
""")
```

## Troubleshooting

| Issue                          | Fix                                                         |
| ------------------------------ | ----------------------------------------------------------- |
| "Planner LM not configured"    | Set `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` in `.env`        |
| "Modal sandbox process exited" | Run `uv run modal token set` and `uv run modal volume list` |
| Timeout errors                 | Increase `--timeout` (CLI) or `timeout=` (Python)           |
| Volume not persisting          | Use the same `volume_name` across sessions                  |
| `FinalOutput` attribute error  | Access fields as `.field`, not `['field']`                  |
