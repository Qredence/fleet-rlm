from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.benchmarks import run_phase4_campaign
from scripts.benchmarks.phase4_api_server import _bounded_trial, _campaign_settings, _shape
from scripts.benchmarks.run_phase4_campaign import Phase4CampaignError, _validate_candidate_url


def test_campaign_trial_correlation_is_bounded_and_content_free() -> None:
    assert _bounded_trial("D-p4-suitable-01-1") == "D-p4-suitable-01-1"
    assert _bounded_trial("") is None
    assert _bounded_trial("bad value with spaces") is None
    assert _bounded_trial("x" * 129) is None


def test_candidate_api_url_is_restricted_to_a_loopback_origin() -> None:
    assert _validate_candidate_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert _validate_candidate_url("http://localhost:8123") == "http://localhost:8123"
    for value in (
        "https://127.0.0.1:8000",
        "http://example.test:8000",
        "http://127.0.0.1",
        "http://127.0.0.1:8000/api",
        "http://127.0.0.1:8000?x=1",
    ):
        with pytest.raises(Phase4CampaignError):
            _validate_candidate_url(value)


def test_shape_reads_the_selected_platform_resource_contract() -> None:
    resources = SimpleNamespace(
        platform=SimpleNamespace(
            spec_for_profile=lambda _profile: SimpleNamespace(cpu=4, memory_gib=8, disk_gib=8),
        )
    )

    assert _shape(resources, object()) == (4, 8, 8)


def test_shape_uses_default_spec_when_the_platform_call_omits_profile() -> None:
    resources = SimpleNamespace(
        platform=SimpleNamespace(
            spec_for_profile=lambda _profile: None,
        ),
        sandbox_spec=SimpleNamespace(cpu=4, memory_gib=8, disk_gib=8),
    )

    assert _shape(resources, None) == (4, 8, 8)


def test_shape_fails_closed_when_resource_shape_is_unavailable() -> None:
    assert _shape(SimpleNamespace(platform=SimpleNamespace()), object()) is None


def test_campaign_server_preserves_ordinary_policy_without_explicit_profile(tmp_path) -> None:
    settings = _campaign_settings(
        profile=None,
        recursive=True,
        data_root=tmp_path / "data",
        database_url="sqlite+aiosqlite:///tmp/fleet-partial.db",
        volume_name="fleet-partial-volume",
    )

    assert settings.root_llm_max_tokens == 16_384
    assert settings.sub_llm_max_tokens == 16_384
    assert settings.rlm_max_iters == 12
    assert settings.rlm_max_llm_calls == 32
    assert settings.database_url == "sqlite+aiosqlite:///tmp/fleet-partial.db"


def test_baseline_policy_overlay_selects_campaign_profile_without_mutating_candidate(
    monkeypatch,
    tmp_path,
) -> None:
    candidate = tmp_path / "candidate"
    candidate_config = candidate / "config"
    candidate_config.mkdir(parents=True)
    source = (
        "[config]\n"
        'default_profile = "daytona-recursive"\n\n'
        "[profiles.daytona-recursive]\n\n"
        "[profiles.phase4-campaign]\n"
    )
    (candidate_config / "fleet.toml").write_text(source, encoding="utf-8")

    baseline = tmp_path / "baseline"
    (baseline / "config").mkdir(parents=True)
    (baseline / "config" / "fleet.toml").write_text(source, encoding="utf-8")

    monkeypatch.setattr(run_phase4_campaign, "REPO_ROOT", candidate)
    run_phase4_campaign._install_baseline_policy_overlay(baseline)

    assert 'default_profile = "phase4-campaign"' in (baseline / "config" / "fleet.toml").read_text()
    assert (candidate / "config" / "fleet.toml").read_text() == source
