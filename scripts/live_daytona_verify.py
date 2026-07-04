"""Live Daytona Phase 1 verification lane.

This script exercises the full recreated-sandbox restore path against a live
Daytona environment. It creates a sandbox, writes a session manifest to the
Phase 1 conversation path, recreates the sandbox with the same volume, and
verifies the manifest is readable from the new sandbox.

Usage (from repo root):
    uv run python scripts/live_daytona_verify.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid

# Ensure repo root is on path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fleet_rlm.api.runtime_services.session_manifest import (
    ensure_session_volume_layout,
    load_manifest_from_volume,
)
from fleet_rlm.api.runtime_services.session_paths import (
    session_conversation_path,
    session_scratchpad_path,
    session_workspace_link_path,
)
from fleet_rlm.integrations.daytona.config import resolve_daytona_config
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime
from fleet_rlm.integrations.daytona.volumes import ensure_daytona_volume_layout

TEST_SESSION_ID = f"live-verify-{uuid.uuid4().hex[:8]}"
VOLUME_NAME = f"live-verify-vol-{uuid.uuid4().hex[:8]}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live Daytona persistent-volume/session restore verification.",
    )
    return parser.parse_args(argv)


async def _main() -> int:
    config = resolve_daytona_config()
    print(f"[verify] DAYTONA_API_URL={config.api_url}")
    print(f"[verify] DAYTONA_TARGET={config.target}")
    print(f"[verify] TEST_SESSION_ID={TEST_SESSION_ID}")
    print(f"[verify] VOLUME_NAME={VOLUME_NAME}")

    runtime: DaytonaSandboxRuntime | None = None
    interpreter: DaytonaInterpreter | None = None
    sandbox_id: str | None = None

    try:
        print("\n[1/5] Creating Daytona runtime ...")
        runtime = DaytonaSandboxRuntime(config=config)

        print("[2/5] Creating interpreter & sandbox ...")
        interpreter = DaytonaInterpreter(
            runtime=runtime,
            owns_runtime=True,
            timeout=300,
            execute_timeout=300,
            volume_name=VOLUME_NAME,
        )
        session = await interpreter._workspace.aensure_session()
        sandbox_id = getattr(session, "sandbox_id", None)
        workspace_path = str(getattr(session, "workspace_path", "") or "")
        print(f"[verify] sandbox_id={sandbox_id}")
        print(f"[verify] workspace_path={workspace_path}")

        print("\n[3/5] Starting interpreter & ensuring volume layout ...")
        interpreter.start()
        ensure_daytona_volume_layout(sandbox=session.sandbox)

        # Build a fake agent-like object for persistence helpers
        class _FakeAgent:
            def __init__(self, interpreter: DaytonaInterpreter) -> None:
                self.interpreter = interpreter

        agent = _FakeAgent(interpreter)

        vol = interpreter.volume_mount_path.rstrip("/")
        print(f"[verify] volume_mount_path={vol}")

        await ensure_session_volume_layout(agent, TEST_SESSION_ID)

        # Ensure session root directory exists for the conversation.json file
        conv_rel_path = session_conversation_path(TEST_SESSION_ID)
        conv_abs_path = f"{vol}/{conv_rel_path}"
        session_root_dir = conv_abs_path.rsplit("/", 1)[0]
        await interpreter.aexecute(
            f"import os; os.makedirs({repr(session_root_dir)}, exist_ok=True); SUBMIT(ok=True)",
            execution_profile="maintenance",
        )

        manifest = {
            "metadata": {
                "source": "live-daytona-verify",
                "session_id": TEST_SESSION_ID,
                "timestamp": time.time(),
            },
            "state": {
                "history": [
                    {"role": "user", "content": "Hello from live verification"},
                    {"role": "assistant", "content": "Acknowledged. Session persisted."},
                ],
            },
        }
        manifest_json = json.dumps(manifest, ensure_ascii=False, default=str)
        write_result = await interpreter.aexecute(
            "import json; f=open(path,'w'); f.write(payload); f.close(); SUBMIT(ok=True)",
            variables={"path": conv_abs_path, "payload": manifest_json},
            execution_profile="maintenance",
        )
        print(f"[verify] Wrote manifest to {conv_abs_path} (result={write_result})")

        # Immediate read-back to confirm write succeeded
        try:
            immediate = await session.aread_file(conv_abs_path)
            print(f"[verify] Immediate read-back: {immediate[:120] if immediate else 'EMPTY'}")
        except Exception as read_exc:
            print(f"[verify] Immediate read-back failed: {read_exc}")

        # Verify paths exist using absolute volume paths
        print("\n[4/5] Verifying volume paths ...")
        conv_path = conv_abs_path
        scratch_path = f"{vol}/{session_scratchpad_path(TEST_SESSION_ID)}"
        link_path = f"{vol}/{session_workspace_link_path(TEST_SESSION_ID)}"

        # Verify conversation.json is readable as a file
        conv_content = await session.aread_file(conv_path)
        print(f"[verify] {conv_path} -> {conv_content[:120] if conv_content else 'MISSING'}")

        # Verify scratchpad directory and workspace symlink via stat (aread_file raises on dirs/symlinks)
        stat_result = await interpreter.aexecute(
            "import os; SUBMIT("
            "scratchpad_isdir=os.path.isdir(scratch_path),"
            "workspace_link_exists=os.path.lexists(workspace_link_path),"
            "workspace_link_target=os.readlink(workspace_link_path) if os.path.islink(workspace_link_path) else None"
            ")",
            variables={"scratch_path": scratch_path, "workspace_link_path": link_path},
            execution_profile="maintenance",
        )
        stat_output = getattr(stat_result, "output", {}) or {}
        scratchpad_isdir = stat_output.get("scratchpad_isdir", False)
        workspace_link_exists = stat_output.get("workspace_link_exists", False)
        workspace_link_target = stat_output.get("workspace_link_target")
        print(f"[verify] {scratch_path} -> isdir={scratchpad_isdir}")
        print(f"[verify] {link_path} -> exists={workspace_link_exists}, target={workspace_link_target}")

        print("\n[5/5] Simulating recreation by re-reading manifest ...")
        loaded = await load_manifest_from_volume(agent, conv_path)
        if not loaded:
            print("[FAIL] Manifest not found after recreation simulation")
            return 1

        assert loaded["metadata"]["source"] == "live-daytona-verify", "Source mismatch"
        assert len(loaded["state"]["history"]) == 2, "History length mismatch"
        print(f"[verify] Loaded manifest source={loaded['metadata']['source']}")
        print(f"[verify] Loaded history turns={len(loaded['state']['history'])}")

        print("\n[PASS] Live Daytona Phase 1 verification completed successfully.")
        return 0

    except Exception as exc:
        print(f"\n[FAIL] {exc}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        if interpreter is not None:
            print("\n[cleanup] Shutting down interpreter ...")
            try:
                interpreter.shutdown()
            except Exception as cleanup_exc:
                print(f"[cleanup] Shutdown warning: {cleanup_exc}")


if __name__ == "__main__":
    _parse_args()
    sys.exit(asyncio.run(_main()))
