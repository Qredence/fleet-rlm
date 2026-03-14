from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fleet_rlm.daytona_rlm.driver import DAYTONA_DRIVER_SOURCE
from fleet_rlm.daytona_rlm.protocol import (
    DriverReady,
    ExecutionRequest,
    ExecutionResponse,
    HostCallbackResponse,
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


def _start_driver(
    tmp_path: Path, readme: str = "# Example\n"
) -> tuple[subprocess.Popen[str], Path]:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "README.md").write_text(readme, encoding="utf-8")

    driver_path = tmp_path / "driver.py"
    driver_path.write_text(DAYTONA_DRIVER_SOURCE, encoding="utf-8")

    process = subprocess.Popen(
        [sys.executable, "-u", str(driver_path), str(repo_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = _read_frame(process)
    assert ready["type"] == DriverReady().type
    return process, repo_path


def _execute(
    process: subprocess.Popen[str], request_id: str, code: str
) -> ExecutionResponse:
    assert process.stdin is not None
    process.stdin.write(
        encode_frame(
            ExecutionRequest(
                request_id=request_id,
                code=code,
            ).to_dict()
        )
        + "\n"
    )
    process.stdin.flush()
    return ExecutionResponse.from_dict(_read_frame(process))


def _write_frame(process: subprocess.Popen[str], payload: dict[str, object]) -> None:
    assert process.stdin is not None
    process.stdin.write(encode_frame(payload) + "\n")
    process.stdin.flush()


def _shutdown(process: subprocess.Popen[str]) -> None:
    assert process.stdin is not None
    process.stdin.write(encode_frame(ShutdownRequest().to_dict()) + "\n")
    process.stdin.flush()
    _ = _read_frame(process)


def test_daytona_driver_accepts_prefixed_host_frames(tmp_path: Path):
    process, _repo_path = _start_driver(tmp_path)
    try:
        response = _execute(
            process,
            "req-1",
            "counter = 2\ncounter += 3\nSUBMIT(counter)",
        )
        assert response.error is None
        assert response.final_artifact is not None
        assert response.final_artifact["value"] == 5
        assert response.final_artifact["finalization_mode"] == "SUBMIT"

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_daytona_driver_structured_helpers_execute_inside_protocol(tmp_path: Path):
    process, _repo_path = _start_driver(
        tmp_path,
        readme="# Example\nIntro line\n## Details\nExample details\n",
    )
    try:
        response = _execute(
            process,
            "req-helpers",
            'slice_result = read_file_slice("README.md", start_line=1, num_lines=2)\n'
            'grep_result = grep_repo("Example", include="*.md")\n'
            'chunk_result = chunk_file("README.md", strategy="headers")\n'
            "summary = {\n"
            '    "first_line": slice_result["lines"][0]["text"],\n'
            '    "grep_count": grep_result["count"],\n'
            '    "chunk_count": chunk_result["chunk_count"],\n'
            "}\n"
            "SUBMIT(output=summary)",
        )
        assert response.error is None
        assert response.final_artifact is not None
        assert response.final_artifact["value"] == {
            "first_line": "# Example",
            "grep_count": 2,
            "chunk_count": 2,
        }
        assert response.callback_count == 0

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_daytona_driver_prompt_helpers_persist_across_successive_executions(
    tmp_path: Path,
):
    process, _repo_path = _start_driver(tmp_path)
    try:
        first = _execute(
            process,
            "req-prompt-store",
            'handle = store_prompt("Alpha line\\nBeta line\\nGamma line", kind="task", label="long-task")\n'
            "manifest = list_prompts()\n"
            'stored_handle_id = handle["handle_id"]\n'
            "SUBMIT(output={"
            '"handle_id": stored_handle_id, '
            '"manifest_count": manifest["count"], '
            '"preview": handle["preview"]'
            "})",
        )
        assert first.error is None
        assert first.final_artifact is not None
        first_value = first.final_artifact["value"]
        assert first_value["manifest_count"] == 1
        assert first_value["preview"] == "Alpha line Beta line Gamma line"

        second = _execute(
            process,
            "req-prompt-read",
            "slice_result = read_prompt_slice(stored_handle_id, start_line=2, num_lines=2)\n"
            'summary = {"handle_id": slice_result["handle_id"], "text": slice_result["text"], "start_line": slice_result["start_line"], "end_line": slice_result["end_line"]}\n'
            'FINAL_VAR("summary")',
        )
        assert second.error is None
        assert second.final_artifact is not None
        assert second.final_artifact["value"] == {
            "handle_id": first_value["handle_id"],
            "text": "Beta line\nGamma line",
            "start_line": 2,
            "end_line": 3,
        }

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_daytona_driver_supports_typed_submit_fields(tmp_path: Path):
    process, _repo_path = _start_driver(tmp_path)
    try:
        _write_frame(
            process,
            ExecutionRequest(
                request_id="req-submit-schema",
                code='SUBMIT(summary="Readable summary", final_markdown="## Heading\\nMore detail")',
                submit_schema=[
                    {"name": "summary", "type": "str | None"},
                    {"name": "final_markdown", "type": "str | None"},
                    {"name": "output", "type": "object"},
                ],
            ).to_dict(),
        )

        response = ExecutionResponse.from_dict(_read_frame(process))
        assert response.error is None
        assert response.final_artifact is not None
        assert response.final_artifact["finalization_mode"] == "SUBMIT"
        assert response.final_artifact["value"] == {
            "summary": "Readable summary",
            "final_markdown": "## Heading\nMore detail",
        }

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_daytona_driver_emits_structured_single_recursive_task_specs(tmp_path: Path):
    process, _repo_path = _start_driver(tmp_path)
    try:
        _write_frame(
            process,
            ExecutionRequest(
                request_id="req-single-task",
                code=(
                    'child = llm_query({"task": "inspect README", "label": "README child", '
                    '"source": {"kind": "file_slice", "path": "README.md", "start_line": 1, '
                    '"end_line": 2, "preview": "# Example Intro line"}})\n'
                    "SUBMIT(child)"
                ),
            ).to_dict(),
        )

        callback_request = _read_frame(process)
        assert callback_request["type"] == "host_callback_request"
        assert callback_request["name"] == "llm_query"
        assert callback_request["payload"] == {
            "task": {
                "task": "inspect README",
                "label": "README child",
                "source": {
                    "kind": "file_slice",
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 2,
                    "preview": "# Example Intro line",
                },
            }
        }

        _write_frame(
            process,
            HostCallbackResponse(
                callback_id=str(callback_request["callback_id"]),
                ok=True,
                value="child summary",
            ).to_dict(),
        )
        response = ExecutionResponse.from_dict(_read_frame(process))
        assert response.error is None
        assert response.final_artifact is not None
        assert response.final_artifact["value"] == "child summary"
        assert response.final_artifact["finalization_mode"] == "SUBMIT"

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_daytona_driver_emits_structured_batched_recursive_task_specs(tmp_path: Path):
    process, _repo_path = _start_driver(tmp_path)
    try:
        _write_frame(
            process,
            ExecutionRequest(
                request_id="req-batched-tasks",
                code=(
                    "tasks = [\n"
                    '    {"task": "inspect README intro", "source": {"kind": "file_slice", "path": "README.md", "start_line": 1, "end_line": 2, "preview": "# Example Intro line"}},\n'
                    '    {"task": "inspect README details", "source": {"kind": "file_slice", "path": "README.md", "start_line": 3, "end_line": 4, "preview": "## Details Example details"}},\n'
                    "]\n"
                    "results = llm_query_batched(tasks)\n"
                    "SUBMIT(results)"
                ),
            ).to_dict(),
        )

        callback_request = _read_frame(process)
        assert callback_request["type"] == "host_callback_request"
        assert callback_request["name"] == "llm_query_batched"
        assert callback_request["payload"] == {
            "tasks": [
                {
                    "task": "inspect README intro",
                    "source": {
                        "kind": "file_slice",
                        "path": "README.md",
                        "start_line": 1,
                        "end_line": 2,
                        "preview": "# Example Intro line",
                    },
                },
                {
                    "task": "inspect README details",
                    "source": {
                        "kind": "file_slice",
                        "path": "README.md",
                        "start_line": 3,
                        "end_line": 4,
                        "preview": "## Details Example details",
                    },
                },
            ]
        }

        _write_frame(
            process,
            HostCallbackResponse(
                callback_id=str(callback_request["callback_id"]),
                ok=True,
                value=["intro summary", "details summary"],
            ).to_dict(),
        )
        response = ExecutionResponse.from_dict(_read_frame(process))
        assert response.error is None
        assert response.final_artifact is not None
        assert response.final_artifact["value"] == [
            "intro summary",
            "details summary",
        ]
        assert response.final_artifact["finalization_mode"] == "SUBMIT"

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_daytona_driver_routes_rlm_query_to_recursive_callback_name(tmp_path: Path):
    process, _repo_path = _start_driver(tmp_path)
    try:
        _write_frame(
            process,
            ExecutionRequest(
                request_id="req-rlm-query",
                code=(
                    'child = rlm_query({"task": "inspect README", "label": "README child"})\n'
                    "SUBMIT(child)"
                ),
            ).to_dict(),
        )

        callback_request = _read_frame(process)
        assert callback_request["type"] == "host_callback_request"
        assert callback_request["name"] == "rlm_query"
        assert callback_request["payload"] == {
            "task": {
                "task": "inspect README",
                "label": "README child",
            }
        }

        _write_frame(
            process,
            HostCallbackResponse(
                callback_id=str(callback_request["callback_id"]),
                ok=True,
                value="recursive child summary",
            ).to_dict(),
        )
        response = ExecutionResponse.from_dict(_read_frame(process))
        assert response.error is None
        assert response.final_artifact is not None
        assert response.final_artifact["value"] == "recursive child summary"

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_daytona_driver_routes_rlm_query_batched_to_recursive_callback_name(
    tmp_path: Path,
):
    process, _repo_path = _start_driver(tmp_path)
    try:
        _write_frame(
            process,
            ExecutionRequest(
                request_id="req-rlm-query-batched",
                code=(
                    "results = rlm_query_batched([\n"
                    '    {"task": "inspect README"},\n'
                    '    {"task": "inspect pyproject"},\n'
                    "])\n"
                    "SUBMIT(results)"
                ),
            ).to_dict(),
        )

        callback_request = _read_frame(process)
        assert callback_request["type"] == "host_callback_request"
        assert callback_request["name"] == "rlm_query_batched"
        assert callback_request["payload"] == {
            "tasks": [
                {"task": "inspect README"},
                {"task": "inspect pyproject"},
            ]
        }

        _write_frame(
            process,
            HostCallbackResponse(
                callback_id=str(callback_request["callback_id"]),
                ok=True,
                value=["recursive README", "recursive pyproject"],
            ).to_dict(),
        )
        response = ExecutionResponse.from_dict(_read_frame(process))
        assert response.error is None
        assert response.final_artifact is not None
        assert response.final_artifact["value"] == [
            "recursive README",
            "recursive pyproject",
        ]

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)


def test_daytona_driver_preserves_helper_state_across_successive_executions(
    tmp_path: Path,
):
    process, _repo_path = _start_driver(tmp_path)
    try:
        first = _execute(
            process,
            "req-1",
            'slice_result = read_file_slice("README.md", start_line=1, num_lines=1)\n'
            'stored_line = slice_result["lines"][0]["text"]',
        )
        assert first.error is None
        assert first.final_artifact is None

        second = _execute(
            process,
            "req-2",
            'summary = {"stored_line": stored_line, "chunk_count": len(chunk_text(read_file("README.md"), strategy="size", size=4))}\n'
            'FINAL_VAR("summary")',
        )
        assert second.error is None
        assert second.final_artifact is not None
        assert second.final_artifact["value"] == {
            "stored_line": "# Example",
            "chunk_count": 3,
        }

        _shutdown(process)
    finally:
        process.kill()
        process.wait(timeout=5)
