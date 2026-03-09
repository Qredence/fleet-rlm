from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fleet_rlm.daytona_rlm.driver import DAYTONA_DRIVER_SOURCE
from fleet_rlm.daytona_rlm.protocol import (
    DriverReady,
    ExecutionRequest,
    ExecutionResponse,
    ShutdownRequest,
    decode_frame,
    encode_frame,
)


def _read_frame(process: subprocess.Popen[str]) -> dict[str, object]:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line, "expected a protocol frame from the sandbox driver"
    frame = decode_frame(line.strip())
    assert frame is not None, f"expected framed payload, got: {line!r}"
    return frame


def test_daytona_driver_accepts_prefixed_host_frames(tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "README.md").write_text("# Example\n", encoding="utf-8")

    driver_path = tmp_path / "driver.py"
    driver_path.write_text(DAYTONA_DRIVER_SOURCE, encoding="utf-8")

    process = subprocess.Popen(
        [sys.executable, "-u", str(driver_path), str(repo_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = _read_frame(process)
        assert ready["type"] == DriverReady().type

        assert process.stdin is not None
        process.stdin.write(
            encode_frame(
                ExecutionRequest(
                    request_id="req-1",
                    code='counter = 2\ncounter += 3\nFINAL_VAR("counter")',
                ).to_dict()
            )
            + "\n"
        )
        process.stdin.flush()

        response = ExecutionResponse.from_dict(_read_frame(process))
        assert response.error is None
        assert response.final_artifact is not None
        assert response.final_artifact["value"] == 5

        process.stdin.write(encode_frame(ShutdownRequest().to_dict()) + "\n")
        process.stdin.flush()
        _ = _read_frame(process)
    finally:
        process.kill()
        process.wait(timeout=5)
