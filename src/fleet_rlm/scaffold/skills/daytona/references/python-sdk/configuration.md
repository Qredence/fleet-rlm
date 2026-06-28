

Daytona supports multiple methods to configure your environment, in order of precedence:

1. [Configuration in code](#configuration-in-code)
2. [Environment variables](#environment-variables)
3. [.env file](#env-file)
4. [Default values](#default-values)

## Configuration in code

To configure your environment in code, use the `DaytonaConfig` class. The `DaytonaConfig` class accepts the following parameters:

- `api_key`: Your Daytona [API Key](../../SKILL.md#authentication)
- `api_url`: URL of your [Daytona API](../api/README.md)
- `target`: Target region to create the Sandboxes on (`us` / `eu`)

```python
from daytona import DaytonaConfig

config = DaytonaConfig(
    api_key="YOUR_API_KEY",
    api_url="YOUR_API_URL",
    target="us"
)
```

## Environment variables

Daytona supports environment variables for configuration. The SDK automatically looks for these environment variables:

| Variable              | Description                                | Required |
| --------------------- | ------------------------------------------ | -------- |
| **`DAYTONA_API_KEY`** | Your Daytona API key.                      | Yes      |
| **`DAYTONA_API_URL`** | URL of your Daytona API.                   | No       |
| **`DAYTONA_TARGET`**  | Daytona Target to create the sandboxes on. | No       |

### Shell

Set environment variables in your shell using the following methods:

**Bash/Zsh:**

```bash
export DAYTONA_API_KEY=your-api-key
export DAYTONA_API_URL=https://your-api-url
export DAYTONA_TARGET=us
```

**Windows PowerShell:**

```bash
$env:DAYTONA_API_KEY="your-api-key"
$env:DAYTONA_API_URL="https://your-api-url"
$env:DAYTONA_TARGET="us"
```

### .env file

Set the environment variables in a `.env` file using the following format:

```bash
DAYTONA_API_KEY=YOUR_API_KEY
DAYTONA_API_URL=https://your_api_url
DAYTONA_TARGET=us
```

### Async Python SDK

Daytona provides an additional `DAYTONA_HAPPY_EYEBALLS_DELAY` environment variable for HTTP transport tuning in the async Python SDK. Use it to reduce intermittent async connection failures, such as `aiohttp.ClientConnectorError`, that can occur on dual-stack (IPv4/IPv6) networks.

| Variable                           | Description                                                                 | Required |
| ---------------------------------- | --------------------------------------------------------------------------- | -------- |
| **`DAYTONA_HAPPY_EYEBALLS_DELAY`** | Controls **`aiohttp`** Happy Eyeballs (IPv4/IPv6 connection race) delay in seconds. | No       |

- unset or empty: use `aiohttp` default behavior
- `none` (case-insensitive): disable the IPv4/IPv6 race
- non-negative float (for example `0.25`): set an explicit delay in seconds

```bash
# Disable Happy Eyeballs
export DAYTONA_HAPPY_EYEBALLS_DELAY=none

# Or set an explicit delay in seconds
export DAYTONA_HAPPY_EYEBALLS_DELAY=0.25
```

## Default values

If no configuration is provided, Daytona will use its built-in default values:

| **Option** | **Value**                           |
| ---------- | ----------------------------------- |
| API URL    | https://app.daytona.io/api          |
| Target     | Default region for the organization |
