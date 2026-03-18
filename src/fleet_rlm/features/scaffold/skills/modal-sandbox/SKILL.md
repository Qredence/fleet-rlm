---
name: modal-sandbox
description: Manage Modal sandboxes for RLM execution. Use when creating, inspecting, or cleaning up Modal sandboxes and volumes used by fleet_rlm ModalInterpreter.
---

# Modal Sandbox Manager

Manage Modal sandboxes and volumes for fleet-rlm's ModalInterpreter.

## Sandbox Operations

### Create and Use a Sandbox

```python
from fleet_rlm import ModalInterpreter

# Basic sandbox
interp = ModalInterpreter(timeout=600)
interp.start()
result = interp.execute('x = 42\nSUBMIT(answer=x)')
print(result.answer)  # 42
interp.shutdown()

# With volume persistence
interp = ModalInterpreter(timeout=600, volume_name="my-project")
interp.start()
# ... use interp ...
interp.shutdown()
```

### Context Manager (auto-cleanup)

```python
with ModalInterpreter(timeout=600, volume_name="my-data") as interp:
    result = interp.execute('SUBMIT(status="ok")')
    print(result.status)
# Sandbox automatically shut down
```

### List Active Containers

```bash
# List running containers (sandboxes run as containers)
uv run modal container list

# List all apps (includes sandbox apps)
uv run modal app list
```

### View Container Logs

```bash
# Connect to a running container for logs/debugging
uv run modal container exec <container-id> -- <command>

# View app logs
uv run modal app logs <app-name>
```

### Terminate a Sandbox

```python
# Use Python API for clean shutdown
from fleet_rlm import ModalInterpreter

interp = ModalInterpreter()
interp.start()
# ... use sandbox ...
interp.shutdown()  # Clean termination
```

## Volume Operations

### Create a Volume (one-time)

```bash
uv run modal volume create my-project-data
```

### List Volumes

```bash
uv run modal volume list
```

### Inspect Volume Contents

```python
with ModalInterpreter(volume_name="my-project-data") as interp:
    result = interp.execute('''
import os
files = os.listdir('/data') if os.path.exists('/data') else []
SUBMIT(files=files, count=len(files))
''')
    print(result.files)
```

### Deep Volume Inspection

```python
with ModalInterpreter(volume_name="my-project-data") as interp:
    result = interp.execute('''
import os
files = []
for root, dirs, filenames in os.walk('/data'):
    for f in filenames:
        path = os.path.join(root, f)
        size = os.path.getsize(path)
        files.append(f"{path} ({size} bytes)")
SUBMIT(files=files, total=len(files))
''')
    print(f"Found {result.total} files:")
    for f in result.files:
        print(f"  {f}")
```

### Upload Files to Volume

```python
with ModalInterpreter(volume_name="my-project-data") as interp:
    # Upload local files/directories
    interp.upload_to_volume(
        local_dirs={"my_data": "/data/my_data"},
        local_files={"config.json": "/data/config.json"}
    )

    # Verify upload
    result = interp.execute('''
import os
SUBMIT(uploaded=os.path.exists('/data/config.json'))
''')
    print(f"Upload successful: {result.uploaded}")
```

### Delete a Volume

```bash
uv run modal volume delete my-project-data
```

## ModalInterpreter Configuration

| Parameter            | Default             | Description                       |
| :------------------- | :------------------ | :-------------------------------- |
| `timeout`            | 600                 | Sandbox lifetime (seconds)        |
| `idle_timeout`       | None                | Auto-shutdown when idle (seconds) |
| `execute_timeout`    | Same as `timeout`   | Max time for `execute()` calls    |
| `volume_name`        | None                | Volume for persistence            |
| `volume_mount_path`  | "/data"             | Mount point in sandbox            |
| `image_pip_packages` | ("numpy", "pandas") | Packages installed in sandbox     |
| `secret_name`        | "LITELLM"           | Modal secret for API keys         |

### Example: Custom Configuration

```python
interp = ModalInterpreter(
    timeout=1800,           # 30 minutes
    idle_timeout=300,       # Shutdown after 5 min idle
    volume_name="my-data",  # Mount volume
    image_pip_packages=(    # Custom packages
        "numpy",
        "pandas",
        "scikit-learn"
    )
)
```

## Quick Reference

| Task               | Command                                        |
| ------------------ | ---------------------------------------------- |
| Create sandbox     | `ModalInterpreter(timeout=600).start()`        |
| Create with volume | `ModalInterpreter(volume_name="name").start()` |
| List containers    | `uv run modal container list`                  |
| List apps          | `uv run modal app list`                        |
| Container exec     | `uv run modal container exec <id> -- <cmd>`    |
| Create volume      | `uv run modal volume create <name>`            |
| List volumes       | `uv run modal volume list`                     |
| Delete volume      | `uv run modal volume delete <name>`            |
| Terminate sandbox  | `interp.shutdown()` (Python API)               |

## Troubleshooting

See `rlm-debug` skill for comprehensive diagnostics.
