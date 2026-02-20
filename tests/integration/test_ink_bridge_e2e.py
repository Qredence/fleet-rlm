"""End-to-end smoke tests for Ink bridge architecture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def bridge_server_script(tmp_path):
    """Create a minimal bridge server test script."""
    script = tmp_path / "test_bridge.py"
    script.write_text("""
import sys
import json

def write_payload(payload):
    sys.stdout.write(json.dumps(payload) + "\\n")
    sys.stdout.flush()

def main():
    write_payload({"jsonrpc": "2.0", "result": {"status": "ready"}, "id": 1})

    for line in sys.stdin:
        try:
            req = json.loads(line.strip())
        except:
            continue

        method = req.get("method")
        params = req.get("params", {})
        req_id = req.get("id")

        if method == "ping":
            result = {"pong": True}
        elif method == "echo":
            result = {"echo": params.get("message", "")}
        elif method == "shutdown":
            write_payload({"jsonrpc": "2.0", "result": {"ok": True}, "id": req_id})
            break
        else:
            result = {"error": "unknown method"}

        write_payload({"jsonrpc": "2.0", "result": result, "id": req_id})

if __name__ == "__main__":
    main()
""")
    return script


def test_bridge_protocol_roundtrip(bridge_server_script):
    """Test that bridge can start, handle requests, and shut down cleanly."""
    proc = subprocess.Popen(
        [sys.executable, str(bridge_server_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        # Read startup message
        startup = proc.stdout.readline()
        assert startup.strip()
        startup_msg = json.loads(startup)
        assert startup_msg["result"]["status"] == "ready"

        # Send ping
        ping_req = json.dumps(
            {"jsonrpc": "2.0", "method": "ping", "params": {}, "id": 1}
        )
        proc.stdin.write(ping_req + "\n")
        proc.stdin.flush()

        ping_resp = proc.stdout.readline()
        assert ping_resp.strip()
        ping_msg = json.loads(ping_resp)
        assert ping_msg["result"]["pong"] is True

        # Send echo
        echo_req = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "echo",
                "params": {"message": "hello fleet"},
                "id": 2,
            }
        )
        proc.stdin.write(echo_req + "\n")
        proc.stdin.flush()

        echo_resp = proc.stdout.readline()
        assert echo_resp.strip()
        echo_msg = json.loads(echo_resp)
        assert echo_msg["result"]["echo"] == "hello fleet"

        # Shutdown
        shutdown_req = json.dumps(
            {"jsonrpc": "2.0", "method": "shutdown", "params": {}, "id": 3}
        )
        proc.stdin.write(shutdown_req + "\n")
        proc.stdin.flush()

        shutdown_resp = proc.stdout.readline()
        assert shutdown_resp.strip()
        shutdown_msg = json.loads(shutdown_resp)
        assert shutdown_msg["result"]["ok"] is True

        # Wait for clean exit
        proc.wait(timeout=2)
        assert proc.returncode == 0

    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=1)


@pytest.mark.asyncio
async def test_bridge_handlers_available():
    """Verify all required handlers are importable and callable."""
    from fleet_rlm.bridge import (
        handlers_chat,
        handlers_commands,
        handlers_mentions,
        handlers_settings,
        handlers_status,
    )

    # Commands
    assert callable(handlers_commands.list_commands)
    result = handlers_commands.list_commands()
    assert "tool_commands" in result
    assert "wrapper_commands" in result

    # Settings (minimal smoke test)
    assert callable(handlers_settings.get_settings)
    # Don't call get_settings without proper env to avoid side effects

    # Mentions
    assert callable(handlers_mentions.search_mentions)
    result = handlers_mentions.search_mentions({"query": "test", "root": "."})
    assert "items" in result
    assert "count" in result

    # Status
    assert callable(handlers_status.get_status)

    # Chat
    assert callable(handlers_chat.submit_chat)


def test_bridge_server_module_runnable():
    """Verify bridge server module can be invoked."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "fleet_rlm.bridge.server", "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(timeout=30)

    # Should either show help or at least not crash
    # (current impl may not have --help, but module should be runnable)
    assert proc.returncode in (0, 2), f"stderr: {stderr}"


@pytest.mark.skipif(
    not Path("tui-ink/dist/cli.js").exists(),
    reason="Ink bundle not built",
)
def test_ink_bundle_exists():
    """Verify Ink frontend bundle is available."""
    bundle = Path("tui-ink/dist/cli.js")
    assert bundle.exists()
    assert bundle.stat().st_size > 0


def test_fleet_cli_launcher_imports():
    """Verify fleet CLI launcher module imports without errors."""
    from fleet_rlm.fleet_cli import main

    assert callable(main)


def test_ink_cli_uses_plural_bridge_rpc_methods():
    """Guard Ink↔bridge method-name contract for command + mention paths."""
    source = Path("tui-ink/src/cli.tsx").read_text(encoding="utf-8")

    assert "commands.execute" in source
    assert "mentions.search" in source
    assert "command.execute" not in source
    assert "mention.search" not in source
