#!/usr/bin/env python3
"""Test /v1/responses endpoint for models declared in config/config.yaml.

Security:
- Never hardcode API keys.
- Never print API key values.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import yaml


DEFAULT_PROMPT = "Respond with exactly one word: working"


def load_dotenv(env_path: Path) -> None:
    """Load .env values into process env without overwriting existing values."""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_models_from_config(config_path: Path) -> list[str]:
    data = yaml.safe_load(config_path.read_text()) or {}
    model_list = data.get("model_list", [])

    models: list[str] = []
    for item in model_list:
        info = item.get("model_info", {}) or {}
        mode = info.get("mode", "chat")
        model_name = item.get("model_name")
        # responses endpoint should only test chat-like models
        if model_name and mode == "chat":
            models.append(model_name)

    return models


def safe_proxy_label(proxy_url: str) -> str:
    # Keep only scheme/host/port for debugging and hide userinfo, path, query, and fragments.
    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return "<invalid-url>"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


async def test_model(
    session: aiohttp.ClientSession,
    *,
    model: str,
    api_base: str,
    api_key: str,
    prompt: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Test a single model with the responses endpoint."""
    url = f"{api_base.rstrip('/')}/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": prompt,
        "max_output_tokens": 50,
    }

    start_time = datetime.now()
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with session.post(
            url, headers=headers, json=payload, timeout=timeout
        ) as response:
            elapsed = (datetime.now() - start_time).total_seconds()

            if response.status == 200:
                data = await response.json()
                output_text = "N/A"
                if data.get("output"):
                    content = data["output"][0].get("content", [])
                    if content:
                        output_text = content[0].get("text", "N/A")[:50]

                return {
                    "model": model,
                    "status": "OK",
                    "http_status": response.status,
                    "response_time": f"{elapsed:.2f}s",
                    "output": output_text,
                    "error": None,
                }

            error_text = await response.text()
            return {
                "model": model,
                "status": "FAILED",
                "http_status": response.status,
                "response_time": f"{elapsed:.2f}s",
                "output": None,
                "error": error_text[:200],
            }
    except asyncio.TimeoutError:
        return {
            "model": model,
            "status": "TIMEOUT",
            "http_status": None,
            "response_time": f">{timeout_seconds}s",
            "output": None,
            "error": f"Request timed out after {timeout_seconds}s",
        }
    except Exception as exc:  # pragma: no cover
        return {
            "model": model,
            "status": "ERROR",
            "http_status": None,
            "response_time": "N/A",
            "output": None,
            "error": str(exc)[:200],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test LiteLLM /v1/responses for chat models in config.yaml"
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to LiteLLM config YAML (default: config/config.yaml)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Request timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt to send to each model",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    # Resolve repository root from this script location to avoid cwd-sensitive behavior.
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    load_dotenv(env_path)

    api_key = os.environ.get("DSPY_LLM_API_KEY")
    api_base = os.environ.get("LITELLM_PROXY_BASE_URL")

    if not api_key:
        print("Missing API key in .env: DSPY_LLM_API_KEY")
        return 1
    if not api_base:
        print("Missing API base in .env: LITELLM_PROXY_BASE_URL")
        return 1

    config_arg_path = Path(args.config)
    config_path = (
        config_arg_path
        if config_arg_path.is_absolute()
        else (repo_root / config_arg_path)
    )
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        return 1

    models = read_models_from_config(config_path)
    if not models:
        print(f"No chat models found in {config_path}")
        return 1

    print("=" * 80)
    print("Testing /v1/responses endpoint for chat models from config")
    print(f"Config: {config_path}")
    print(f".env: {env_path}")
    print(f"Proxy: {safe_proxy_label(api_base)}")
    print(f"Models to test: {len(models)}")
    print("API key: loaded (value hidden)")
    print("=" * 80)

    async with aiohttp.ClientSession() as session:
        tasks = [
            test_model(
                session,
                model=model,
                api_base=api_base,
                api_key=api_key,
                prompt=args.prompt,
                timeout_seconds=args.timeout,
            )
            for model in models
        ]
        results = await asyncio.gather(*tasks)

    working_count = 0
    failed_count = 0

    for result in results:
        model = result["model"]
        provider = model.split("/")[0] if "/" in model else "vertex_ai"

        print(f"[{provider}] {result['status']} {model}")
        print(f"  Time: {result['response_time']} | HTTP: {result['http_status']}")
        if result["output"]:
            print(f"  Output: '{result['output']}'")
        if result["error"]:
            print(f"  Error: {result['error'][:100]}")

        if result["status"] == "OK":
            working_count += 1
        else:
            failed_count += 1

    print("=" * 80)
    print(
        f"SUMMARY: {working_count} working, {failed_count} failed out of {len(models)} models tested"
    )
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
