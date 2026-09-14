from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def certification():
    path = Path(__file__).parents[3] / "scripts" / "benchmarks" / "certify_mlflow.py"
    spec = importlib.util.spec_from_file_location("certify_mlflow", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_uri_label_does_not_retain_backend_identity(certification) -> None:
    assert certification._safe_uri_label("http://127.0.0.1:5001") == "local_http"
    assert certification._safe_uri_label("https://managed.example") == "https_backend"
    assert certification._safe_uri_label("databricks") == "managed_databricks"


def test_backend_version_accepts_mlflow_plain_text_response(certification, monkeypatch) -> None:
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"3.16.0"

    monkeypatch.setattr(certification.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    assert certification._backend_version("http://127.0.0.1:5001") == "3.16.0"


def test_write_once_refuses_replacement(certification, tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    digest = certification._write_once(path, {"schema": "fleet.mlflow-certification/v1"})
    assert len(digest) == 64
    with pytest.raises(certification.CertificationError, match="already exists"):
        certification._write_once(path, {"schema": "different"})


def test_trace_linkage_checks_tags_trace_ids_and_parent_relationships(certification) -> None:
    session_id = str(uuid4())
    run_id = str(uuid4())
    trace_id = "tr-certification"
    root = SimpleNamespace(name="fleet_turn", span_id="root", parent_id=None, trace_id=trace_id)
    children = [
        SimpleNamespace(name="certification_child", span_id="child-1", parent_id="root", trace_id=trace_id),
        SimpleNamespace(name="certification_child", span_id="child-2", parent_id="root", trace_id=trace_id),
    ]
    trace = SimpleNamespace(
        info=SimpleNamespace(
            trace_id=trace_id,
            tags={
                "fleet.session_id": session_id,
                "fleet.run_id": run_id,
                "fleet.trace_phase": "execution",
            },
        ),
        data=SimpleNamespace(spans=[root, *children]),
    )

    assert all(
        certification._trace_linkage(
            trace,
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
        ).values()
    )

    children[1].parent_id = "unrelated-root"
    checks = certification._trace_linkage(
        trace,
        session_id=session_id,
        run_id=run_id,
        trace_id=trace_id,
    )
    assert checks["child_spans_linked"] is False


@pytest.mark.asyncio
async def test_run_trace_fails_closed_without_current_handle_identity(certification, monkeypatch) -> None:
    @contextmanager
    def no_trace(*_args, **_kwargs):
        yield SimpleNamespace(trace_id=None)

    @contextmanager
    def no_phase(*_args, **_kwargs):
        yield None

    class _Proxy:
        def __init__(self, *_args, **_kwargs):
            pass

        async def acall(self, *_args, **_kwargs):
            return "ok"

    monkeypatch.setattr(certification, "turn_trace", no_trace)
    monkeypatch.setattr(certification, "turn_phase_span", no_phase)
    monkeypatch.setattr(certification, "DeadlineLMProxy", _Proxy)
    monkeypatch.setattr(certification, "annotate_trace_io", lambda **_kwargs: None)
    monkeypatch.setattr(certification, "flush_tracing", lambda: None)

    import mlflow

    monkeypatch.setattr(
        mlflow,
        "get_last_active_trace_id",
        lambda: pytest.fail("stale last-active trace must not be used"),
    )
    with pytest.raises(certification.CertificationError, match="trace identity"):
        await certification._run_trace()


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}])
def test_token_aggregation_rejects_missing_or_double_counted_usage(certification, usage):
    assert not certification._expected_token_usage(usage)


def test_token_aggregation_requires_exact_observed_counts(certification):
    assert certification._expected_token_usage({"input_tokens": 6, "output_tokens": 4, "total_tokens": 10})
    assert not certification._expected_token_usage({"input_tokens": 6.0, "output_tokens": 4, "total_tokens": 10})


def _fault_report(certification):
    root = certification.ET.Element("testsuites")
    suite = certification.ET.SubElement(root, "testsuite")
    for nodes in certification._FAULT_TESTS.values():
        for node in nodes:
            path, _, name = node.partition("::")
            certification.ET.SubElement(suite, "testcase", file=path, name=name or "test_api_contract")
    return root


def test_fault_proof_requires_every_selected_test_to_execute(certification):
    report = _fault_report(certification)
    assert all(v["status"] == "passed" for v in certification._fault_results(report, 0).values())
    suite = report.find("testsuite")
    assert suite is not None
    suite.remove(next(iter(suite)))
    assert certification._fault_results(report, 0)["expired_credentials"]["status"] == "failed"


@pytest.mark.parametrize("result", ["failure", "error", "skipped"])
def test_fault_proof_rejects_failure_error_and_skip(certification, result):
    report = _fault_report(certification)
    case = next(report.iter("testcase"))
    certification.ET.SubElement(case, result)
    assert certification._fault_results(report, 0)["expired_credentials"]["status"] == "failed"


def test_fault_proof_rejects_nonzero_exit_even_with_passing_xml(certification):
    assert all(v["status"] == "failed" for v in certification._fault_results(_fault_report(certification), 1).values())


def test_sampling_checks_fail_closed_on_timeout(certification, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise certification.subprocess.TimeoutExpired("probe", 90)

    monkeypatch.setattr(certification.subprocess, "run", timeout)
    args = certification.build_parser().parse_args(["--output", "unused.json"])
    settings = SimpleNamespace(mlflow_tracking_uri="http://127.0.0.1:5001", mlflow_experiment_name="test")
    result = certification._run_sampling_checks(args, settings)
    assert result["status"] == "failed"
    assert len(result["probes"]) == 2
    assert all(probe["error_category"] == "TimeoutExpired" for probe in result["probes"])


@pytest.mark.parametrize("passed,exit_code", [(True, 0), (False, 2)])
def test_main_retains_receipt_and_reports_incomplete_checks(certification, monkeypatch, tmp_path, passed, exit_code):
    monkeypatch.setattr(
        certification,
        "run",
        lambda _args: {
            "certification": {"passed": passed},
            "promotion": {"eligible": False, "reason": "dirty_candidate"},
        },
    )
    output = tmp_path / "proof.json"
    assert certification.main(["--output", str(output)]) == exit_code
    proof = certification.json.loads(output.read_text())
    assert proof["certification"]["passed"] is passed
    assert proof["promotion"]["eligible"] is False


@pytest.mark.parametrize("dirty", [False, True])
def test_git_identity_uses_source_repository_from_other_directory(certification, monkeypatch, tmp_path, dirty):
    monkeypatch.chdir(tmp_path)
    calls = []

    def git(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == certification._REPO_ROOT
        assert kwargs["check"] is True
        output = "a" * 40 if command[1] == "rev-parse" else " M config/fleet.toml\n" if dirty else ""
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(certification.subprocess, "run", git)
    assert certification._git_identity() == {"revision": "a" * 40, "dirty": dirty}
    assert calls == [["git", "rev-parse", "HEAD"], ["git", "status", "--porcelain"]]


@pytest.mark.parametrize(
    ("initial", "final", "stable"),
    [
        ({"revision": "a", "dirty": False}, {"revision": "a", "dirty": False}, True),
        ({"revision": "a", "dirty": False}, {"revision": "b", "dirty": False}, False),
        ({"revision": "a", "dirty": True}, {"revision": "a", "dirty": False}, False),
        ({"revision": "a", "dirty": False}, {"revision": "a", "dirty": True}, False),
    ],
)
def test_candidate_stability_requires_same_clean_revision(certification, initial, final, stable) -> None:
    assert certification._candidate_is_stable(initial, final) is stable
