from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "deployment_observability.py"
    spec = importlib.util.spec_from_file_location("deployment_observability", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_summary_includes_release_and_observability_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    parser = module._build_parser()
    args = parser.parse_args(
        [
            "--environment",
            "pypi",
            "--package-url",
            "https://pypi.org/project/fleet-rlm/0.5.0/",
            "--release-tag",
            "v0.5.0",
            "--smoke-check",
            "Installed wheel smoke test passed",
        ]
    )

    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Qredence/fleet-rlm")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv(
        "DEPLOYMENT_METRICS_URL",
        "https://grafana.example.com/d/fleet/overview",
    )
    monkeypatch.setenv(
        "DEPLOYMENT_POSTHOG_DASHBOARD_URL",
        "https://eu.posthog.com/project/1/dashboard/42",
    )

    summary = module.build_summary(
        args=args,
        posthog_result=module.AnnotationResult(
            status="sent",
            detail="PostHog deploy marker emitted successfully.",
        ),
    )

    assert "## Deployment observability (pypi)" in summary
    assert "[Package](https://pypi.org/project/fleet-rlm/0.5.0/)" in summary
    assert "[GitHub release](https://github.com/Qredence/fleet-rlm/releases/tag/v0.5.0)" in summary
    assert "[Workflow run](https://github.com/Qredence/fleet-rlm/actions/runs/12345)" in summary
    assert "[Metrics dashboard](https://grafana.example.com/d/fleet/overview)" in summary
    assert "[PostHog dashboard](https://eu.posthog.com/project/1/dashboard/42)" in summary
    assert "PostHog deploy marker: **sent**" in summary
    assert "- Installed wheel smoke test passed" in summary


def test_build_summary_ignores_local_only_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    parser = module._build_parser()
    args = parser.parse_args(["--environment", "pypi"])

    monkeypatch.setenv("POSTHOG_HOST", "http://127.0.0.1:8000")
    monkeypatch.setenv("DEPLOYMENT_HEALTHCHECK_URL", "http://localhost:8765/health")

    summary = module.build_summary(args=args, posthog_result=None)

    assert "Configure one or more of `DEPLOYMENT_METRICS_URL`" in summary
    assert "localhost" not in summary


def test_emit_posthog_deploy_marker_sends_capture_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    captured: dict[str, object] = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            _ = (exc_type, exc, tb)

        def getcode(self) -> int:
            return 200

    def _fake_urlopen(request, timeout: int):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = request.data.decode("utf-8")
        return _Response()

    monkeypatch.setenv("POSTHOG_API_KEY", "test-api-key")
    monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.com")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Qredence/fleet-rlm")
    monkeypatch.setenv("GITHUB_RUN_ID", "98765")
    monkeypatch.setattr(module, "urlopen", _fake_urlopen)

    result = module.emit_posthog_deploy_marker(
        environment="pypi",
        package_name="fleet-rlm",
        package_url="https://pypi.org/project/fleet-rlm/0.5.0/",
        release_tag="v0.5.0",
        release_version="0.5.0",
    )

    assert result.status == "sent"
    assert captured["url"] == "https://eu.i.posthog.com/capture/"
    assert captured["timeout"] == 10
    assert "fleet_rlm_release_deployed" in str(captured["body"])
    assert "https://github.com/Qredence/fleet-rlm/actions/runs/98765" in str(captured["body"])
