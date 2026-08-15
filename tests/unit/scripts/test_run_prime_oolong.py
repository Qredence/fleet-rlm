from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from scripts.benchmarks import prime_oolong_sidecar as sidecar
from scripts.benchmarks import run_prime_oolong as runner


def _environment_identity() -> dict[str, Any]:
    return {
        "owner": "primeintellect",
        "name": "oolong-rlm",
        "version": runner.PRIME_VERSION,
        "version_id": runner.PRIME_VERSION_ID,
        "hub_hash": runner.PRIME_HUB_HASH,
        "source_sha256": dict(runner.PRIME_SOURCE_SHA256),
    }


def _inspection_payload() -> dict[str, Any]:
    return {
        "kind": "directory",
        "version_id": runner.PRIME_VERSION_ID,
        "entries": [
            {
                "path": path,
                "is_directory": False,
                "content_hash": digest,
            }
            for path, digest in runner.PRIME_SOURCE_SHA256.items()
        ],
    }


def _row(example_id: str = "7") -> dict[str, Any]:
    question = f"question-{example_id}"
    context = f"context-{example_id}"
    return {
        "type": "example",
        "example_id": example_id,
        "question": question,
        "context": context,
        "answer": "['answer']",
        "answer_type": "ANSWER_TYPE.USER",
        "context_len": 131_072,
        "dataset": "trec_coarse",
        "question_sha256": runner.hashlib.sha256(question.encode()).hexdigest(),
        "context_sha256": runner.hashlib.sha256(context.encode()).hexdigest(),
    }


def test_pinned_prime_environment_identity_is_exact() -> None:
    assert runner.PRIME_REFERENCE == "primeintellect/oolong-rlm@0.1.11"
    assert runner.PRIME_HUB_HASH == "97d47526"
    assert runner.PRIME_VERSION_ID == "zixnre6tq4e4drk82nm2ebph"
    assert runner.PRIME_SOURCE_SHA256 == {
        "oolong_rlm.py": "eb915d4201e8dd2bdcbe8480e1761e1f0eb8978d1b97c35ab582f9d81f705c20",
        "pyproject.toml": "acf0483d63c1b23adfde6a8036f355f56799cac788b840f9a25e9ce4c4c2e06f",
        "README.md": "e4479f35a33660b87261cd9712e678e471f41519f00cf855f8d464f64026329c",
    }
    assert runner.DATASET_ARGS == {
        "subset": "synth",
        "split": "validation",
        "dataset_name": "trec_coarse",
        "context_len": 131_072,
        "filter_numerical": True,
        "shuffle": False,
        "include_env_tips": False,
        "prompt_in_context_file": False,
        "reward_mode": "oolong",
    }


def test_hub_inspection_accepts_only_pinned_version_and_hashes() -> None:
    identity = runner._validate_hub_inspection(
        _inspection_payload(),
        "0.1.11 (latest)  2026-06-01 19:59:07 UTC  97d47526014a5a713598bc36",
    )
    assert identity == _environment_identity()

    drifted = _inspection_payload()
    drifted["entries"][0]["content_hash"] = "0" * 64
    with pytest.raises(runner.PrimeOolongError, match="source hashes drifted"):
        runner._validate_hub_inspection(
            drifted,
            "0.1.11 (latest)  2026-06-01 19:59:07 UTC  97d47526014a5a713598bc36",
        )

    with pytest.raises(runner.PrimeOolongError, match="Hub hash drifted"):
        runner._validate_hub_inspection(_inspection_payload(), "0.1.11 (latest) deadbeef")


def test_inspection_uses_authenticated_prime_cli_without_network_in_unit_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run_command(command: list[str], **_kwargs: Any) -> Any:
        commands.append(command)
        if command[2] == "inspect":
            return SimpleNamespace(returncode=0, stdout=json.dumps(_inspection_payload()))
        return SimpleNamespace(
            returncode=0,
            stdout="0.1.11 (latest) 2026-06-01 97d47526014a5a713598bc36",
        )

    monkeypatch.setattr(runner, "_run_command", run_command)

    assert runner._inspect_prime_environment() == _environment_identity()
    assert commands == [
        ["prime", "env", "inspect", runner.PRIME_REFERENCE, "--output", "json", "--plain"],
        ["prime", "env", "version", "list", runner.PRIME_ENVIRONMENT, "--full-hashes", "--plain"],
    ]


def test_pulled_environment_rejects_source_drift(tmp_path: Path) -> None:
    for name in runner.PRIME_SOURCE_SHA256:
        (tmp_path / name).write_text("drifted", encoding="utf-8")
    metadata = tmp_path / ".prime" / ".env-metadata.json"
    metadata.parent.mkdir()
    metadata.write_text(
        json.dumps({"owner": "primeintellect", "name": "oolong-rlm", "version": "0.1.11"}),
        encoding="utf-8",
    )

    with pytest.raises(runner.PrimeOolongError, match="source hashes drifted"):
        runner._validate_pulled_environment(tmp_path)


def test_sidecar_command_is_isolated_and_does_not_use_project_environment(tmp_path: Path) -> None:
    assert runner._sidecar_command(tmp_path) == [
        "uv",
        "run",
        "--isolated",
        "--no-project",
        "--with",
        "oolong_rlm==0.1.11",
        "--index",
        runner.PRIME_SIMPLE_INDEX,
        "python",
        str(runner._SIDECAR_PATH),
    ]


def test_sidecar_cache_paths_are_on_the_ssd7_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "_SIDECAR_CACHE_ROOT", tmp_path / "prime-oolong-cache")
    monkeypatch.setenv("DATABRICKS_TOKEN", "must-not-cross-process")
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "must-not-cross-process")

    environment = runner._sidecar_environment()

    assert environment["HOME"] == str(tmp_path / "prime-oolong-cache" / "home")
    assert environment["TMPDIR"] == str(tmp_path / "prime-oolong-cache" / "tmp")
    assert environment["UV_CACHE_DIR"] == str(tmp_path / "prime-oolong-cache" / "uv")
    assert environment["XDG_CACHE_HOME"] == str(tmp_path / "prime-oolong-cache" / "xdg")
    assert environment["HF_HOME"] == str(tmp_path / "prime-oolong-cache" / "huggingface")
    assert environment["HF_DATASETS_CACHE"] == str(tmp_path / "prime-oolong-cache" / "huggingface" / "datasets")
    assert "DATABRICKS_TOKEN" not in environment
    assert "FLEET_DAYTONA_API_KEY" not in environment


def test_sidecar_runner_rejects_nonzero_and_malformed_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "_run_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="", stderr="secret"),
    )
    with pytest.raises(runner.PrimeOolongError, match="sidecar failed"):
        runner._invoke_sidecar(tmp_path, [{"op": "export", "limit": 1}])

    monkeypatch.setattr(
        runner,
        "_run_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="not-json\n", stderr=""),
    )
    with pytest.raises(runner.PrimeOolongError, match="malformed JSONL"):
        runner._invoke_sidecar(tmp_path, [{"op": "export", "limit": 1}])


def test_export_rejects_duplicate_or_tampered_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _row("1")
    second = _row("2")
    monkeypatch.setattr(
        runner,
        "_invoke_sidecar",
        lambda *_args, **_kwargs: [first, second, {"type": "export_complete", "count": 2}],
    )
    assert [row["example_id"] for row in runner._export_examples(tmp_path, limit=2)] == ["1", "2"]

    duplicate = dict(second, example_id="1")
    monkeypatch.setattr(
        runner,
        "_invoke_sidecar",
        lambda *_args, **_kwargs: [first, duplicate, {"type": "export_complete", "count": 2}],
    )
    with pytest.raises(runner.PrimeOolongError, match="duplicate"):
        runner._export_examples(tmp_path, limit=2)

    tampered = dict(first, context="changed")
    monkeypatch.setattr(
        runner,
        "_invoke_sidecar",
        lambda *_args, **_kwargs: [tampered, {"type": "export_complete", "count": 1}],
    )
    with pytest.raises(runner.PrimeOolongError, match="digest does not match"):
        runner._export_examples(tmp_path, limit=1)


def test_score_outputs_requires_exact_ids_and_bounded_scores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row("1"), _row("2")]
    monkeypatch.setattr(
        runner,
        "_invoke_sidecar",
        lambda *_args, **_kwargs: [
            {"type": "score", "request_id": "1", "score": 1.0},
            {"type": "score", "request_id": "2", "score": 0.5},
        ],
    )
    assert runner._score_outputs(tmp_path, rows, ["a", "b"]) == {"1": 1.0, "2": 0.5}

    monkeypatch.setattr(
        runner,
        "_invoke_sidecar",
        lambda *_args, **_kwargs: [{"type": "score", "request_id": "1", "score": 1.1}],
    )
    with pytest.raises(runner.PrimeOolongError, match="invalid"):
        runner._score_outputs(tmp_path, rows[:1], ["a"])


def test_sidecar_exports_via_load_environment_get_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}
    dataset = [
        {
            "example_id": 9,
            "prompt": [{"role": "user", "content": "Which label?"}],
            "answer": "['label']",
            "info": {
                "context": "long context",
                "answer_type": "ANSWER_TYPE.USER",
                "context_len": 131_072,
                "dataset": "trec_coarse",
            },
        }
    ]

    class Environment:
        def get_dataset(self, *, n: int) -> list[dict[str, Any]]:
            calls["limit"] = n
            return dataset

    def load_environment(**kwargs: Any) -> Environment:
        calls["args"] = kwargs
        return Environment()

    monkeypatch.setitem(sys.modules, "oolong_rlm", SimpleNamespace(load_environment=load_environment))

    exported = sidecar.export_examples(1)
    assert calls == {"args": sidecar.ENVIRONMENT_ARGS, "limit": 1}
    assert exported[0]["example_id"] == "9"
    assert exported[0]["question"] == "Which label?"
    assert exported[0]["context"] == "long context"
    assert len(exported[0]["context_sha256"]) == 64


def test_sidecar_scores_directly_with_oolong_rubric(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Rubric:
        def __init__(self, *, subset: str) -> None:
            captured["subset"] = subset

        def oolong_reward(self, state: Mapping[str, Any]) -> float:
            captured["state"] = state
            return 0.75

    monkeypatch.setitem(sys.modules, "oolong_rlm", SimpleNamespace(OolongRubric=Rubric))
    result = sidecar.score_response(
        {
            "request_id": "1",
            "answer": "['gold']",
            "answer_type": "ANSWER_TYPE.USER",
            "output": "gold",
        }
    )

    assert result == {"type": "score", "request_id": "1", "score": 0.75}
    assert captured == {
        "subset": "synth",
        "state": {
            "final_answer": "gold",
            "answer": "['gold']",
            "info": {"answer_type": "ANSWER_TYPE.USER"},
        },
    }


def test_trajectory_diagnostic_uses_dspy_evaluate_and_keeps_only_bounded_findings() -> None:
    diagnostics, mean_score = runner._evaluate_trajectory_diagnostics(
        ["1"],
        [
            {
                "codes": [
                    "matches = [line for line in context.splitlines() if 'label' in line]",
                    "sample = matches[:10]\nprint(len(matches), sample)",
                    "judgments = llm_query_batched([str(item) for item in sample])",
                ],
                "outputs": ["12 ['bounded']"],
            }
        ],
    )

    result = diagnostics["1"]
    assert result["score"] == 1.0
    assert mean_score == 100.0
    assert result["semantic_call_counts"] == {"llm_query": 0, "llm_query_batched": 1, "rlm_query": 0}
    assert all(value["passed"] is True for value in result["criteria"].values())
    assert "12 ['bounded']" not in json.dumps(result)


def test_termination_mode_requires_explicit_terminal_output_evidence() -> None:
    assert runner._termination_mode(typed_submit_observed=True, final_answer="answer") == "typed_submit"
    assert runner._termination_mode(typed_submit_observed=False, final_answer="answer") == (
        "native_extraction_fallback"
    )
    assert runner._termination_mode(typed_submit_observed=False, final_answer=None) == "text_fallback"


def test_sidecar_verifies_the_installed_prime_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "oolong_rlm.py"
    source.write_text("drifted", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "oolong_rlm", SimpleNamespace(__file__=str(source)))

    with pytest.raises(sidecar.SidecarProtocolError, match="source hash drifted"):
        sidecar._verify_installed_environment()


class _StreamResponse:
    def __init__(self, attachment_id: str) -> None:
        self.attachment_id = attachment_id

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self) -> AsyncIterator[str]:
        chunks = [
            {"type": "data-attachment", "data": {"attachment_id": self.attachment_id}},
            {
                "type": "data-usage",
                "data": {
                    "usage": {
                        "iterations": 3,
                        "duration_ms": 8,
                        "observed_lm_usage": {"provider-model": {"secret": "not public"}},
                    }
                },
            },
            {"type": "data-structured-result", "data": {"value": {"answer": "Answer: label"}}},
            {"type": "data-rlm-output", "data": {"output": "FINAL submitted"}},
            {"type": "finish", "finishReason": "stop"},
        ]
        for chunk in chunks:
            yield f"data: {json.dumps(chunk)}"
        yield "data: [DONE]"


class _StreamContext:
    def __init__(self, attachment_id: str) -> None:
        self.response = _StreamResponse(attachment_id)

    async def __aenter__(self) -> _StreamResponse:
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.turns: list[dict[str, Any]] = []
        self.attachment_id = "attachment-1"

    async def get(self, path: str) -> httpx.Response:
        assert path == "/api/settings"
        return httpx.Response(
            200,
            json={
                "active_profile": "daytona-bench",
                "scopes": [
                    {
                        "name": "daytona-bench",
                        "fields": [
                            {"path": "llm.root.model", "value": "model"},
                            {"path": "llm.sub.model", "value": "sub-model"},
                            {"path": "rlm.max_iters", "value": 20},
                        ],
                    }
                ],
            },
            request=httpx.Request("GET", "http://test"),
        )

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        if path == "/api/attachments":
            self.uploads.append(kwargs)
            return httpx.Response(
                201,
                json={"id": self.attachment_id},
                request=httpx.Request("POST", "http://test"),
            )
        assert path == "/api/sessions"
        return httpx.Response(
            201,
            json={"id": "session-1"},
            request=httpx.Request("POST", "http://test"),
        )

    def stream(self, method: str, path: str, **kwargs: Any) -> _StreamContext:
        self.turns.append({"method": method, "path": path, **kwargs})
        return _StreamContext(self.attachment_id)


@pytest.mark.asyncio
async def test_evaluate_uses_fleet_attachment_and_emits_sanitized_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client()

    class _ClientContext:
        async def __aenter__(self) -> _Client:
            return client

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(runner.httpx, "AsyncClient", lambda **_kwargs: _ClientContext())
    monkeypatch.setattr(runner, "_score_outputs", lambda *_args, **_kwargs: {"7": 1.0})
    args = runner.build_parser().parse_args(["--limit", "1", "--output", str(tmp_path / "receipt.json")])

    receipt = await runner.evaluate(
        args,
        environment_root=tmp_path,
        environment_identity=_environment_identity(),
        rows=[_row()],
    )

    assert len(client.uploads) == 1
    assert client.turns[0]["json"] == {
        "text": "question-7",
        "attachment_ids": ["attachment-1"],
        "skill_selections": [],
    }
    result = receipt["results"][0]
    assert result["score"] == 1.0
    assert result["termination_mode"] == "typed_submit"
    assert result["context_prepared"] is True
    assert result["context_accessed"] is True
    assert result["usage"] == {"iterations": 3, "duration_ms": 8}
    serialized = json.dumps(receipt)
    assert "long context" not in serialized
    assert "['answer']" not in serialized
    assert "provider-model" not in serialized
    assert receipt["aggregate"] == {
        "count": 1,
        "mean_score": 1.0,
        "error_rate": 0.0,
        "typed_completion_rate": 1.0,
        "prepared_rate": 1.0,
        "accessed_rate": 1.0,
        "iteration_ceiling_rate": 0.0,
    }
    assert receipt["model_roles"] == {"root": "model", "sub": "sub-model"}
    assert receipt["dspy"]["rlm_type"] == "dspy.predict.rlm.RLM"
    assert receipt["protocol"]["deterministic_rescore"] is True
    assert receipt["trajectory_rubric"]["source_sha256"] == runner.PRIME_RLM_RUBRIC_SHA256
    assert runner._mechanics_gate_passes(receipt) is True


@pytest.mark.asyncio
async def test_run_requires_enabled_toml_live_policy_before_prime_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def disabled_policy() -> None:
        raise runner.FleetConfigurationError("disabled")

    monkeypatch.setattr(runner, "require_live_execution", disabled_policy)
    monkeypatch.setattr(
        runner,
        "_prepared_prime_environment",
        lambda: pytest.fail("Prime environment must not be accessed without consent"),
    )
    args = runner.build_parser().parse_args(["--output", str(tmp_path / "receipt.json")])
    with pytest.raises(runner.PrimeOolongError, match="disabled or unavailable"):
        await runner._run(args)


def test_main_loads_dotenv_without_overriding_exports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, bool]] = []

    def load_dotenv(path: Path, *, override: bool) -> bool:
        calls.append((path, override))
        return True

    async def run(_args: Any) -> int:
        return 0

    monkeypatch.setattr(runner, "load_dotenv", load_dotenv)
    monkeypatch.setattr(runner, "_run", run)

    assert runner.main(["--output", str(tmp_path / "receipt.json")]) == 0
    assert calls == [(runner._REPO_ROOT / ".env", False)]
