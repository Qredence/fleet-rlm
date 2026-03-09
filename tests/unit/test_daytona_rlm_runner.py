from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from fleet_rlm.daytona_rlm.diagnostics import DaytonaDiagnosticError
from fleet_rlm.daytona_rlm.protocol import HostCallbackRequest
from fleet_rlm.daytona_rlm.runner import DaytonaRLMRunner, run_daytona_rlm_pilot
from fleet_rlm.daytona_rlm.types import RolloutBudget


class _FakeSession:
    def __init__(self, repo_url: str, ref: str | None, sandbox_id: str):
        self.repo_url = repo_url
        self.ref = ref
        self.repo_path = "/workspace/repo"
        self.sandbox_id = sandbox_id
        self.deleted = False
        self.driver_started = False
        self.driver_closed = False
        self.execute_calls = 0
        self.files = {
            "/workspace/repo/README.md": b"# Example\n",
        }
        self.env: dict[str, object] = {
            "__builtins__": __builtins__,
            "json": json,
        }

    def start_driver(self, *, timeout: float = 30.0) -> None:
        del timeout
        self.driver_started = True

    def close_driver(self, *, timeout: float = 5.0) -> None:
        del timeout
        self.driver_closed = True

    def run(self, command: str, *, cwd: str | None = None):
        return {
            "exit_code": 0,
            "stdout": f"ran:{command}@{cwd or self.repo_path}",
            "stderr": "",
            "ok": True,
        }

    def read_file(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"{self.repo_path}/{path}"
        return self.files[path].decode("utf-8")

    def list_files(self, path: str = ".") -> list[str]:
        del path
        return sorted(self.files)

    def find_files(self, path: str = ".", pattern: str = "*") -> list[str]:
        del path, pattern
        return sorted(self.files)

    def execute_code(self, *, code: str, callback_handler, timeout: float):
        del timeout
        self.execute_calls += 1
        final_artifact: dict[str, object] | None = None
        callback_count = 0
        stdout = io.StringIO()
        stderr = io.StringIO()

        def FINAL(value: object) -> object:
            nonlocal final_artifact
            final_artifact = {
                "kind": "markdown",
                "value": value,
                "finalization_mode": "FINAL",
            }
            return value

        def FINAL_VAR(variable_name: str) -> object:
            nonlocal final_artifact
            value = self.env[variable_name]
            final_artifact = {
                "kind": "markdown",
                "value": value,
                "variable_name": variable_name,
                "finalization_mode": "FINAL_VAR",
            }
            return value

        def _handle_callback(name: str, payload: dict[str, object]):
            nonlocal callback_count
            callback_count += 1
            response = callback_handler(
                HostCallbackRequest(
                    callback_id=f"cb-{self.execute_calls}-{callback_count}",
                    name=name,
                    payload=payload,
                )
            )
            if not response.ok:
                raise RuntimeError(response.error or "callback failed")
            return response.value

        self.env.update(
            {
                "run": lambda command, cwd=None: self.run(command, cwd=cwd),
                "read_file": lambda path: self.read_file(path),
                "list_files": lambda path=".": self.list_files(path),
                "find_files": lambda path=".", pattern="*": self.find_files(
                    path, pattern
                ),
                "rlm_query": lambda task: _handle_callback("rlm_query", {"task": task}),
                "rlm_query_batched": lambda tasks: _handle_callback(
                    "rlm_query_batched",
                    {"tasks": list(tasks)},
                ),
                "FINAL": FINAL,
                "FINAL_VAR": FINAL_VAR,
            }
        )

        error_text: str | None = None
        try:
            exec(code, self.env, self.env)
        except Exception as exc:  # pragma: no cover - exercised via response
            error_text = f"{type(exc).__name__}: {exc}"

        return type(
            "_ExecResponse",
            (),
            {
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
                "error": error_text,
                "final_artifact": final_artifact,
                "duration_ms": 1,
                "callback_count": callback_count,
            },
        )()

    def delete(self) -> None:
        self.deleted = True


class _FakeRuntime:
    def __init__(self):
        self.sessions: list[_FakeSession] = []

    def create_repo_session(self, *, repo_url: str, ref: str | None):
        session = _FakeSession(
            repo_url=repo_url,
            ref=ref,
            sandbox_id=f"sbx-{len(self.sessions) + 1}",
        )
        self.sessions.append(session)
        return session


class _ScriptedLM:
    def __init__(self, scripts: dict[str, str]):
        self.scripts = scripts
        self.prompts: list[str] = []

    def __call__(self, prompt: str):
        self.prompts.append(prompt)
        for key, script in self.scripts.items():
            if f"Task: {key}" in prompt:
                return [{"text": script}]
        raise AssertionError(f"Unexpected prompt: {prompt}")


def _code(body: str) -> str:
    return f"```python\n{body}\n```"


def test_runner_supports_recursive_child_spawning_via_host_callbacks(tmp_path: Path):
    lm = _ScriptedLM(
        {
            "root task": _code(
                'child = rlm_query("child task")\nanswer = f"root got {child}"\nFINAL_VAR("answer")'
            ),
            "child task": _code('answer = "child answer"\nFINAL_VAR("answer")'),
        }
    )
    runtime = _FakeRuntime()
    runner = DaytonaRLMRunner(
        lm=lm,
        runtime=runtime,
        budget=RolloutBudget(),
        output_dir=tmp_path,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="root task")

    assert result.final_artifact is not None
    assert result.final_artifact.value == "root got child answer"
    assert len(result.nodes) == 2
    root = result.nodes[result.root_id]
    assert len(root.child_ids) == 1
    assert runtime.sessions[0].execute_calls == 1
    assert runtime.sessions[1].execute_calls == 1


def test_runner_keeps_batched_results_in_input_order(tmp_path: Path):
    lm = _ScriptedLM(
        {
            "batched task": _code(
                'tasks = ["slow child", "fast child", "medium child"]\n'
                "results = rlm_query_batched(tasks)\n"
                'FINAL_VAR("results")'
            ),
            "slow child": _code('answer = "slow"\nFINAL_VAR("answer")'),
            "fast child": _code('answer = "fast"\nFINAL_VAR("answer")'),
            "medium child": _code('answer = "medium"\nFINAL_VAR("answer")'),
        }
    )
    runner = DaytonaRLMRunner(
        lm=lm,
        runtime=_FakeRuntime(),
        budget=RolloutBudget(batch_concurrency=3),
        output_dir=tmp_path,
    )

    result = runner.run(
        repo="https://github.com/example/repo.git",
        task="batched task",
    )

    assert result.final_artifact is not None
    assert result.final_artifact.value == ["slow", "fast", "medium"]


def test_runner_enforces_max_depth(tmp_path: Path):
    lm = _ScriptedLM(
        {
            "depth task": _code('child = rlm_query("depth child")\nFINAL_VAR("child")'),
        }
    )
    runner = DaytonaRLMRunner(
        lm=lm,
        runtime=_FakeRuntime(),
        budget=RolloutBudget(max_depth=0),
        output_dir=tmp_path,
    )

    result = runner.run(
        repo="https://github.com/example/repo.git",
        task="depth task",
    )

    assert result.final_artifact is not None
    assert result.final_artifact.finalization_mode == "error"
    assert "max_depth=0" in str(result.final_artifact.value)


def test_runner_enforces_max_sandboxes():
    runner = DaytonaRLMRunner(
        lm=_ScriptedLM({"noop": _code('FINAL("done")')}),
        runtime=_FakeRuntime(),
        budget=RolloutBudget(max_sandboxes=1),
    )

    _ = runner._create_session(repo="https://github.com/example/repo.git", ref=None)
    with pytest.raises(RuntimeError, match="Sandbox budget exceeded"):
        runner._create_session(repo="https://github.com/example/repo.git", ref=None)


def test_runner_records_bootstrap_daytona_errors_on_node(tmp_path: Path):
    class _BrokenRuntime:
        def create_repo_session(self, *, repo_url: str, ref: str | None):
            del repo_url, ref
            raise DaytonaDiagnosticError(
                "Daytona repo clone failure: auth denied",
                category="sandbox_create_clone_error",
                phase="repo_clone",
            )

    runner = DaytonaRLMRunner(
        lm=_ScriptedLM({"noop": _code('FINAL("done")')}),
        runtime=_BrokenRuntime(),
        budget=RolloutBudget(),
        output_dir=tmp_path,
    )

    with pytest.raises(DaytonaDiagnosticError, match="auth denied"):
        runner.run(repo="https://github.com/example/repo.git", task="noop")

    assert len(runner._nodes) == 1
    root = next(iter(runner._nodes.values()))
    assert root.status == "error"
    assert root.error is not None
    assert root.error.startswith("sandbox_create_clone_error:")


def test_runner_enforces_global_timeout(tmp_path: Path):
    lm = _ScriptedLM(
        {
            "timeout task": _code('answer = "still running"\nFINAL_VAR("answer")'),
        }
    )
    runner = DaytonaRLMRunner(
        lm=lm,
        runtime=_FakeRuntime(),
        budget=RolloutBudget(global_timeout=0),
        output_dir=tmp_path,
    )

    with pytest.raises(TimeoutError, match="Global timeout exceeded"):
        runner.run(repo="https://github.com/example/repo.git", task="timeout task")


def test_runner_supports_final_var_completion(tmp_path: Path):
    lm = _ScriptedLM(
        {
            "final var task": _code('answer = {"status": "ok"}\nFINAL_VAR("answer")'),
        }
    )
    runner = DaytonaRLMRunner(
        lm=lm,
        runtime=_FakeRuntime(),
        budget=RolloutBudget(),
        output_dir=tmp_path,
    )

    result = runner.run(
        repo="https://github.com/example/repo.git",
        task="final var task",
    )

    assert result.final_artifact is not None
    assert result.final_artifact.variable_name == "answer"
    assert result.final_artifact.value == {"status": "ok"}
    assert result.final_artifact.finalization_mode == "FINAL_VAR"


def test_run_daytona_rlm_pilot_persists_json_tree_and_summary(tmp_path: Path):
    lm = _ScriptedLM(
        {
            "persist task": _code(
                'child = rlm_query("persist child")\nsummary = {"child": child}\nFINAL_VAR("summary")'
            ),
            "persist child": _code('answer = "ok"\nFINAL_VAR("answer")'),
        }
    )

    result = run_daytona_rlm_pilot(
        repo="https://github.com/example/repo.git",
        task="persist task",
        runtime=_FakeRuntime(),
        lm=lm,
        output_dir=tmp_path,
    )

    assert result.result_path is not None
    path = Path(result.result_path)
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["root_id"] == result.root_id
    assert len(payload["nodes"]) == 2
    assert payload["final_artifact"]["value"] == {"child": "ok"}
    assert payload["summary"]["sandboxes_used"] == 2
    root_payload = payload["nodes"][result.root_id]
    assert root_payload["iteration_count"] == 1
    assert root_payload["prompt_previews"]
    assert root_payload["response_previews"]
    assert root_payload["observations"][0]["callback_count"] == 1
