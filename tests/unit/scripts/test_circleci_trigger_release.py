"""Contracts for the CircleCI-to-GitHub release bridge."""

from __future__ import annotations

import pytest

from scripts import circleci_trigger_release as trigger


@pytest.mark.parametrize("dispatch_status", [200, 204])
def test_main_accepts_documented_dispatch_success_statuses(
    monkeypatch: pytest.MonkeyPatch,
    dispatch_status: int,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_request(
        url: str,
        *,
        headers: dict[str, str],
        method: str = "GET",
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        captured.append(
            {
                "url": url,
                "headers": headers,
                "method": method,
                "payload": payload,
            }
        )
        return dispatch_status, {}

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(trigger, "_request_json", fake_request)
    monkeypatch.setattr(
        trigger,
        "_find_run",
        lambda **_kwargs: {"id": 1, "html_url": "https://github.com/Qredence/fleet-rlm/actions/runs/1"},
    )
    monkeypatch.setattr(trigger, "_wait_for_run", lambda **_kwargs: None)

    assert (
        trigger.main(
            [
                "--version",
                "0.7.3",
                "--repository",
                "Qredence/fleet-rlm",
                "--ref",
                "main",
            ]
        )
        == 0
    )
    assert captured[0]["url"] == (
        "https://api.github.com/repos/Qredence/fleet-rlm/actions/workflows/release.yml/dispatches"
    )
    assert captured[0]["method"] == "POST"
    assert captured[0]["payload"] == {"ref": "main", "inputs": {"version": "0.7.3"}}
